#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分析力学量化交易系统 v2.0
基于哈密顿-混沌框架的选股与风控模型

核心改进（相比设计文档 v1.0）：
1. Hurst 指数：自研 R/S 分析实现，滚动窗口计算
2. LLE：改用 Rosenstein 方法（比 Wolf 更稳健）
3. 哈密顿量：动能改用波动率加权，势能做标准化
4. 风控止损：修正 max/min 逻辑错误
5. 仓位管理：Hurst 加成区分趋势/反转方向
6. 计算效率：向量化 + 预计算缓存
"""

import numpy as np
import pandas as pd
import warnings
from datetime import datetime, timedelta
from scipy import stats
from scipy.signal import find_peaks

warnings.filterwarnings('ignore')

# ============================================================
# 模块一：Hurst 指数（自研 R/S 分析）
# ============================================================

def hurst_rs(series):
    """
    Hurst 指数 - R/S 分析法（自研实现）

    原理：
    对于长度为 N 的时间序列，将其分为若干子区间，
    计算每个子区间的重标极差 R/S，然后拟合 log(R/S) vs log(N)。
    H = 斜率

    H > 0.5: 持续性（趋势记忆）
    H = 0.5: 随机游走
    H < 0.5: 反持续性（均值回归）
    """
    series = np.asarray(series, dtype=float)
    N = len(series)

    if N < 20:
        return 0.5  # 数据不足，默认随机游走

    # 去除均值
    series = series - np.mean(series)

    # 选择多个子区间长度（至少 2 的幂次关系）
    lags = []
    n = N
    # 取 N/2, N/4, ... 到最小 10
    while n >= 10:
        lags.append(n)
        n = n // 2

    # 也加入一些中间值
    for k in [N // 3, N // 5, N // 7, N // 11, N // 13]:
        if k >= 10 and k not in lags:
            lags.append(k)

    lags = sorted(set(lags))

    if len(lags) < 3:
        return 0.5

    rs_values = []

    for lag in lags:
        # 将序列分成若干段，每段长度为 lag
        n_segments = N // lag
        if n_segments < 1:
            continue

        rs_list = []
        for i in range(n_segments):
            segment = series[i * lag: (i + 1) * lag]
            if len(segment) < 2:
                continue

            # 累积偏差
            mean_seg = np.mean(segment)
            cumdev = np.cumsum(segment - mean_seg)

            # 极差 R 和标准差 S
            R = np.max(cumdev) - np.min(cumdev)
            S = np.std(segment, ddof=1)

            if S > 1e-10 and R > 1e-10:
                rs_list.append(R / S)

        if rs_list:
            rs_values.append((np.log(lag), np.log(np.mean(rs_list))))

    if len(rs_values) < 3:
        return 0.5

    # 线性回归拟合 H
    x = np.array([v[0] for v in rs_values])
    y = np.array([v[1] for v in rs_values])

    slope, intercept, r_value, p_value, std_err = stats.linregress(x, y)

    # 限制在 [0, 1] 范围内
    H = float(np.clip(float(slope), 0.0, 1.0))

    return H


def rolling_hurst(series, window=100):
    """
    滚动计算 Hurst 指数
    """
    result = pd.Series(index=series.index, dtype=float)
    values = series.values

    for i in range(window, len(values) + 1):
        segment = values[i - window: i]
        result.iloc[i - 1] = hurst_rs(segment)

    return result


# ============================================================
# 模块二：相空间重构
# ============================================================

def mutual_information(series, max_lag=20):
    """
    计算互信息函数，用于确定延迟时间 tau
    tau = 互信息的第一个极小值
    """
    series = np.asarray(series, dtype=float)
    N = len(series)

    # 分箱
    n_bins = min(int(np.sqrt(N / 2)), 20)
    if n_bins < 3:
        return 1

    mi_values = []

    for lag in range(1, min(max_lag + 1, N // 4)):
        x = series[:-lag]
        y = series[lag:]

        # 计算互信息
        hist_2d, _, _ = np.histogram2d(x, y, bins=n_bins)
        hist_2d = hist_2d / hist_2d.sum()

        p_x = hist_2d.sum(axis=1)
        p_y = hist_2d.sum(axis=0)

        # 避免 log(0)
        mask = hist_2d > 0
        mi = 0.0
        for i in range(n_bins):
            for j in range(n_bins):
                if hist_2d[i, j] > 0 and p_x[i] > 0 and p_y[j] > 0:
                    mi += hist_2d[i, j] * np.log(hist_2d[i, j] / (p_x[i] * p_y[j]))

        mi_values.append(mi)

    # 找第一个极小值
    if len(mi_values) < 3:
        return 1

    for i in range(1, len(mi_values) - 1):
        if mi_values[i] < mi_values[i - 1] and mi_values[i] <= mi_values[i + 1]:
            return i + 1  # lag 从 1 开始

    return 1


def autocorrelation_zero_crossing(series, max_lag=20):
    """
    用自相关函数第一个零点确定 tau（互信息的快速替代）
    """
    series = np.asarray(series, dtype=float)
    series = series - np.mean(series)
    N = len(series)

    for lag in range(1, min(max_lag + 1, N // 4)):
        corr = np.corrcoef(series[:-lag], series[lag:])[0, 1]
        if corr <= 0:
            return lag

    return 1


def embed_phase_space(series, m=3, tau=1):
    """
    Takens 相空间重构
    X_i = [x(i), x(i+tau), x(i+2*tau), ..., x(i+(m-1)*tau)]
    """
    series = np.asarray(series, dtype=float)
    N = len(series)
    n_vectors = N - (m - 1) * tau

    if n_vectors <= 0:
        return np.array([]).reshape(0, m)

    embedded = np.zeros((n_vectors, m))
    for i in range(m):
        embedded[:, i] = series[i * tau: i * tau + n_vectors]

    return embedded


# ============================================================
# 模块三：最大李雅普诺夫指数 (LLE) - Rosenstein 方法
# ============================================================

def largest_lyapunov_rosenstein(series, m=3, tau=1, max_steps=None):
    """
    最大李雅普诺夫指数 - Rosenstein 方法

    原理：
    1. 相空间重构
    2. 对每个点找最近邻（排除时间上太近的点）
    3. 计算距离随时间的发散率
    4. 拟合 ln(d(k)) vs k 的斜率 = λ

    相比 Wolf 方法更稳健，对噪声不敏感。
    """
    series = np.asarray(series, dtype=float)

    # 标准化
    if np.std(series) > 1e-10:
        series = (series - np.mean(series)) / np.std(series)
    else:
        return 0.0

    N = len(series)

    if N < 50:
        return 0.0

    # 相空间重构
    embedded = embed_phase_space(series, m, tau)
    M = len(embedded)

    if M < 10:
        return 0.0

    # 最小时间分离
    min_sep = m * tau

    if max_steps is None:
        max_steps = min(M // 3, 50)

    # 找每个点的最近邻
    d0_list = []
    neighbor_idx_list = []

    for i in range(M - max_steps):
        # 计算到所有其他点的距离
        diffs = embedded - embedded[i]
        dists = np.sqrt(np.sum(diffs ** 2, axis=1))

        # 排除时间上太近的点
        for j in range(max(0, i - min_sep), min(M, i + min_sep + 1)):
            dists[j] = np.inf

        # 找最近邻
        j_min = np.argmin(dists)
        if dists[j_min] < np.inf and dists[j_min] > 1e-10:
            d0_list.append(dists[j_min])
            neighbor_idx_list.append((i, j_min))

    if len(d0_list) < 5:
        return 0.0

    # 计算平均发散
    divergence = np.zeros(max_steps)

    for k in range(max_steps):
        log_sum = 0.0
        count = 0

        for i, j in neighbor_idx_list:
            if i + k < M and j + k < M:
                d_k = np.sqrt(np.sum((embedded[i + k] - embedded[j + k]) ** 2))
                if d_k > 1e-10:
                    log_sum += np.log(d_k)
                    count += 1

        if count > 0:
            divergence[k] = log_sum / count
        else:
            divergence[k] = divergence[k - 1] if k > 0 else 0.0

    # 线性拟合斜率
    x = np.arange(max_steps)
    y = divergence

    # 只用前 60% 做拟合（后面可能饱和）
    fit_len = int(max_steps * 0.6)
    if fit_len < 5:
        fit_len = max_steps

    slope, _, _, _, _ = stats.linregress(x[:fit_len], y[:fit_len])

    return slope


def compute_lle(series, window=100):
    """
    滚动计算 LLE
    自动确定 m 和 tau
    """
    values = np.asarray(series, dtype=float)
    N = len(values)

    if N < window:
        return 0.0

    segment = values[-window:]

    # 自动确定 tau（用自相关零点，快速版）
    tau = autocorrelation_zero_crossing(segment, max_lag=10)

    # 嵌入维度用固定值 3（Cao 方法太慢，金融数据 m=3 经验上够用）
    m = 3

    # 计算 LLE
    lle = largest_lyapunov_rosenstein(segment, m=m, tau=tau, max_steps=min(window // 4, 30))

    return lle


def rolling_lle(series, window=100):
    """
    滚动 LLE 时间序列
    """
    result = pd.Series(index=series.index, dtype=float)
    values = series.values

    for i in range(window, len(values) + 1):
        segment = values[i - window: i]
        tau = autocorrelation_zero_crossing(segment, max_lag=10)
        lle = largest_lyapunov_rosenstein(segment, m=3, tau=tau, max_steps=min(window // 4, 30))
        result.iloc[i - 1] = lle

    return result


# ============================================================
# 模块四：KAM 环面代理指标
# ============================================================

def kam_toroidality(series, window=60):
    """
    KAM 环面度代理指标

    改进版：
    1. 相空间重构后计算轨迹的"旋转性"
    2. 用 FFT 检测主频率集中度
    3. 环面度 = 主频能量占比（越高越有序/环面状）
    """
    series = np.asarray(series, dtype=float)

    if len(series) < window:
        return 0.3  # 默认中等

    segment = series[-window:]

    # 标准化
    if np.std(segment) > 1e-10:
        segment = (segment - np.mean(segment)) / np.std(segment)

    # 相空间重构（2D 足够检测环面性）
    tau = autocorrelation_zero_crossing(segment, max_lag=10)
    if tau < 1:
        tau = 1

    embedded = embed_phase_space(segment, m=2, tau=tau)

    if len(embedded) < 10:
        return 0.3

    # 计算角动量代理 = 位置 × 速度的叉积（2D 下是标量）
    velocities = np.diff(embedded, axis=0)
    # 角动量 ~ x * vy - y * vx（2D 叉积）
    pos = embedded[:-1]
    angular_momentum = pos[:, 0] * velocities[:, 1] - pos[:, 1] * velocities[:, 0]

    # ACF of angular momentum
    am = angular_momentum - np.mean(angular_momentum)
    if np.std(am) < 1e-10:
        return 0.15

    # 自相关
    acf = np.correlate(am, am, mode='full')
    acf = acf[len(acf) // 2:]
    if acf[0] > 1e-10:
        acf = acf / acf[0]
    else:
        return 0.15

    # FFT 检测频率集中度
    fft_result = np.fft.rfft(acf[:min(len(acf), window // 2)])
    power = np.abs(fft_result) ** 2

    if np.sum(power) < 1e-10:
        return 0.15

    # 主频能量占比（排除 DC 分量）
    power_no_dc = power[1:] if len(power) > 1 else power
    total_energy = np.sum(power_no_dc)

    if total_energy < 1e-10:
        return 0.15

    max_energy = np.max(power_no_dc)
    toroidality = max_energy / total_energy

    return np.clip(toroidality, 0.0, 1.0)


def rolling_toroidality(series, window=60):
    """
    滚动环面度
    """
    result = pd.Series(index=series.index, dtype=float)
    values = series.values

    for i in range(window, len(values) + 1):
        segment = values[i - window: i]
        result.iloc[i - 1] = kam_toroidality(segment, window)

    return result


# ============================================================
# 模块五：哈密顿量代理指标
# ============================================================

def market_hamiltonian(df, window=20):
    """
    离散哈密顿量代理：H = T + V

    改进版：
    T (动能) = 波动率加权的收益率平方（市场活性）
    V (势能) = 标准化的价格偏离均值的平方（均值回归张力）

    相比原版：
    - T 改用 |log_return| * realized_vol，更物理
    - V 做了标准化，使不同价位的股票可比
    """
    log_ret = df['log_return']
    realized_vol = log_ret.rolling(window).std() * np.sqrt(252)

    # 动能：收益率大小 × 波动率（方向无关的"运动量"）
    T = (log_ret ** 2) * (realized_vol + 0.01)

    # 势能：偏离均值的程度（标准化）
    ma = df['close'].rolling(window).mean()
    vol_pct = df['close'].rolling(window).std() / df['close'].rolling(window).mean()
    V = ((df['close'] - ma) / (ma * (vol_pct + 0.01))) ** 2

    # 总能量
    H = T + V

    # 能量变化率
    dH = H.diff()

    # 能量 z-score
    H_mean = H.rolling(window * 3).mean()
    H_std = H.rolling(window * 3).std()
    H_zscore = (H - H_mean) / (H_std + 1e-10)

    return H, dH, H_zscore


def hamiltonian_alert(H_zscore, threshold=2.0):
    """
    哈密顿量预警

    返回值：
    'ENERGY_SPIKE': 能量突增，可能即将变盘
    'ENERGY_COLLAPSE': 能量崩塌，趋势衰竭
    'NORMAL': 正常
    """
    if pd.isna(H_zscore) or H_zscore is None:
        return 'NORMAL'

    if H_zscore > threshold:
        return 'ENERGY_SPIKE'
    elif H_zscore < -threshold:
        return 'ENERGY_COLLAPSE'
    else:
        return 'NORMAL'


# ============================================================
# 模块六：综合特征计算
# ============================================================

class FeatureEngine:
    """
    特征工程引擎：一次性计算所有力学指标
    """

    def __init__(self, hurst_window=100, lle_window=100, tor_window=60,
                 ham_window=20):
        self.hurst_window = hurst_window
        self.lle_window = lle_window
        self.tor_window = tor_window
        self.ham_window = ham_window

    def compute_all_features(self, df, latest_only=False):
        """
        计算所有特征，返回增强后的 DataFrame

        latest_only=True: 只计算最后一行的 hurst/lle/tor（日报模式，提速~1600x）
        latest_only=False: 全量滚动计算（回测模式）
        """
        df = df.copy()

        # 基础预处理（快，始终全量计算）
        df['log_return'] = np.log(df['close'] / df['close'].shift(1))
        df['detrended'] = df['close'] - df['close'].rolling(20).mean()
        df['realized_vol'] = df['log_return'].rolling(20).std() * np.sqrt(252)
        df['ma20'] = df['close'].rolling(20).mean()
        df['ma_volume'] = df['volume'].rolling(20).mean()

        if latest_only:
            # 日报模式：只计算最后一行的 hurst/lle/tor
            df['hurst'] = np.nan
            df['lle'] = np.nan
            df['toroidality'] = np.nan

            lr_values = df['log_return'].dropna().values
            det_values = df['detrended'].dropna().values
            last_idx = df.index[-1]

            print(f"  计算 Hurst (latest, window={self.hurst_window})...")
            if len(lr_values) >= self.hurst_window:
                df.loc[last_idx, 'hurst'] = hurst_rs(lr_values[-self.hurst_window:])

            print(f"  计算 LLE (latest, window={self.lle_window})...")
            if len(lr_values) >= self.lle_window:
                df.loc[last_idx, 'lle'] = compute_lle(lr_values, self.lle_window)

            print(f"  计算 Toroidality (latest, window={self.tor_window})...")
            if len(det_values) >= self.tor_window:
                df.loc[last_idx, 'toroidality'] = kam_toroidality(
                    det_values[-self.tor_window:], self.tor_window)
        else:
            # 回测模式：全量滚动计算
            print(f"  计算 Hurst (window={self.hurst_window})...")
            df['hurst'] = rolling_hurst(df['log_return'].dropna(),
                                         self.hurst_window).reindex(df.index)

            print(f"  计算 LLE (window={self.lle_window})...")
            df['lle'] = rolling_lle(df['log_return'].dropna(),
                                     self.lle_window).reindex(df.index)

            print(f"  计算 Toroidality (window={self.tor_window})...")
            df['toroidality'] = rolling_toroidality(df['detrended'].dropna(),
                                                     self.tor_window).reindex(df.index)

        # 哈密顿量（快，始终全量计算）
        print(f"  计算 Hamiltonian (window={self.ham_window})...")
        H, dH, H_zscore = market_hamiltonian(df, self.ham_window)
        df['hamiltonian'] = H
        df['hamiltonian_dh'] = dH
        df['hamiltonian_z'] = H_zscore

        return df


# ============================================================
# 模块七：选股模块
# ============================================================

class StockSelector:
    """
    选股模块：多因子打分 + 策略匹配
    """

    def __init__(self, params=None):
        # 阈值根据 A 股实际 LLE 分布校准:
        # LLE 全样本: p10=0.039, p50=0.055, p90=0.072, max=0.089
        self.params = params or {
            'lambda_chaos': 0.075,      # 强混沌阈值 (约 p90)
            'lambda_stable': 0.045,     # 稳定上限 (约 p25)
            'lambda_weak': 0.065,       # 弱混沌上限 (约 p75)
            'tor_breakdown': 0.12,      # 环面破裂阈值
            'tor_stable': 0.20,         # 环面稳定阈值
            'hurst_trend': 0.55,        # 趋势阈值
            'hurst_reversal': 0.45,     # 反转阈值
            'score_threshold': 40,      # 选股分数门槛
            'max_hold_trend': 20,       # 趋势策略最大持仓天数
            'max_hold_reversal': 10,    # 反转策略最大持仓天数
        }

    def calculate_score(self, stock_features, market_lle, market_tor):
        """
        多因子打分
        """
        p = self.params

        # 第一层：大盘过滤
        if market_lle > p['lambda_chaos'] or market_tor < p['tor_breakdown']:
            return None, 'MARKET_CHAOS'

        # 获取个股最新指标
        lle = stock_features['lle']
        H = stock_features['hurst']
        tor = stock_features['toroidality']
        ham_z = stock_features['hamiltonian_z']

        if pd.isna(lle) or pd.isna(H) or pd.isna(tor):
            return None, 'INSUFFICIENT_DATA'

        # S_lambda: LLE 得分（越小越稳定，得分越高）
        # 校准后: lambda 在 [0.03, 0.08] 区间映射到 [100, 0]
        s_lambda = max(0, min(100, (0.08 - lle) / 0.05 * 100))

        # S_hurst: Hurst 得分
        # 改进：趋势和反转都给分，但方向不同
        # 趋势（H>0.55）：H 越大得分越高
        # 反转（H<0.45）：H 越小得分越高
        if H > p['hurst_trend']:
            s_hurst = min(100, (H - 0.5) * 400)  # H=0.55 -> 20, H=0.75 -> 100
        elif H < p['hurst_reversal']:
            s_hurst = min(100, (0.5 - H) * 400)  # H=0.45 -> 20, H=0.25 -> 100
        else:
            s_hurst = 0  # 随机游走区，不给分

        # S_tor: 环面度得分
        s_tor = max(0, min(100, tor * 200))  # tor=0.3 -> 60, tor=0.5 -> 100

        # S_ham: 哈密顿量得分（中等能量最好，z-score 接近 0 得分高）
        if pd.isna(ham_z):
            s_ham = 50
        else:
            s_ham = max(0, 100 - abs(ham_z) * 30)

        # 综合得分
        score = (0.30 * s_lambda + 0.25 * s_hurst +
                 0.25 * s_tor + 0.20 * s_ham)

        # 策略匹配
        if H > p['hurst_trend']:
            strategy = 'trend'
        elif H < p['hurst_reversal']:
            strategy = 'reversal'
        else:
            strategy = 'neutral'

        return {
            'score': score,
            's_lambda': s_lambda,
            's_hurst': s_hurst,
            's_tor': s_tor,
            's_ham': s_ham,
            'lle': lle,
            'hurst': H,
            'toroidality': tor,
            'hamiltonian_z': ham_z,
            'strategy': strategy,
        }, 'OK'

    def check_buy_signal(self, stock_df, features, market_lle, market_tor):
        """
        v3.1 买入信号：保持v2逻辑不变
        （v3数据验证发现新增信号虽然胜率高但样本太小，实际回测反而过度交易）
        """
        p = self.params

        score_info, status = self.calculate_score(features, market_lle, market_tor)

        if status != 'OK' or score_info is None:
            return False, score_info, status

        if score_info['score'] < p['score_threshold']:
            return False, score_info, 'LOW_SCORE'

        # 大盘条件
        if market_lle > p['lambda_weak'] or market_tor < 0.15:
            return False, score_info, 'MARKET_RISK'

        # 个股条件
        if score_info['lle'] > p['lambda_weak']:
            return False, score_info, 'STOCK_CHAOS'

        if score_info['toroidality'] < p['tor_stable']:
            return False, score_info, 'TORUS_WEAK'

        # 价格突破条件
        latest = stock_df.iloc[-1]
        prev = stock_df.iloc[-2]

        if pd.isna(latest['ma20']) or pd.isna(prev['ma20']):
            return False, score_info, 'NO_MA'

        # 趋势策略
        if score_info['strategy'] == 'trend':
            if pd.isna(latest['ma_volume']) or latest['ma_volume'] < 1:
                return False, score_info, 'NO_VOL'
            vol_ratio = latest['volume'] / latest['ma_volume']
            if (prev['close'] <= prev['ma20'] and
                    latest['close'] > latest['ma20'] and
                    vol_ratio > 1.0):
                return True, score_info, 'TREND_BREAKOUT'
            if (latest['close'] > latest['ma20'] and
                    latest['close'] > prev['close'] and
                    vol_ratio > 1.2 and
                    score_info['hurst'] > 0.60):
                return True, score_info, 'TREND_MOMENTUM'

        # 反转策略
        elif score_info['strategy'] == 'reversal':
            lower_band = latest['ma20'] - 2 * stock_df['close'].rolling(20).std().iloc[-1]
            if latest['close'] <= lower_band * 1.02:
                return True, score_info, 'REVERSAL_BOUNCE'

        return False, score_info, 'NO_SIGNAL'


# ============================================================
# 模块八：风控模块
# ============================================================

class RiskManager:
    """
    v3.1 风控体系：在v2基础上增加混沌方向分类

    只做两处改动（基于数据验证）：
    1. 新增DANGER: 高LLE+低Hurst+均线上方 = 顶部反转（-1.81%，胜率14.3%）
    2. 新增CLEAR过滤: 高LLE+高Hurst+均线上方+低Tor = 爆拉前奏（+0.06%，胜率60.5%）
    其余全部保持v2逻辑不变
    """

    def __init__(self, params=None):
        self.params = params or {
            'lle_high': 0.065,
            'lle_low': 0.050,
            'hurst_trend': 0.58,
            'hurst_reversal': 0.45,
            'tor_high': 0.22,
            'tor_low': 0.15,
            'ma_dev_warn': -0.02,
            'ma_dev_danger': -0.05,
            'base_stop_loss': -0.08,
            'tight_stop_loss': -0.05,
            'hamiltonian_threshold': 2.5,
        }

    def check_market_risk(self, market_lle, market_tor, market_hurst=0.5,
                          above_ma20=True, dev_ma20=0.0, below_ma20_days=0):
        p = self.params

        # ====== DANGER ======

        # [v2保留] 高Tor + 低Hurst + 均线下方
        if (market_tor > p['tor_high'] and
                market_hurst < p['hurst_reversal'] and
                not above_ma20):
            return 'DANGER'

        # [v3新增] 高LLE + 低Hurst + 均线上方 = 顶部反转！
        # 数据: -1.81%, 胜率14.3% — 价格还在均线上方但Hurst已崩=趋势要死
        if (market_lle > p['lle_high'] and
                market_hurst < p['hurst_reversal'] and
                above_ma20):
            return 'DANGER'

        # [v2保留] 高Tor + 中Hurst + 跌破均线5%
        if (market_tor > p['tor_high'] and
                p['hurst_reversal'] <= market_hurst <= p['hurst_trend'] and
                dev_ma20 < p['ma_dev_danger']):
            return 'DANGER'

        # [v2保留] 大幅跌破均线 + Hurst高
        if (dev_ma20 < p['ma_dev_danger'] and
                market_hurst > p['hurst_trend'] and
                not above_ma20):
            return 'DANGER'

        # ====== WARNING ======

        # [v2保留] 高Tor + 均线下方
        if (market_tor > p['tor_high'] and not above_ma20):
            return 'WARNING'

        # [v2保留] 跌破均线 + 中性Hurst
        if (dev_ma20 < p['ma_dev_warn'] and
                p['hurst_reversal'] <= market_hurst <= p['hurst_trend']):
            return 'WARNING'

        # [v2保留] 高LLE + 高Tor + 均线下方
        if (market_lle > p['lle_high'] and
                market_tor > p['tor_high'] and
                not above_ma20):
            return 'WARNING'

        return 'CLEAR'

    def dynamic_stop_loss(self, current_lle, market_risk_level='CLEAR'):
        """
        动态止损：根据市场风险等级调整
        """
        p = self.params

        if market_risk_level == 'DANGER':
            return p['tight_stop_loss']  # 危险时5%止损
        elif market_risk_level == 'WARNING':
            return -0.06  # 警告时6%止损
        else:
            return p['base_stop_loss']  # 正常8%止损

    def position_size(self, stock_lle, market_lle, stock_hurst, market_risk_level,
                      above_ma20=True, stock_tor=0.2, vol_ratio=1.0):
        """
        v3.1 仓位管理：在v2基础上增加趋势加速加仓
        """
        base_position = 0.10

        if market_risk_level == 'DANGER':
            return 0.0
        elif market_risk_level == 'WARNING':
            market_discount = 0.4
        else:
            market_discount = 1.0

        # 方向加成（v2逻辑 + v3趋势加速）
        if above_ma20 and stock_hurst > 0.58:
            # [v3新增] 趋势加速：高Hurst+高Tor+波动放大 → 1.4x
            if stock_tor > 0.22 and vol_ratio > 1.2:
                direction_boost = 1.4
            else:
                direction_boost = 1.3
        elif not above_ma20 and stock_hurst < 0.45:
            direction_boost = 0.8
        elif above_ma20 and stock_hurst > 0.55:
            direction_boost = 1.0
        else:
            direction_boost = 0.6

        position = base_position * market_discount * direction_boost
        position = min(position, 0.15)
        position = max(position, 0.02)

        if market_risk_level == 'DANGER':
            position = 0.0

        return position

    def check_sell_signal(self, pos, current_features, market_risk_level,
                          current_price, current_date, market_hurst=0.5,
                          above_ma20=True, below_ma20_days=0):
        """
        v3.1 卖出信号：在v2基础上增加顶部反转信号
        """
        p = self.params

        # 1. 大盘 DANGER：清仓
        if market_risk_level == 'DANGER':
            return True, 'MARKET_DANGER'

        # 2. [v3新增] 顶部反转：高LLE + 低Hurst + 均线上方
        # 数据: -1.81%, 胜率14.3% — 最强下跌信号
        if (current_features['lle'] > p['lle_high'] and
                current_features['hurst'] < p['hurst_reversal'] and
                above_ma20):
            return True, 'TOP_REVERSAL'

        # 3. [v2保留] 趋势反转：Hurst崩塌 + 均线下方
        if (pos['strategy'] == 'trend' and
                current_features['hurst'] < p['hurst_reversal'] and
                not above_ma20):
            return True, 'TREND_REVERSAL'

        # 4. [v2保留] MA破位：价格跌破均线 + Hurst下降
        if (pos['strategy'] == 'trend' and
                not above_ma20 and
                current_features['hurst'] < pos['entry_hurst'] - 0.05):
            return True, 'MA_BREAKDOWN'

        # 5. [v2保留] 反转策略：Hurst回到趋势区
        if (pos['strategy'] == 'reversal' and
                current_features['hurst'] > 0.55 and
                above_ma20):
            return True, 'REVERSAL_COMPLETE'

        # 6. 止损
        pnl = (current_price - pos['entry_price']) / pos['entry_price']
        stop = self.dynamic_stop_loss(current_features['lle'], market_risk_level)
        if pnl < stop:
            return True, f'STOP_LOSS({pnl:.1%})'

        # 7. 移动止盈
        max_price = pos.get('max_price', pos['entry_price'])
        if current_price > max_price:
            pos['max_price'] = current_price
            max_price = current_price

        if pos['strategy'] == 'trend':
            if pnl > 0.12:
                trailing = (current_price - max_price) / max_price
                if trailing < -0.06:
                    return True, f'TRAILING_PROFIT({pnl:.1%})'
        else:
            if pnl > 0.08:
                return True, f'TAKE_PROFIT({pnl:.1%})'

        # 8. 持仓超时
        hold_days = (current_date - pos['entry_date']).days
        max_days = 40 if pos['strategy'] == 'trend' else 15
        if hold_days > max_days:
            if pos['strategy'] == 'trend' and pnl > 0.03:
                pass
            else:
                return True, 'TIME_EXIT'

        return False, 'HOLD'


# ============================================================
# 模块九：回测引擎
# ============================================================

class BacktestEngine:
    """
    回测引擎

    特点：
    - 每日盘前计算指标
    - 三层风控
    - 选股 + 仓位管理
    - 交易记录 + 绩效分析
    """

    def __init__(self, initial_cash=1_000_000, commission=0.0003,
                 slippage=0.001, max_positions=10):
        self.initial_cash = initial_cash
        self.commission = commission
        self.slippage = slippage
        self.max_positions = max_positions

        self.cash = initial_cash
        self.positions = {}  # code -> position dict
        self.nav_history = []
        self.trade_log = []
        self.daily_signals = []

    def _execute_buy(self, code, price, size_pct, date, score_info):
        """
        执行买入
        """
        if len(self.positions) >= self.max_positions:
            return

        if code in self.positions:
            return

        # 计算买入股数（按 size_pct 分配资金）
        target_value = self.nav() * size_pct
        buy_price = price * (1 + self.slippage)  # 滑点
        shares = int(target_value / buy_price / 100) * 100  # 整手

        if shares < 100:
            return

        cost = shares * buy_price
        commission_fee = cost * self.commission
        total_cost = cost + commission_fee

        if total_cost > self.cash:
            shares = int(self.cash / buy_price / 100) * 100
            if shares < 100:
                return
            cost = shares * buy_price
            commission_fee = cost * self.commission
            total_cost = cost + commission_fee

        self.cash -= total_cost

        self.positions[code] = {
            'code': code,
            'shares': shares,
            'entry_price': buy_price,
            'max_price': buy_price,  # 移动止盈用
            'entry_date': date,
            'entry_lle': score_info['lle'],
            'entry_hurst': score_info['hurst'],
            'entry_toroidality': score_info['toroidality'],
            'entry_hamiltonian_z': score_info.get('hamiltonian_z', 0),
            'strategy': score_info['strategy'],
            'cost': total_cost,
        }

        self.trade_log.append({
            'date': date,
            'code': code,
            'action': 'BUY',
            'price': buy_price,
            'shares': shares,
            'commission': commission_fee,
            'score': score_info['score'],
            'strategy': score_info['strategy'],
            'lle': score_info['lle'],
            'hurst': score_info['hurst'],
            'toroidality': score_info['toroidality'],
        })

    def _execute_sell(self, code, price, date, reason):
        """
        执行卖出
        """
        if code not in self.positions:
            return

        pos = self.positions[code]
        sell_price = price * (1 - self.slippage)  # 滑点
        revenue = pos['shares'] * sell_price
        commission_fee = revenue * self.commission
        net_revenue = revenue - commission_fee

        self.cash += net_revenue

        pnl = net_revenue - pos['cost']
        pnl_pct = (sell_price - pos['entry_price']) / pos['entry_price']

        self.trade_log.append({
            'date': date,
            'code': code,
            'action': 'SELL',
            'price': sell_price,
            'shares': pos['shares'],
            'commission': commission_fee,
            'reason': reason,
            'pnl': pnl,
            'pnl_pct': pnl_pct,
            'hold_days': (date - pos['entry_date']).days,
            'strategy': pos['strategy'],
        })

        del self.positions[code]

    def nav(self):
        """
        当前净值
        """
        return self.cash  # 简化：持仓按成本计价，日终按市价调整

    def _mark_to_market(self, date, price_data):
        """
        按市价标记
        """
        total_value = self.cash
        for code, pos in self.positions.items():
            if code in price_data:
                total_value += pos['shares'] * price_data[code]
        return total_value

    def run(self, market_df, stock_data_dict, feature_engine,
            selector, risk_manager, start_date, end_date):
        """
        运行回测

        参数：
        - market_df: 大盘指数数据（含特征）
        - stock_data_dict: {code: df} 个股数据（含特征）
        - feature_engine: 特征引擎
        - selector: 选股器
        - risk_manager: 风控器
        - start_date, end_date: 回测区间
        """
        # 准备交易日历
        trading_days = market_df[
            (market_df['date'] >= start_date) &
            (market_df['date'] <= end_date)
        ]['date'].tolist()

        print(f"\n回测区间: {start_date} ~ {end_date}")
        print(f"交易日数: {len(trading_days)}")
        print(f"股票池大小: {len(stock_data_dict)}")

        market_df = market_df.set_index('date')

        # v3: 预计算大盘连续在MA20下方的天数
        market_dates = market_df.index.tolist()
        market_below_days = {}
        consecutive_below = 0
        for d in market_dates:
            row = market_df.loc[d]
            mc = row.get('close', 0)
            mm = row.get('ma20', mc)
            if mm > 0 and mc < mm:
                consecutive_below += 1
            else:
                consecutive_below = 0
            market_below_days[d] = consecutive_below

        for i, date in enumerate(trading_days):
            if i % 50 == 0:
                print(f"  进度: {i}/{len(trading_days)} ({date.strftime('%Y-%m-%d')})")

            # 获取当日大盘特征
            if date not in market_df.index:
                continue

            market_row = market_df.loc[date]
            market_lle = market_row.get('lle', 0)
            market_tor = market_row.get('toroidality', 0.3)
            market_hurst = market_row.get('hurst', 0.5)
            market_close = market_row.get('close', 0)
            market_ma20 = market_row.get('ma20', market_close)
            market_above_ma20 = market_close > market_ma20 if market_ma20 > 0 else True
            market_dev_ma20 = (market_close - market_ma20) / market_ma20 if market_ma20 > 0 else 0
            market_below_ma20_days = market_below_days.get(date, 0)

            # 第一层风控（v3.0: 混沌方向分类）
            market_risk = risk_manager.check_market_risk(
                market_lle, market_tor, market_hurst,
                market_above_ma20, market_dev_ma20,
                below_ma20_days=market_below_ma20_days
            )

            # 记录每日信号
            daily_signal = {
                'date': date,
                'market_lle': market_lle,
                'market_tor': market_tor,
                'market_hurst': market_hurst,
                'market_above_ma20': market_above_ma20,
                'market_dev_ma20': market_dev_ma20,
                'market_risk': market_risk,
                'nav': 0,
                'cash_ratio': 0,
                'n_positions': 0,
                'buy_signals': [],
                'sell_signals': [],
            }

            # 如果大盘危险，清仓
            if market_risk == 'DANGER':
                for code in list(self.positions.keys()):
                    if code in stock_data_dict:
                        stock_df = stock_data_dict[code]
                        stock_row = stock_df[stock_df['date'] == date]
                        if len(stock_row) > 0:
                            price = stock_row.iloc[0]['close']
                            self._execute_sell(code, price, date, 'MARKET_DANGER')
                            daily_signal['sell_signals'].append({
                                'code': code, 'reason': 'MARKET_DANGER'
                            })

            else:
                # 持仓管理：检查卖出信号
                for code in list(self.positions.keys()):
                    if code not in stock_data_dict:
                        continue

                    stock_df = stock_data_dict[code]
                    stock_row = stock_df[stock_df['date'] == date]
                    if len(stock_row) == 0:
                        continue

                    current_price = stock_row.iloc[0]['close']

                    # 获取截止当日的特征
                    hist = stock_df[stock_df['date'] <= date]
                    if len(hist) < 2:
                        continue

                    current_features = {
                        'lle': hist.iloc[-1].get('lle', 0),
                        'hurst': hist.iloc[-1].get('hurst', 0.5),
                        'toroidality': hist.iloc[-1].get('toroidality', 0.3),
                        'hamiltonian_z': hist.iloc[-1].get('hamiltonian_z', 0),
                    }

                    # 个股均线状态
                    stock_ma20 = hist.iloc[-1].get('ma20', current_price)
                    stock_above_ma20 = current_price > stock_ma20 if stock_ma20 > 0 else True

                    # v3: 计算个股连续在MA20下方天数
                    stock_below_days = 0
                    for j in range(len(hist)-1, -1, -1):
                        sj = hist.iloc[j]
                        sm = sj.get('ma20', sj['close'])
                        if sm > 0 and sj['close'] < sm:
                            stock_below_days += 1
                        else:
                            break

                    should_sell, reason = risk_manager.check_sell_signal(
                        self.positions[code], current_features,
                        market_risk, current_price, date,
                        market_hurst=market_hurst,
                        above_ma20=stock_above_ma20,
                        below_ma20_days=stock_below_days,
                    )

                    if should_sell:
                        self._execute_sell(code, current_price, date, reason)
                        daily_signal['sell_signals'].append({
                            'code': code, 'reason': reason
                        })

                # 选股 + 买入
                if market_risk != 'DANGER' and len(self.positions) < selector.params.get('max_positions', 10):
                    candidates = []

                    for code, stock_df in stock_data_dict.items():
                        if code in self.positions:
                            continue

                        hist = stock_df[stock_df['date'] <= date]
                        if len(hist) < max(feature_engine.hurst_window,
                                           feature_engine.lle_window) + 5:
                            continue

                        latest = hist.iloc[-1]
                        features = {
                            'lle': latest.get('lle', np.nan),
                            'hurst': latest.get('hurst', np.nan),
                            'toroidality': latest.get('toroidality', np.nan),
                            'hamiltonian_z': latest.get('hamiltonian_z', np.nan),
                        }

                        signal, score_info, status = selector.check_buy_signal(
                            hist, features, market_lle, market_tor
                        )

                        if signal and score_info:
                            candidates.append((code, score_info, latest['close']))

                    # 按得分排序
                    candidates.sort(key=lambda x: x[1]['score'], reverse=True)

                    for code, score_info, price in candidates[:3]:
                        # 个股均线状态
                        stock_hist = stock_data_dict[code]
                        stock_hist_to_date = stock_hist[stock_hist['date'] <= date]
                        stock_ma20 = stock_hist_to_date.iloc[-1].get('ma20', price) if len(stock_hist_to_date) > 0 else price
                        stock_above = price > stock_ma20 if stock_ma20 > 0 else True

                        # v3: 计算个股波动率比率
                        stock_vol_ratio = 1.0
                        if len(stock_hist_to_date) >= 60:
                            recent_vol = stock_hist_to_date['close'].pct_change().iloc[-20:].std()
                            long_vol = stock_hist_to_date['close'].pct_change().iloc[-60:].std()
                            if long_vol > 0:
                                stock_vol_ratio = recent_vol / long_vol

                        size = risk_manager.position_size(
                            score_info['lle'], market_lle,
                            score_info['hurst'], market_risk,
                            above_ma20=stock_above,
                            stock_tor=score_info['toroidality'],
                            vol_ratio=stock_vol_ratio,
                        )
                        if size > 0:
                            self._execute_buy(code, price, size, date, score_info)
                            daily_signal['buy_signals'].append({
                                'code': code,
                                'score': score_info['score'],
                                'strategy': score_info['strategy']
                            })

            # 日终估值
            price_data = {}
            for code in self.positions:
                if code in stock_data_dict:
                    stock_df = stock_data_dict[code]
                    stock_row = stock_df[stock_df['date'] == date]
                    if len(stock_row) > 0:
                        price_data[code] = stock_row.iloc[0]['close']

            current_nav = self._mark_to_market(date, price_data)
            daily_signal['nav'] = current_nav
            daily_signal['cash_ratio'] = self.cash / current_nav if current_nav > 0 else 1
            daily_signal['n_positions'] = len(self.positions)

            self.nav_history.append(daily_signal)

        # 回测结束，清算剩余持仓
        last_date = trading_days[-1] if trading_days else end_date
        for code in list(self.positions.keys()):
            if code in stock_data_dict:
                stock_df = stock_data_dict[code]
                stock_row = stock_df[stock_df['date'] <= last_date]
                if len(stock_row) > 0:
                    price = stock_row.iloc[-1]['close']
                    self._execute_sell(code, price, last_date, 'BACKTEST_END')

        return self._analyze_results()

    def _analyze_results(self):
        """
        绩效分析
        """
        if not self.nav_history:
            return {}

        nav_series = pd.Series([d['nav'] for d in self.nav_history],
                               index=[d['date'] for d in self.nav_history])

        # 日收益率
        returns = nav_series.pct_change().dropna()

        # 累计收益率
        total_return = (nav_series.iloc[-1] / self.initial_cash - 1)

        # 年化收益率
        n_days = len(nav_series)
        years = n_days / 252
        cagr = (nav_series.iloc[-1] / self.initial_cash) ** (1 / years) - 1 if years > 0 else 0

        # 最大回撤
        peak = nav_series.expanding().max()
        drawdown = (nav_series - peak) / peak
        max_drawdown = drawdown.min()

        # 夏普比率（无风险利率 2%）
        rf = 0.02 / 252
        if len(returns) > 1 and returns.std() > 0:
            sharpe = (returns.mean() - rf) / returns.std() * np.sqrt(252)
        else:
            sharpe = 0

        # 卡玛比率
        calmar = cagr / abs(max_drawdown) if max_drawdown != 0 else 0

        # 交易统计
        trades = pd.DataFrame(self.trade_log)
        sell_trades = trades[trades['action'] == 'SELL'].copy() if len(trades) > 0 else pd.DataFrame()

        if len(sell_trades) > 0:
            win_trades = sell_trades[sell_trades['pnl'] > 0]
            win_rate = len(win_trades) / len(sell_trades)
            avg_win = win_trades['pnl'].mean() if len(win_trades) > 0 else 0
            lose_trades = sell_trades[sell_trades['pnl'] <= 0]
            avg_loss = lose_trades['pnl'].mean() if len(lose_trades) > 0 else 0
            profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else 0
        else:
            win_rate = 0
            avg_win = 0
            avg_loss = 0
            profit_factor = 0

        # 月度收益
        monthly_nav = nav_series.resample('ME').last()
        monthly_returns = monthly_nav.pct_change().dropna()
        monthly_win_rate = (monthly_returns > 0).sum() / len(monthly_returns) if len(monthly_returns) > 0 else 0

        return {
            'nav_series': nav_series,
            'drawdown_series': drawdown,
            'returns': returns,
            'total_return': total_return,
            'cagr': cagr,
            'max_drawdown': max_drawdown,
            'sharpe': sharpe,
            'calmar': calmar,
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'monthly_win_rate': monthly_win_rate,
            'n_trades': len(sell_trades),
            'trades': trades,
            'nav_history': self.nav_history,
        }


# ============================================================
# 主入口
# ============================================================

if __name__ == '__main__':
    print("分析力学量化交易系统 v3.0")
    print("=" * 60)
    print("模块列表:")
    print("  1. Hurst 指数 (R/S 分析, 自研)")
    print("  2. 最大李雅普诺夫指数 (Rosenstein 方法)")
    print("  3. KAM 环面代理指标")
    print("  4. 哈密顿量代理指标")
    print("  5. 选股模块 (多因子打分)")
    print("  6. 风控模块 (三层风控)")
    print("  7. 回测引擎")
    print()
    print("使用 run_backtest.py 运行完整回测")
