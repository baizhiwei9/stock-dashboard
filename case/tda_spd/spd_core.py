#!/usr/bin/env python3
"""
SPD几何漂移行业选择模块
========================
用协方差矩阵SPD流形上的Fisher-z测地漂移替代TDA流形旋转的偏好系数矩阵。
TDA仍负责市场状态判断和仓位控制，本模块只负责选行业/因子权重。

核心公式: w_i = drift_i x (s_i+1)^2 x discount
  drift_i: 因子i与等权市场的Fisher-z相关结构漂移(SPD(2)测地距离)
  s_i:     因子夏普比(与TDA完全相同)
  discount: 正夏普动量折扣(与TDA相同, 默认0.5)
"""

import numpy as np
from scipy.linalg import logm


def compute_spd_drift_scores(returns_current, returns_ref):
    """
    计算每个因子的SPD测地漂移得分。

    对每个因子i:
    1. 计算它与等权市场组合的相关系数 rho_i
    2. Fisher z变换: z_i = atanh(rho_i)  <- SPD(2)流形上的测地坐标
    3. 漂移 = |z_i(t) - z_i(t_ref)|     <- SPD(2)上的测地线距离

    同时计算全矩阵漂移(Log-Euclidean)作为总结构漂移监控指标。

    参数:
        returns_current: 当前窗口日收益率 DataFrame (n_days x n_factors)
        returns_ref: 参考窗口日收益率 DataFrame (n_days x n_factors)

    返回:
        drift_scores: dict {因子名: 漂移得分}
        total_drift: float, 全相关矩阵Log-Euclidean测地距离
    """
    factor_names = returns_current.columns.tolist()
    market_current = returns_current.mean(axis=1)
    market_ref = returns_ref.mean(axis=1)

    drift_scores = {}
    for f in factor_names:
        rho_now = returns_current[f].corr(market_current)
        rho_ref = returns_ref[f].corr(market_ref)
        rho_now = np.clip(rho_now, -0.999, 0.999)
        rho_ref = np.clip(rho_ref, -0.999, 0.999)
        z_now = np.arctanh(rho_now)
        z_ref = np.arctanh(rho_ref)
        drift_scores[f] = abs(z_now - z_ref)

    R_current = returns_current.corr()
    R_ref = returns_ref.corr()
    try:
        diff = logm(R_current.values) - logm(R_ref.values)
        total_drift = float(np.sqrt(np.sum(np.real(diff) ** 2)))
    except Exception:
        total_drift = 0.0

    return drift_scores, total_drift


def compute_spd_weights(price_window, price_df, current_idx, sharpe_dict,
                        top_n=5, momentum_discount=0.5, window=60, ref_offset=60):
    """
    SPD行业选择权重: drift x (s+1)^2, Top N集中持仓。

    参数:
        price_window: 当前60日价格窗口 DataFrame (用于计算收益率)
        price_df: 完整价格矩阵 (用于取参考窗口)
        current_idx: 当前日期在price_df.index中的位置
        sharpe_dict: {因子名: 夏普比}
        top_n: 集中持仓因子数
        momentum_discount: 正夏普动量折扣
        window: 回看窗口
        ref_offset: 参考窗口偏移(交易日)

    返回:
        weights: dict {因子名: 权重}, Top N归一化, 降序排列
        total_drift: float, 全矩阵漂移(监控用)
    """
    # 只剔除全空行(how='all'), 不整行 dropna: 单个因子的缺口不应把该日从全部因子中删除
    returns_current = price_window.pct_change().dropna(how='all')

    ref_end = current_idx - ref_offset
    if ref_end < window:
        returns_ref = returns_current
    else:
        price_ref = price_df.iloc[max(0, ref_end - window + 1):ref_end + 1]
        returns_ref = price_ref.pct_change().dropna(how='all')

    drift_scores, total_drift = compute_spd_drift_scores(returns_current, returns_ref)

    raw_weights = {}
    for factor, s in sharpe_dict.items():
        drift = drift_scores.get(factor, 0.0)
        momentum_w = (s + 1) ** 2
        if s > 0:
            momentum_w = momentum_w * momentum_discount
        momentum_w = max(0, momentum_w)
        drift_w = drift if drift > 0 else 0.001
        raw_w = drift_w * momentum_w
        raw_weights[factor] = max(0, raw_w)

    sorted_factors = sorted(raw_weights.items(), key=lambda x: x[1], reverse=True)
    top_factors = sorted_factors[:top_n]

    total = sum(w for _, w in top_factors)
    if total == 0:
        weights = {f: 1.0 / top_n for f, _ in top_factors}
    else:
        weights = {f: w / total for f, w in top_factors}

    weights = dict(sorted(weights.items(), key=lambda x: x[1], reverse=True))
    weights = {k: round(v, 4) for k, v in weights.items()}

    return weights, total_drift
