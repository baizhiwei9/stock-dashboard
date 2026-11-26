"""
市场流动性+风险偏好综合监控指标
每日计算并生成柱状图展示近一周趋势
新增：吸收率因子(Absorption Ratio)、兴登堡指标(Hindenburg Omen)
"""

# akshare 兼容性处理
import sys, pkgutil
sys.modules['pkgutil.ImpImporter'] = type('ImpImporter', (), {'find_module': lambda *a: None})()

import tushare as ts
import pandas as pd
from datetime import datetime, timedelta
import json
import os
import numpy as np
import akshare as ak
# DataHub 根（唯一口径见 case/_lib/hub_root.py，与 datahub_path.py 一致）
def _hub_root_path(_f=__file__):
    _d = os.path.dirname(os.path.abspath(_f))
    for _ in range(6):
        _lib = os.path.join(_d, "_lib")
        if os.path.isfile(os.path.join(_lib, "hub_root.py")):
            if _lib not in sys.path:
                sys.path.insert(0, _lib)
            from hub_root import hub_root
            return hub_root(_d)
        _d = os.path.dirname(_d)
    return '/opt/coze/.fuse_drive/dataHub_三级体系'

_HUB_ROOT = _hub_root_path()


# ============================================================
# data_hub 统一数据接口（两融 / 期权等统一从这里取数）
# ============================================================
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_HUB_SCRIPTS = os.path.join(_BASE_DIR, 'codeact', 'scripts')
if _HUB_SCRIPTS not in sys.path:
    sys.path.insert(0, _HUB_SCRIPTS)

# DataHub 统一取数（禁止直连 akshare/tushare）
_HUB_MAIN = _HUB_ROOT
if os.path.isdir(os.path.join(_HUB_MAIN, 'data_hub')) and _HUB_MAIN not in sys.path:
    sys.path.insert(0, _HUB_MAIN)
try:
    import data_hub as dh
except Exception as _e:
    dh = None
    print(f"  [警告] DataHub 导入失败，将回退直连: {_e}")

# 设置Tushare Token
def _hub_lib_dir(_f=__file__):
    _d = os.path.dirname(os.path.abspath(_f))
    for _ in range(6):
        _lib = os.path.join(_d, "_lib")
        if os.path.isfile(os.path.join(_lib, "hub_root.py")):
            return _lib
        _d = os.path.dirname(_d)
    return ""

_HUB_LIB_DIR = _hub_lib_dir()
if _HUB_LIB_DIR and _HUB_LIB_DIR not in sys.path:
    sys.path.insert(0, _HUB_LIB_DIR)
from hub_root import tushare_token as _tushare_token, smtp_password as _smtp_password  # noqa: E402

ts.set_token(_tushare_token())
pro = ts.pro_api()

# 创建输出目录
OUTPUT_DIR = '市场情绪监控/outputs'
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ============================================================
# 吸收率因子(Absorption Ratio)模块
# ============================================================

# 申万一级行业代码列表（31个）
SW_INDUSTRY_CODES = [
    '801010',  # 农林牧渔
    '801030',  # 基础化工
    '801040',  # 钢铁
    '801050',  # 有色金属
    '801080',  # 电子
    '801110',  # 家用电器
    '801120',  # 食品饮料
    '801130',  # 纺织服饰
    '801140',  # 轻工制造
    '801150',  # 医药生物
    '801160',  # 公用事业
    '801170',  # 交通运输
    '801180',  # 房地产
    '801200',  # 商贸零售
    '801210',  # 社会服务
    '801230',  # 综合
    '801710',  # 建筑材料
    '801720',  # 建筑装饰
    '801730',  # 电力设备
    '801740',  # 国防军工
    '801750',  # 计算机
    '801760',  # 传媒
    '801770',  # 通信
    '801780',  # 银行
    '801790',  # 非银金融
    '801880',  # 汽车
    '801890',  # 机械设备
    '801950',  # 煤炭
    '801960',  # 环保
    '801970',  # 美容护理
    '801980',  # 石油石化
]


def get_sw_industry_data():
    """获取申万一级行业指数日线数据"""
    industry_data = {}
    success_count = 0
    
    for code in SW_INDUSTRY_CODES:
        try:
            df = dh.fetch_sw_index_daily(code) if dh else None
            if df is not None and len(df) > 0:
                # DataHub 列名 → 业务列名
                df = df.rename(columns={
                    'trade_date': '日期', 'open': '开盘', 'high': '最高',
                    'low': '最低', 'close': '收盘', 'vol': '成交量', 'amount': '成交额',
                })
                if '代码' not in df.columns:
                    df['代码'] = code
                df['日期'] = df['日期'].astype(str).str.replace('-', '')
                df = df.sort_values('日期').reset_index(drop=True)
                industry_data[code] = df
                success_count += 1
        except Exception as e:
            print(f"  [警告] 行业 {code} 数据获取失败: {e}")
    
    print(f"  成功获取 {success_count}/{len(SW_INDUSTRY_CODES)} 个行业数据")
    return industry_data


def calculate_exponential_weights(n, half_life):
    """计算指数衰减权重"""
    decay_rate = np.log(2) / half_life
    t = np.arange(n)
    weights = np.exp(-decay_rate * (n - 1 - t))
    return weights / weights.sum()


def calculate_weighted_covariance(returns, weights):
    """计算指数衰减加权协方差矩阵"""
    n, m = returns.shape  # n: 时间步数, m: 资产数
    weighted_returns = returns * weights[:, np.newaxis]
    cov_matrix = np.cov(weighted_returns, rowvar=False, ddof=1)
    return cov_matrix


def calculate_absorption_ratio():
    """计算吸收率因子(Absorption Ratio)
    
    Returns:
        dict: {
            ar: 当前AR值,
            ar_5d: 近5日AR均值,
            dar_long: Long DAR (vs 250日均值),
            dar_short: Short DAR (vs 125日均值),
            long_signal: Long信号 (-1, 0, 1),
            short_signal: Short信号 (-1, 0, 1),
            final_signal: 融合信号 (-1, 0, 1),
            signal_desc: 信号描述,
            n_industries: 有效行业数,
            pc1_explained: PC1解释方差比例,
            pc3_explained: PC1+PC2+PC3解释方差比例
        }
    """
    print("\n--- 计算吸收率因子(Absorption Ratio) ---")
    
    # 1. 获取31个行业数据
    industry_data = get_sw_industry_data()
    
    if len(industry_data) < 10:
        print("  [警告] 行业数据不足，返回默认值")
        return _default_ar_result()
    
    # 2. 对齐日期，取共同交易日
    all_dates = None
    aligned_data = {}
    
    for code, df in industry_data.items():
        df['日期'] = pd.to_datetime(df['日期'])
        if all_dates is None:
            all_dates = set(df['日期'])
        else:
            all_dates = all_dates.intersection(set(df['日期']))
    
    all_dates = sorted(list(all_dates))
    print(f"  共同交易日数量: {len(all_dates)}")
    
    if len(all_dates) < 260:
        print(f"  [警告] 数据不足250天，返回默认值")
        return _default_ar_result()
    
    # 3. 构建收益率矩阵
    returns_matrix = []
    valid_codes = []
    
    for code, df in industry_data.items():
        df_indexed = df.set_index('日期')
        # 计算日收益率
        closes = df_indexed.loc[all_dates, '收盘'].values
        ret = np.diff(closes) / closes[:-1]
        if len(ret) == len(all_dates) - 1:
            returns_matrix.append(ret)
            valid_codes.append(code)
    
    if len(returns_matrix) < 10:
        return _default_ar_result()
    
    returns_matrix = np.array(returns_matrix).T  # shape: (days, n_assets)
    n_industries = len(valid_codes)
    print(f"  有效行业数: {n_industries}")
    
    # 4. 计算滚动AR
    window_long = 250
    window_short = 125
    half_life = 125
    k = 3  # 前K个主成分
    
    ar_series = []
    
    # 需要至少 window_long 天的数据
    min_start = window_long
    total_days = len(returns_matrix)
    
    for i in range(min_start, total_days):
        window_data = returns_matrix[i-window_long:i]
        
        # 计算指数衰减权重
        weights = calculate_exponential_weights(window_long, half_life)
        
        # 计算加权均值
        weighted_mean = np.average(window_data, weights=weights, axis=0)
        
        # 计算加权协方差矩阵
        centered = window_data - weighted_mean
        cov_matrix = np.cov((centered * weights[:, np.newaxis]).T, ddof=1)
        
        # 处理协方差矩阵确保对称正定
        cov_matrix = (cov_matrix + cov_matrix.T) / 2
        eigvals, eigvecs = np.linalg.eigh(cov_matrix)
        
        # 排序特征值（升序）
        idx = np.argsort(eigvals)[::-1]
        eigvals = eigvals[idx]
        
        total_var = np.sum(eigvals)
        if total_var > 0:
            pc3_var = np.sum(eigvals[:min(k, len(eigvals))])
            ar = pc3_var / total_var
        else:
            ar = 0.5
        
        ar_series.append(ar)
    
    ar_series = np.array(ar_series)
    
    if len(ar_series) < 5:
        return _default_ar_result()
    
    # 5. 计算当前AR和5日均值
    ar = float(ar_series[-1])
    ar_5d = float(np.mean(ar_series[-5:]))
    
    # 6. 计算DAR_long (vs 250日均值)
    ar_250_mean = float(np.mean(ar_series))
    ar_250_std = float(np.std(ar_series))
    
    if ar_250_std > 0:
        dar_long = (ar_5d - ar_250_mean) / ar_250_std
    else:
        dar_long = 0
    
    # 7. 计算DAR_short (vs 125日均值)
    if len(ar_series) >= 125:
        ar_125_mean = float(np.mean(ar_series[-125:]))
        ar_125_std = float(np.std(ar_series[-125:]))
        
        if ar_125_std > 0:
            dar_short = (ar_5d - ar_125_mean) / ar_125_std
        else:
            dar_short = 0
    else:
        dar_short = dar_long
    
    # 8. 信号生成
    # Long Signal
    if dar_long > 1:
        long_signal = 1  # 尖峰
    elif dar_long < -1:
        long_signal = -1  # 低谷
    else:
        long_signal = 0
    
    # Short Signal
    if dar_short > 1:
        short_signal = 1  # 尖峰
    elif dar_short < -1:
        short_signal = -1  # 低谷
    else:
        short_signal = 0
    
    # 9. AR 5日趋势
    ar_5d_trend = ar_5d - float(np.mean(ar_series[-10:-5])) if len(ar_series) >= 10 else 0
    
    # 10. 融合规则
    if long_signal == 1 and short_signal == 1:
        final_signal = 1
        signal_desc = "风险共振↑ 🔴"
    elif long_signal == -1 and short_signal == -1:
        final_signal = -1
        signal_desc = "风险分散 🟢"
    elif long_signal == 0 and short_signal == 0:
        final_signal = 0
        signal_desc = "中性 ⚪"
    elif long_signal == 1 and short_signal == 0:
        final_signal = 1 if ar_5d_trend > 0 else 0
        signal_desc = "风险积聚" if final_signal == 1 else "中性 ⚪"
    elif long_signal == -1 and short_signal == 0:
        final_signal = -1 if ar_5d_trend < 0 else 0
        signal_desc = "风险释放" if final_signal == -1 else "中性 ⚪"
    elif (long_signal == 1 and short_signal == -1) or (long_signal == -1 and short_signal == 1):
        final_signal = 0
        signal_desc = "信号冲突 → 中性 ⚪"
    elif long_signal == 0 and short_signal == 1:
        final_signal = 0
        signal_desc = "短期风险↑ ⚪"
    elif long_signal == 0 and short_signal == -1:
        final_signal = 0
        signal_desc = "短期风险↓ ⚪"
    else:
        final_signal = 0
        signal_desc = "中性 ⚪"
    
    # 计算PC1和PC3解释方差（用于显示）
    # 重新计算当前窗口
    window_data = returns_matrix[-window_long:]
    weights = calculate_exponential_weights(window_long, half_life)
    weighted_mean = np.average(window_data, weights=weights, axis=0)
    centered = window_data - weighted_mean
    cov_matrix = np.cov((centered * weights[:, np.newaxis]).T, ddof=1)
    cov_matrix = (cov_matrix + cov_matrix.T) / 2
    eigvals, _ = np.linalg.eigh(cov_matrix)
    eigvals = np.sort(eigvals)[::-1]
    total_var = np.sum(eigvals)
    
    pc1_explained = float(eigvals[0] / total_var) if total_var > 0 else 0
    pc3_explained = float(np.sum(eigvals[:3]) / total_var) if total_var > 0 else 0
    
    result = {
        'ar': round(ar, 4),
        'ar_5d': round(ar_5d, 4),
        'dar_long': round(dar_long, 2),
        'dar_short': round(dar_short, 2),
        'long_signal': long_signal,
        'short_signal': short_signal,
        'final_signal': final_signal,
        'signal_desc': signal_desc,
        'n_industries': n_industries,
        'pc1_explained': round(pc1_explained * 100, 2),
        'pc3_explained': round(pc3_explained * 100, 2),
    }
    
    print(f"  AR={ar:.4f}, AR_5d={ar_5d:.4f}")
    print(f"  DAR_long={dar_long:.2f}, DAR_short={dar_short:.2f}")
    print(f"  最终信号: {signal_desc}")
    print(f"  PC1解释方差: {pc1_explained:.2f}%, PC3累计: {pc3_explained:.2f}%")
    
    return result


def _default_ar_result():
    """返回默认AR结果"""
    return {
        'ar': 0.5,
        'ar_5d': 0.5,
        'dar_long': 0,
        'dar_short': 0,
        'long_signal': 0,
        'short_signal': 0,
        'final_signal': 0,
        'signal_desc': '数据不足 ⚪',
        'n_industries': 0,
        'pc1_explained': 0,
        'pc3_explained': 0,
    }


# ============================================================
# 兴登堡指标(Hindenburg Omen)模块
# ============================================================

def calculate_hindenburg_omen():
    """计算兴登堡指标(Hindenburg Omen)
    
    Returns:
        dict: {
            new_highs_count: 120日新高数量,
            new_lows_count: 120日新低数量,
            high_pct: 新高占比(%),
            low_pct: 新低占比(%),
            index_close: 上证指数收盘价,
            index_above_50ma: 指数是否高于50日均线,
            mcclellan_proxy: McClellan代理指标,
            hindenburg_triggered: 是否触发兴登堡警告,
            hindenburg_desc: 文字描述
        }
    """
    print("\n--- 计算兴登堡指标(Hindenburg Omen) ---")
    
    try:
        # 1. 获取市场新高新低统计
        df_hl = dh.get_high_low_statistics('all') if dh else None
        
        if df_hl is None or len(df_hl) == 0:
            print("  [警告] 高低统计数据获取失败，返回默认值")
            return _default_hindenburg_result()
        
        # 列名: ['date', 'close', 'high20', 'low20', 'high60', 'low60', 'high120', 'low120']
        df_hl = df_hl.sort_values('date').reset_index(drop=True)
        
        # 最新数据
        latest = df_hl.iloc[-1]
        high120 = int(latest['high120'])
        low120 = int(latest['low120'])
        index_close = float(latest['close'])
        
        # 2. A股总股票数（使用估算值，因为获取全部代码较慢）
        # 可用近期总成交股票数近似
        try:
            today = datetime.now().strftime('%Y%m%d')
            df_total = dh.get_market_daily(trade_date=today) if dh else None
            if df_total is not None and len(df_total) > 0:
                total_stocks = len(df_total)
            else:
                # 备用：尝试前一天
                prev_date = (datetime.now() - timedelta(days=1)).strftime('%Y%m%d')
                df_total = dh.get_market_daily(trade_date=prev_date) if dh else None
                total_stocks = len(df_total) if df_total is not None else 5300
        except:
            total_stocks = 5300
        
        # 3. 计算占比
        high_pct = high120 / total_stocks * 100
        low_pct = low120 / total_stocks * 100
        
        # 4. 检查上证指数是否高于50日均线
        index_above_50ma = False
        if len(df_hl) >= 50:
            ma50 = df_hl['close'].rolling(50).mean().iloc[-1]
            index_above_50ma = index_close > ma50
        
        # 5. McClellan代理指标（使用high120-low120的变化方向）
        # 如果当日high120-low120扩大，说明市场宽度恶化
        mcclellan_proxy = 0
        if len(df_hl) >= 2:
            diff_today = high120 - low120
            prev = df_hl.iloc[-2]
            prev_diff = int(prev['high120']) - int(prev['low120'])
            mcclellan_proxy = diff_today - prev_diff
        
        # 6. 兴登堡触发条件
        # 条件1: 新高占比 > 2.2% 且 新低占比 > 2.2%
        # 条件2: 上证指数高于50日均线
        # 条件3: McClellan Oscillator代理为负（high120-low120扩大说明宽度收窄）
        cond1_new_high = high_pct > 2.2
        cond1_new_low = low_pct > 2.2
        cond2_above_ma = index_above_50ma
        cond3_mcclellan = mcclellan_proxy > 0  # 高低差扩大 = 宽度收窄
        
        # 宽松条件：满足新高+新低阈值 且 上证在50日线上
        # 或者：新高新低都>2% 且 McClellan代理为负
        if cond1_new_high and cond1_new_low and cond2_above_ma:
            hindenburg_triggered = True
            hindenburg_desc = f"新高{high_pct:.2f}%+新低{low_pct:.2f}%，指数在50日线上方"
        elif high_pct > 2.0 and low_pct > 2.0 and mcclellan_proxy > 0:
            hindenburg_triggered = True
            hindenburg_desc = f"宽度收窄，高低差扩大"
        else:
            hindenburg_triggered = False
            hindenburg_desc = "未触发"
        
        result = {
            'new_highs_count': high120,
            'new_lows_count': low120,
            'high_pct': round(high_pct, 2),
            'low_pct': round(low_pct, 2),
            'index_close': round(index_close, 2),
            'index_above_50ma': index_above_50ma,
            'mcclellan_proxy': mcclellan_proxy,
            'hindenburg_triggered': hindenburg_triggered,
            'hindenburg_desc': hindenburg_desc,
            'total_stocks': total_stocks,
        }
        
        print(f"  120日新高: {high120}只 ({high_pct:.2f}%)")
        print(f"  120日新低: {low120}只 ({low_pct:.2f}%)")
        print(f"  上证指数: {index_close:.2f}, 50日线上: {'是' if index_above_50ma else '否'}")
        print(f"  McClellan代理: {mcclellan_proxy}")
        print(f"  状态: {hindenburg_desc}")
        
        return result
        
    except Exception as e:
        print(f"  [错误] 兴登堡指标计算失败: {e}")
        return _default_hindenburg_result()


def _default_hindenburg_result():
    """返回默认兴登堡结果"""
    return {
        'new_highs_count': 0,
        'new_lows_count': 0,
        'high_pct': 0,
        'low_pct': 0,
        'index_close': 0,
        'index_above_50ma': False,
        'mcclellan_proxy': 0,
        'hindenburg_triggered': False,
        'hindenburg_desc': '数据获取失败',
        'total_stocks': 5300,
    }


# ============================================================
# 以下为原有函数（保持不变）
# ============================================================

def get_trade_dates(days=15):
    """获取近7个交易日（优先用AKShare交易日历，失败降级Tushare，最后用周历）"""
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')
    
    # 一级：DataHub 交易日历
    if dh is not None:
        try:
            _all = dh.get_trade_dates(start_date, end_date)
            if _all and len(_all) >= 7:
                _dates = sorted([str(x).replace('-', '') for x in _all])
                return _dates[-7:]
        except Exception as e:
            print(f"  [提示] DataHub交易日历获取失败: {e}")
    
    # 二级：Tushare交易日历
    try:
        df = pro.trade_cal(exchange='SSE', start_date=start_date, end_date=end_date, is_open='1')
        if df is not None and len(df) > 0 and 'cal_date' in df.columns:
            dates = df['cal_date'].tolist()
            if len(dates) >= 7:
                return dates[-7:]
    except Exception as e:
        print(f"  [提示] Tushare交易日历获取失败: {e}")
    
    # 三级：手动计算近7个交易日（跳过周末，不考虑节假日）
    dates = []
    current = datetime.now()
    while len(dates) < 7:
        if current.weekday() < 5:
            dates.append(current.strftime('%Y%m%d'))
        current -= timedelta(days=1)
    return sorted(dates)[-7:]


def get_shibor_7d():
    """获取SHIBOR 7天利率（DataHub 统一接口）"""
    try:
        r = dh.get_interest_rate('shibor') if dh else None
        if isinstance(r, dict):
            v = (r.get('latest') or {}).get('1W')
            if v is not None:
                return float(v)
    except Exception:
        pass
    return 1.65


def get_dr007():
    """获取DR007银行间7天回购利率（DataHub EDB: LIQ.CN.DR007）"""
    try:
        from data_hub.edb.api import get_many
        d = get_many(['LIQ.CN.DR007'])
        if d is not None and len(d) > 0 and 'value' in d.columns:
            v = pd.to_numeric(d['value'], errors='coerce').dropna()
            if len(v) > 0:
                return float(v.iloc[-1])
    except Exception:
        pass
    return get_shibor_7d()


def get_trading_volume(trade_date):
    """获取全A成交额（万亿）和成交额前5%股票占比"""
    try:
        df = dh.get_market_daily(trade_date=trade_date) if dh else None
        if df is not None and len(df) > 0 and 'amount' in df.columns:
            # 总成交额
            total_amount = df['amount'].sum()
            total_wanyi = total_amount / 1e9  # 千元 -> 万亿
            
            # 计算成交额前5%股票占比
            df_sorted = df.sort_values('amount', ascending=False)
            top_5pct_count = max(1, int(len(df) * 0.05))
            top_5pct_amount = df_sorted.head(top_5pct_count)['amount'].sum()
            concentration = (top_5pct_amount / total_amount * 100) if total_amount > 0 else 30
            
            if total_wanyi > 0.1:
                return round(total_wanyi, 2), round(concentration, 1)
        
        return 1.2, 30.0  # 默认值
    except Exception as e:
        return 1.2, 30.0


def get_turnover_rate(trade_date):
    """已废弃，保留兼容"""
    return 0


def get_limit_list_data(trade_date):
    """获取涨跌停家数"""
    try:
        # 使用涨跌停股池（DataHub 统一接口）
        _zt = dh.get_limit_pool('zt', trade_date=trade_date) if dh else None
        up_limit = len(_zt) if _zt is not None else 0

        _dt = dh.get_limit_pool('dt', trade_date=trade_date) if dh else None
        down_limit = len(_dt) if _dt is not None else 0
        
        if up_limit > 0 or down_limit > 0:
            return up_limit, down_limit
            
        # 备用方案：通过每日涨跌幅统计
        df = dh.get_market_daily(trade_date=trade_date) if dh else None
        if df is not None and len(df) > 0 and 'pct_chg' in df.columns:
            up_limit = len(df[df['pct_chg'] >= 9.5])
            down_limit = len(df[df['pct_chg'] <= -9.5])
            if up_limit > 0 or down_limit > 0:
                return up_limit, down_limit
        # 默认估算值
        return 65, 15
    except Exception as e:
        return 65, 15


def get_north_money(trade_date):
    """获取北向资金净流入"""
    try:
        # 使用沪深港通资金流向（DataHub 统一接口）
        df = dh.get_northbound_flow(lookback_days=5) if dh else None
        if df is not None and len(df) > 0:
            for col in ['north_money', 'north_net_mf', 'net_mf', 'north_net']:
                if col in df.columns:
                    v = pd.to_numeric(df[col], errors='coerce').dropna()
                    if len(v) > 0:
                        north_net = float(v.iloc[-1])
                        if abs(north_net) > 1:
                            return north_net / 1e8 if abs(north_net) > 1e6 else north_net
        # 默认估算值
        return 45
    except Exception as e:
        return 45


# ============================================================
# 两融余额监控模块
# ============================================================

MARGIN_CACHE_FILE = os.path.join(OUTPUT_DIR, 'margin_history.json')
MARGIN_ALERT_FILE = os.path.join(OUTPUT_DIR, 'margin_alerts.json')

# 告警阈值
MARGIN_CHANGE_ALERT_PCT = 50  # 日变动百分比 > 50% 告警（注意：实际两融日变幅通常<5%，50%是极端异常值）
MARGIN_PERCENTILE_HIGH = 80   # 高分位告警
MARGIN_PERCENTILE_LOW = 20    # 低分位告警
MARGIN_HISTORY_DAYS = 250     # 历史数据回溯天数（约1年交易日）


def load_margin_history():
    """加载两融历史数据缓存"""
    try:
        if os.path.exists(MARGIN_CACHE_FILE):
            with open(MARGIN_CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except:
        pass
    return []


def save_margin_history(history):
    """保存两融历史数据缓存（最多保留1000天）"""
    try:
        if len(history) > 1000:
            history = history[-1000:]
        os.makedirs(os.path.dirname(MARGIN_CACHE_FILE), exist_ok=True)
        with open(MARGIN_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  [WARN] 两融历史缓存保存失败: {e}")


def get_margin_trade_dates(lookback=10):
    """获取最近N个交易日（用于两融数据查询）"""
    try:
        # 用tushare交易日历
        today = datetime.now().strftime('%Y%m%d')
        start = (datetime.now() - timedelta(days=lookback * 2 + 10)).strftime('%Y%m%d')
        _tds = dh.get_trade_dates(start, today) if dh else None
        df = pd.DataFrame({'cal_date': _tds}) if _tds else None
        if df is not None and not df.empty:
            trading_days = df[df['is_open'] == 1]['cal_date'].tolist()
            trading_days.sort()
            return trading_days[-lookback:]
    except:
        pass
    # fallback: 生成最近10个工作日
    dates = []
    d = datetime.now()
    while len(dates) < lookback:
        if d.weekday() < 5:
            dates.append(d.strftime('%Y%m%d'))
        d -= timedelta(days=1)
    dates.reverse()
    return dates


def fetch_margin_balance_akshare(start_date=None, end_date=None):
    """
    两融余额统一入口（全系统唯一）。
    ------------------------------------------------------------
    统一走 data_hub 接口（四级降级：AKShare → Tushare → Coze网关 → Web），
    数据落库到 data_hub 两融历史库 margin_history.db，保证各报告口径一致。
    其它脚本需要两融数据时一律引用本入口，不要各自直连 akshare 采集。

    start_date/end_date: 'YYYYMMDD' 格式，默认最近 MARGIN_HISTORY_DAYS 天。
    返回: list[dict] - 按日期升序，字段 trade_date/rzye/rqye/rzrqye（单位：元）
    """
    try:
        if not end_date:
            end_date = datetime.now().strftime('%Y%m%d')
        if not start_date:
            start_date = (datetime.now() - timedelta(days=MARGIN_HISTORY_DAYS)).strftime('%Y%m%d')

        from data_hub.margin_db import sync_margin_history, query_margin

        # 1. 日常增量同步：只拉最近 10 天
        #    （akshare 深市是逐日调用，区间过长会超时降级；更早历史由
        #     margin_history.db 全量库承担，无需重复采集）
        try:
            sync_margin_history(lookback_days=10)
        except Exception as e:
            print(f"  [WARN] 两融增量同步失败（继续使用库内历史）: {e}")

        # 2. 从统一历史库读取
        df = query_margin(start_date=start_date, end_date=end_date)
        if df is None or len(df) == 0:
            print(f"  [WARN] 两融历史库无 {start_date} ~ {end_date} 数据")
            return []

        merged = []
        for _, row in df.iterrows():
            rzrqye = row.get('rzrqye')
            if pd.isna(rzrqye):
                continue
            rzye = row.get('rzye')
            rqye = row.get('rqye')
            merged.append({
                'trade_date': str(row['trade_date']).replace('-', '')[:8],
                'rzye': float(rzye) if pd.notna(rzye) else 0.0,
                'rqye': float(rqye) if pd.notna(rqye) else 0.0,
                'rzrqye': float(rzrqye),
            })
        merged.sort(key=lambda x: x['trade_date'])
        print(f"  [两融] data_hub 统一接口返回 {len(merged)} 天 ({start_date} ~ {end_date})")
        return merged
    except Exception as e:
        print(f"  [WARN] 两融数据获取失败: {e}")
        return []


def backfill_margin_history_if_needed(history):
    """
    如果历史数据不足MARGIN_HISTORY_DAYS天，回溯补全。
    只在首次或数据明显不足时触发深市逐天拉取（耗时操作）。
    """
    try:
        current_days = len(history)
        if current_days >= MARGIN_HISTORY_DAYS * 0.9:  # 已经有90%以上数据，不需要补
            return history
        
        print(f"  两融历史数据不足（{current_days}/{MARGIN_HISTORY_DAYS}天），开始回溯补全...")
        
        from datetime import datetime, timedelta
        end_date = datetime.now().strftime('%Y%m%d')
        start_date = (datetime.now() - timedelta(days=MARGIN_HISTORY_DAYS + 30)).strftime('%Y%m%d')  # 多取30天缓冲
        
        full_data = fetch_margin_balance_akshare(start_date=start_date, end_date=end_date)
        
        if not full_data:
            print(f"  [WARN] 回溯拉取失败，保留现有数据")
            return history
        
        # 合并：新数据优先（新数据可能更全）
        history_map = {h['trade_date']: h for h in history}
        for item in full_data:
            history_map[item['trade_date']] = item
        
        merged = sorted(history_map.values(), key=lambda x: x['trade_date'])
        
        # 只保留需要的天数
        if len(merged) > MARGIN_HISTORY_DAYS + 30:
            merged = merged[-(MARGIN_HISTORY_DAYS + 30):]
        
        print(f"  回溯完成，历史数据从 {current_days} 天 → {len(merged)} 天")
        return merged
    except Exception as e:
        print(f"  [WARN] 两融历史回溯失败: {e}")
        return history


def get_margin_balance_data():
    """
    获取两融余额数据（统一走 data_hub 两融历史库 margin_history.db）。
    返回: {'current': dict, 'history': list, 'percentiles': dict, 'changes': dict, 'data_points': int}
    """
    result = {
        'current': None,
        'history': [],
        'percentiles': {},
        'changes': {},
        'data_points': 0,
    }
    
    try:
        # 统一走 data_hub 两融历史库（内部含增量同步 + MARGIN_HISTORY_DAYS 窗口），
        # 不再维护本地 json 缓存与回溯逻辑，保证全系统同一份口径。
        history = fetch_margin_balance_akshare()
        if not history:
            return result

        result['history'] = history
        result['data_points'] = len(history)
        
        # 7. 取最新值
        latest = history[-1] if history else None
        if not latest:
            return result
        result['current'] = latest
        
        # 8. 计算变动（日、周、月、年）
        rzrq_series = [h['rzrqye'] for h in history if h.get('rzrqye')]
        if len(rzrq_series) >= 2:
            current_val = rzrq_series[-1]
            
            def pct_change(lookback_days):
                if len(rzrq_series) > lookback_days:
                    prev = rzrq_series[-lookback_days-1]
                    if prev > 0:
                        return round((current_val - prev) / prev * 100, 2)
                return None
            
            result['changes'] = {
                'day_pct': pct_change(1),
                'week_pct': pct_change(5),
                'month_pct': pct_change(22),
                'year_pct': pct_change(250),
            }
        
        # 9. 计算分位数（只要有20天以上就计算，不限制1年）
        if len(rzrq_series) >= 20:
            current_val = rzrq_series[-1]
            percentile = round(float(pd.Series(rzrq_series).rank(pct=True).iloc[-1]) * 100, 1)
            result['percentiles'] = {
                'rzrqye_percentile': percentile,
                'rzrqye_high': max(rzrq_series),
                'rzrqye_low': min(rzrq_series),
                'rzrqye_median': float(pd.Series(rzrq_series).median()),
            }
        
        return result
    except Exception as e:
        print(f"  [WARN] 两融余额数据获取失败: {e}")
        import traceback
        traceback.print_exc()
        return result


def check_margin_alerts(margin_data):
    """
    检查两融余额告警条件。
    返回: list[dict] - 告警列表
    """
    alerts = []
    if not margin_data or not margin_data.get('current'):
        return alerts
    
    current = margin_data['current']
    changes = margin_data.get('changes', {})
    percentiles = margin_data.get('percentiles', {})
    
    # 1. 日变动 > 50%（注意：正常两融日变幅<5%，50%通常是数据异常或极端行情）
    day_pct = changes.get('day_pct')
    if day_pct is not None and abs(day_pct) > MARGIN_CHANGE_ALERT_PCT:
        direction = "飙升" if day_pct > 0 else "暴跌"
        alerts.append({
            'type': 'change_alert',
            'level': 'critical',
            'title': f'⚠️ 两融余额{direction}',
            'message': f'两融余额日变动 {day_pct:+.2f}%，超过 {MARGIN_CHANGE_ALERT_PCT}% 告警阈值',
            'detail': f'当前两融余额: {current["rzrqye"]/1e12:.2f}万亿',
        })
    
    # 2. 历史分位数告警
    pct_val = percentiles.get('rzrqye_percentile')
    if pct_val is not None:
        if pct_val >= MARGIN_PERCENTILE_HIGH:
            alerts.append({
                'type': 'percentile_high',
                'level': 'warning',
                'title': f'🟠 两融余额处于高位（{pct_val}%分位）',
                'message': f'两融余额处于历史 {pct_val}% 分位，已超过 {MARGIN_PERCENTILE_HIGH}% 高警戒线',
                'detail': f'当前: {current["rzrqye"]/1e12:.2f}万亿 | 历史最高: {percentiles["rzrqye_high"]/1e12:.2f}万亿',
            })
        elif pct_val <= MARGIN_PERCENTILE_LOW:
            alerts.append({
                'type': 'percentile_low',
                'level': 'info',
                'title': f'🔵 两融余额处于低位（{pct_val}%分位）',
                'message': f'两融余额处于历史 {pct_val}% 分位，低于 {MARGIN_PERCENTILE_LOW}% 低警戒线',
                'detail': f'当前: {current["rzrqye"]/1e12:.2f}万亿 | 历史最低: {percentiles["rzrqye_low"]/1e12:.2f}万亿',
            })
    
    return alerts


def send_margin_alert_email(alerts, margin_data):
    """发送两融余额告警邮件"""
    try:
        import sys
        sys.path.insert(0, os.environ.get('CASE_WORKSPACE') or '/Coze/Drive/蓁宝/所有对话/主对话')
        from send_email import send_email_default
        
        if not alerts:
            return False
        
        today_str = datetime.now().strftime('%Y-%m-%d')
        subject = f"⚠️ 两融余额告警 - {today_str}"
        
        body_parts = []
        for alert in alerts:
            body_parts.append(f"<h3 style='color: {('#ff4444' if alert['level']=='critical' else '#ff9900' if alert['level']=='warning' else '#6699ff')};'>{alert['title']}</h3>")
            body_parts.append(f"<p><b>{alert['message']}</b></p>")
            if alert.get('detail'):
                body_parts.append(f"<p>{alert['detail']}</p>")
            body_parts.append("<br>")
        
        # 附加当前两融数据摘要
        current = margin_data.get('current', {})
        pct = margin_data.get('percentiles', {})
        if current:
            body_parts.append("<h4>📊 两融余额概要</h4>")
            body_parts.append(f"<p>日期: {current.get('trade_date', today_str)}</p>")
            body_parts.append(f"<p>两融总余额: {current.get('rzrqye', 0)/1e12:.2f}万亿</p>")
            body_parts.append(f"<p>融资余额: {current.get('rzye', 0)/1e12:.2f}万亿</p>")
            body_parts.append(f"<p>融券余额: {current.get('rqye', 0)/1e12:.2f}万亿</p>")
            if pct.get('rzrqye_percentile') is not None:
                body_parts.append(f"<p>历史分位: {pct['rzrqye_percentile']}%</p>")
        
        body = "\n".join(body_parts)
        send_email_default(subject=subject, body_html=body)
        print(f"  ✅ 两融余额告警邮件已发送（{len(alerts)}条）")
        return True
    except Exception as e:
        print(f"  [WARN] 两融告警邮件发送失败: {e}")
        return False


def get_margin_trend(margin_data):
    """判断两融余额趋势（用于风险偏好评分）"""
    history = margin_data.get('history', [])
    if len(history) < 5:
        return 'stable', 7
    
    rzrq_series = [h['rzrqye'] for h in history if h.get('rzrqye')]
    if len(rzrq_series) < 3:
        return 'stable', 7
    
    recent = rzrq_series[-5:]
    # 3日趋势
    if len(recent) >= 3:
        last3 = recent[-3:]
        if all(last3[i] < last3[i+1] for i in range(len(last3)-1)):
            return 'increasing', 10  # 两融增加=杠杆提升=风险偏好提升
        elif all(last3[i] > last3[i+1] for i in range(len(last3)-1)):
            return 'decreasing', 3  # 两融减少=去杠杆=风险偏好降低
    
    return 'stable', 7


def get_margin_data(trade_dates):
    """获取融资余额变化（兼容旧接口，返回trend, score）"""
    try:
        margin_data = get_margin_balance_data()
        return get_margin_trend(margin_data)
    except:
        return 'stable', 7


def calculate_liquidity_score(rate_7d, volume):
    """计算流动性评分（满分60分）
    - DR007/SHIBOR007: 30分（利率低=流动性好）
    - 全A成交额: 30分
    """
    score = 0
    
    # DR007/SHIBOR007 7天利率 (30分，利率越低分越高)
    if rate_7d < 1.5:
        score += 30
    elif 1.5 <= rate_7d < 1.8:
        score += 24
    elif 1.8 <= rate_7d < 2.0:
        score += 18
    elif 2.0 <= rate_7d < 2.5:
        score += 12
    else:
        score += 6
    
    # 全A成交额 (30分)
    if volume > 1.5:
        score += 30
    elif 1.2 <= volume <= 1.5:
        score += 24
    elif 0.8 <= volume < 1.2:
        score += 18
    elif 0.5 <= volume < 0.8:
        score += 12
    else:
        score += 6
    
    return score


def calculate_risk_appetite_score(concentration, up_limit, down_limit, north_money):
    """计算风险偏好评分（满分40分）
    - 成交额前5%股票占比: 18分（占比低=风险偏好高，资金分散）
    - 涨停家数: 8分
    - 跌停家数: 7分（反向）
    - 北向资金: 7分
    """
    score = 0
    
    # 成交额前5%股票占比 (18分，占比越低分越高，说明资金分散、风险偏好高)
    if concentration < 20:
        score += 18  # 极度分散，风险偏好极高
    elif 20 <= concentration < 25:
        score += 15  # 较分散
    elif 25 <= concentration < 30:
        score += 12  # 正常
    elif 30 <= concentration < 35:
        score += 9   # 略集中
    elif 35 <= concentration < 40:
        score += 6   # 较集中
    else:
        score += 3   # 高度集中，风险偏好低
    
    # 涨停家数 (8分)
    if up_limit > 100:
        score += 8
    elif 60 <= up_limit <= 100:
        score += 6
    elif 30 <= up_limit < 60:
        score += 4
    else:
        score += 2
    
    # 跌停家数 (7分, 反向)
    if down_limit < 5:
        score += 7
    elif 5 <= down_limit < 15:
        score += 5
    elif 15 <= down_limit < 30:
        score += 3
    else:
        score += 1
    
    # 北向资金 (7分)
    if north_money > 80:
        score += 7
    elif 0 <= north_money <= 80:
        score += 5
    elif -50 <= north_money < 0:
        score += 3
    else:
        score += 1
    
    return score


def get_indicator_details(trade_date):
    """获取单日指标详情"""
    rate_7d = get_shibor_7d()
    volume, concentration = get_trading_volume(trade_date)
    up_limit, down_limit = get_limit_list_data(trade_date)
    north_money = get_north_money(trade_date)
    
    return {
        'rate_7d': rate_7d,
        'volume': volume,
        'concentration': concentration,
        'up_limit': up_limit,
        'down_limit': down_limit,
        'north_money': north_money
    }


# ============================================================
# 期权IV均线监控模块
# ============================================================

# IV监控标的：科创50ETF + 创业板ETF
IV_MA_TARGETS = [
    {"name": "科创50ETF", "code": "588000", "exchange": "SSE", "opt_code": "OP588000.SH",
     "ts_code_fund": "588000.SH", "index_code": "000688.SH"},
    {"name": "创业板ETF", "code": "159915", "exchange": "SZSE", "opt_code": "OP159915.SZ",
     "ts_code_fund": "159915.SZ", "index_code": "399006.SZ"},
]

IV_MA_LOOKBACK = 180  # 回溯交易日数（确保>=120以计算MA80）


def _bs_iv_newton(S, K, T, r, mkt_price, is_call=True):
    """牛顿法反推IV，失败返回0"""
    from math import log, sqrt, exp
    from scipy.stats import norm
    if T <= 0 or mkt_price <= 0 or S <= 0 or K <= 0:
        return 0.0
    intrinsic = max(S - K, 0) if is_call else max(K - S, 0)
    if mkt_price < intrinsic - 1e-6:
        return 0.0
    sigma = 0.3
    for _ in range(100):
        d1 = (log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrt(T))
        d2 = d1 - sigma * sqrt(T)
        if is_call:
            price = S * norm.cdf(d1) - K * exp(-r * T) * norm.cdf(d2)
        else:
            price = K * exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
        diff = mkt_price - price
        if abs(diff) < 1e-6:
            return sigma
        vega = S * sqrt(T) * norm.pdf(d1)
        if vega < 1e-10:
            break
        sigma = sigma + diff / vega
        if sigma < 0.001:
            sigma = 0.001
        if sigma > 5.0:
            sigma = 5.0
    return max(sigma, 0.0) if 0.01 < sigma < 3.0 else 0.0


def _get_fund_daily(ts_code, start_date, end_date):
    """获取ETF日线数据（tushare fund_daily）"""
    import time
    for retry in range(3):
        try:
            time.sleep(0.15 * (retry + 1))
            df = dh.get_etf_daily(ts_code=ts_code, start_date=start_date, end_date=end_date) if dh else None
            if df is not None and len(df) > 0:
                df = df.sort_values('trade_date').reset_index(drop=True)
                return df
        except Exception as e:
            err_str = str(e)
            if '数量超限' in err_str or '频率' in err_str:
                print(f"  [IV] fund_daily限流(重试{retry+1}/3): {ts_code}")
                time.sleep(2)
                continue
            print(f"  [IV] fund_daily失败 {ts_code}: {e}")
            break
    return None


def _get_index_daily(ts_code, start_date, end_date):
    """获取指数日线数据（tushare index_daily）"""
    import time
    for retry in range(3):
        try:
            time.sleep(0.15 * (retry + 1))
            df = dh.get_index_daily(ts_code=ts_code, start_date=start_date, end_date=end_date) if dh else None
            if df is not None and len(df) > 0:
                df = df.sort_values('trade_date').reset_index(drop=True)
                return df
        except Exception as e:
            err_str = str(e)
            if '数量超限' in err_str or '频率' in err_str:
                print(f"  [IV] index_daily限流(重试{retry+1}/3): {ts_code}")
                time.sleep(2)
                continue
            print(f"  [IV] index_daily失败 {ts_code}: {e}")
            break
    return None


def _calc_atm_iv_from_contracts(contracts_df, spot_price, trade_date, r=0.025):
    """从单日期权合约数据计算ATM IV
    取最近到期月份（>=3天DTE）的平值附近看涨+看跌IV均值
    """
    from datetime import datetime as dt
    trade_dt = dt.strptime(trade_date, '%Y%m%d')

    # 过滤有有效价格的合约
    valid = contracts_df[(contracts_df['close'] > 0) & (contracts_df['exercise_price'] > 0)].copy()
    if len(valid) < 4:
        return 0.0

    # 计算每个合约到期日，找最近到期（>=3天DTE）
    valid['maturity_date'] = valid['maturity_date'].astype(str)
    valid['dte'] = valid['maturity_date'].apply(
        lambda x: max(0, (dt.strptime(x[:8], '%Y%m%d') - trade_dt).days))
    near_expiry = valid[valid['dte'] >= 3]
    if near_expiry.empty:
        near_expiry = valid[valid['dte'] >= 1]
    if near_expiry.empty:
        return 0.0

    nearest_exp = near_expiry['dte'].min()
    near_month = near_expiry[near_expiry['dte'] == nearest_exp]
    T = nearest_exp / 365.0

    # 找ATM行权价（离spot最近）
    strikes = near_month['exercise_price'].unique()
    if len(strikes) == 0:
        return 0.0
    atm_strike = min(strikes, key=lambda k: abs(k - spot_price))

    # 在ATM附近取2档（更稳健）
    sorted_strikes = sorted(strikes)
    atm_idx = sorted_strikes.index(atm_strike)
    lo = max(0, atm_idx - 1)
    hi = min(len(sorted_strikes), atm_idx + 2)
    near_strikes = sorted_strikes[lo:hi]

    call_ivs = []
    put_ivs = []
    for K in near_strikes:
        # call
        call_row = near_month[(near_month['call_put'] == 'C') & (near_month['exercise_price'] == K)]
        if len(call_row) > 0:
            iv = _bs_iv_newton(spot_price, K, T, r, float(call_row.iloc[0]['close']), is_call=True)
            if iv > 0.01:
                call_ivs.append(iv)
        # put
        put_row = near_month[(near_month['call_put'] == 'P') & (near_month['exercise_price'] == K)]
        if len(put_row) > 0:
            iv = _bs_iv_newton(spot_price, K, T, r, float(put_row.iloc[0]['close']), is_call=False)
            if iv > 0.01:
                put_ivs.append(iv)

    all_ivs = call_ivs + put_ivs
    if len(all_ivs) >= 2:
        return sum(all_ivs) / len(all_ivs)
    elif len(all_ivs) == 1:
        return all_ivs[0]
    return 0.0


def get_iv_ma_from_tushare(target, start_date, end_date):
    """ATM IV 历史（DataHub 单一真源 data_hub.get_option_iv_history）

    口径说明：DataHub 返回百分比（25.3=25.3%），本函数统一转小数（0.253），
    与旧 tushare 自算口径保持一致，下游 ×100 展示不受影响。

    Returns:
        DataFrame with columns [trade_date, atm_iv] 或 None
    """
    name = target.get('name', '?')
    fund_code = str(target.get('ts_code_fund', '') or '')
    underlying = fund_code.split('.')[0].strip()

    if not underlying:
        print(f"  [IV-DataHub] {name} 缺少标的代码(ts_code_fund)")
        return None
    if dh is None:
        print(f"  [IV-DataHub] {name} DataHub 不可用")
        return None

    try:
        ser = dh.get_option_iv_history(underlying, start_date, end_date)
        if ser is None or len(ser) == 0:
            print(f"  [IV-DataHub] {name} IV历史为空")
            return None

        df = ser.reset_index()
        df.columns = ['trade_date', 'atm_iv']
        df['trade_date'] = df['trade_date'].astype(str).str.replace('-', '')
        # 百分比 → 小数，保持下游口径
        df['atm_iv'] = pd.to_numeric(df['atm_iv'], errors='coerce') / 100.0
        df = df.dropna(subset=['atm_iv'])
        df = df.sort_values('trade_date').reset_index(drop=True)

        if len(df) == 0:
            print(f"  [IV-DataHub] {name} IV清洗后为空")
            return None

        print(f"  [IV-DataHub] {name} 有效IV日数: {len(df)}, 最新IV={df['atm_iv'].iloc[-1] * 100:.2f}%")
        return df
    except Exception as e:
        print(f"  [IV-DataHub] {name} 失败: {e}")
        return None


def get_iv_ma_from_rv(target, start_date, end_date):
    """降级方案：用指数已实现波动率(RV)代替期权IV
    使用20日RV，计算RV的MA40/MA80
    数据源：tushare index_daily → akshare指数行情
    """
    name = target['name']
    idx_code = target['index_code']

    df = _get_index_daily(idx_code, start_date, end_date)

    # tushare失败，尝试akshare
    if df is None or len(df) < 100:
        print(f"  [IV-RV降级] 尝试akshare指数行情备用...")
        try:
            ak_df = dh.get_index_daily(ts_code=idx_code, start_date=start_date,
                                       end_date=end_date) if dh else None
            if ak_df is not None and len(ak_df) > 100:
                ak_df = ak_df.copy()
                ak_df['trade_date'] = ak_df['trade_date'].astype(str).str.replace('-', '')
                ak_df = ak_df.sort_values('trade_date').reset_index(drop=True)
                ak_df['pct_chg'] = ak_df['close'].pct_change() * 100
                df = ak_df[['trade_date', 'pct_chg', 'close']].copy()
                df = df.sort_values('trade_date').reset_index(drop=True)
                print(f"  [IV-RV降级] DataHub获取{name} {len(df)}天数据")
        except Exception as e:
            print(f"  [IV-RV降级] akshare备用失败: {e}")

    if df is None or len(df) < 100:
        print(f"  [IV-RV降级] {name} 指数日线不足({len(df) if df is not None else 0}天)")
        return None

    df['pct_chg'] = df['pct_chg'].astype(float)
    df['ret'] = df['pct_chg'] / 100.0
    df['rv20'] = df['ret'].rolling(window=20).std() * np.sqrt(252)

    df_valid = df.dropna(subset=['rv20']).copy()
    if len(df_valid) < 80:
        print(f"  [IV-RV降级] {name} 有效RV日数不足: {len(df_valid)}")
        return None

    iv_df = df_valid[['trade_date', 'rv20']].rename(columns={'rv20': 'atm_iv'}).reset_index(drop=True)
    iv_df['trade_date'] = iv_df['trade_date'].astype(str)
    print(f"  [IV-RV降级] {name} RV代替IV, 有效日数: {len(iv_df)}, 最新={iv_df['atm_iv'].iloc[-1]*100:.2f}%")
    return iv_df


def calculate_iv_ma_data():
    """计算期权IV均线监控数据
    标的：科创50ETF(588000)、创业板ETF(159915)
    数据源优先级：Tushare期权日线 → RV降级

    Returns:
        list of dict: [
            {
                'name': 标的名,
                'current_iv': 当前IV(%),
                'ma40': MA40(%),
                'ma80': MA80(%),
                'stars': 星级(0/1/2),
                'signal': 信号描述,
                'data_source': 数据源(tushare/rv/missing),
                'data_days': 有效数据天数,
            }
        ]
    """
    print("\n--- 计算期权IV均线监控 ---")

    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=IV_MA_LOOKBACK * 2)).strftime('%Y%m%d')

    results = []

    for target in IV_MA_TARGETS:
        name = target['name']
        print(f"\n  [{name}]")

        iv_df = None
        data_source = 'missing'

        # 1. Tushare主链路
        try:
            iv_df = get_iv_ma_from_tushare(target, start_date, end_date)
            if iv_df is not None and len(iv_df) >= 80:
                data_source = 'tushare'
        except Exception as e:
            print(f"  [IV-Tushare] {name} 异常: {e}")

        # 2. RV降级
        if iv_df is None or len(iv_df) < 80:
            print(f"  [降级] 切换到RV代替IV...")
            try:
                iv_df = get_iv_ma_from_rv(target, start_date, end_date)
                if iv_df is not None and len(iv_df) >= 80:
                    data_source = 'rv'
            except Exception as e:
                print(f"  [IV-RV降级] {name} 异常: {e}")

        if iv_df is None or len(iv_df) < 80:
            print(f"  [缺失] {name} 数据不足，标记为缺失")
            results.append({
                'name': name,
                'current_iv': None,
                'ma40': None,
                'ma80': None,
                'stars': 0,
                'signal': '数据缺失',
                'data_source': 'missing',
                'data_days': 0,
            })
            continue

        # 计算MA40 / MA80
        iv_df['ma40'] = iv_df['atm_iv'].rolling(40, min_periods=30).mean()
        iv_df['ma80'] = iv_df['atm_iv'].rolling(80, min_periods=60).mean()

        latest = iv_df.iloc[-1]
        current_iv = float(latest['atm_iv']) * 100  # 转百分比
        ma40 = float(latest['ma40']) * 100 if pd.notna(latest['ma40']) else None
        ma80 = float(latest['ma80']) * 100 if pd.notna(latest['ma80']) else None

        # 信号判断
        if ma80 is not None and current_iv > ma80:
            stars = 2
            signal = '★★ 极度超买/恐慌'
        elif ma40 is not None and current_iv > ma40:
            stars = 1
            signal = '★ 偏强'
        else:
            stars = 0
            signal = '平稳'

        result = {
            'name': name,
            'current_iv': round(current_iv, 2),
            'ma40': round(ma40, 2) if ma40 else None,
            'ma80': round(ma80, 2) if ma80 else None,
            'stars': stars,
            'signal': signal,
            'data_source': data_source,
            'data_days': len(iv_df),
        }
        results.append(result)

        src_label = {'tushare': 'Tushare期权IV', 'rv': 'RV代替(降级)'}
        print(f"  ✅ {name}: IV={current_iv:.2f}% MA40={ma40:.2f}% MA80={ma80:.2f}% {signal} [{src_label.get(data_source, data_source)}]")

    return results


# ============================================================
# 期权IV均线可视化工具
# ============================================================

def _iv_bar_visual(current_iv, ma40, ma80, width=100):
    """生成文字版IV均线横线可视化
    竖轴上下：值越大越靠上（位置越高）

    Returns:
        str: 多行文字版可视化
    """
    if current_iv is None or ma40 is None or ma80 is None:
        return "  数据不足，无法展示"

    values = [current_iv, ma40, ma80]
    vmin = min(values)
    vmax = max(values)
    vrange = vmax - vmin if vmax > vmin else 1.0
    # 留10%边距
    vmin_pad = vmin - vrange * 0.15
    vmax_pad = vmax + vrange * 0.15
    vrange_pad = vmax_pad - vmin_pad

    # 相对位置(0=底部, 1=顶部)
    def pos(v):
        return (v - vmin_pad) / vrange_pad

    # 构建行：从上到下，共7行
    rows = []
    bar_len = width  # 横线长度（字符数）

    # 定义每条线的标签和位置
    lines = [
        ('IV ', current_iv, '─' * bar_len),
        ('MA40', ma40, '─' * bar_len),
        ('MA80', ma80, '─' * bar_len),
    ]
    # 按位置从高到低排序
    lines_sorted = sorted(lines, key=lambda x: pos(x[1]), reverse=True)

    # 生成可视化：每条线占一行，左侧标注标签+值
    for label, val, bar in lines_sorted:
        star = ''
        if label.startswith('IV'):
            # 判断星级标记
            if ma80 is not None and val > ma80:
                star = ' ★★'
            elif ma40 is not None and val > ma40:
                star = ' ★'
        rows.append(f"  {label:>4} {val:>6.2f}% │{bar}│{star}")

    return '\n'.join(rows)


def get_vix_percentile():
    """计算各指数20日已实现波动率(VIX代理)及近一年分位数
    指数: 沪深300、中证500、科创50、中证1000、创业板指
    分位≥80% → 🔴高波预警; 分位≤20% → 🔴低波预警
    """
    indices = {
        '沪深300': '000300.SH',
        '中证500': '000905.SH',
        '科创50': '000688.SH',
        '中证1000': '000852.SH',
        '创业板指': '399006.SZ',
    }
    
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=500)).strftime('%Y%m%d')
    
    results = []
    
    for name, ts_code in indices.items():
        try:
            df = dh.get_index_daily(ts_code=ts_code, start_date=start_date, end_date=end_date) if dh else None
            if df is None or len(df) < 60:
                print(f"  [警告] {name}({ts_code}) 数据不足, 跳过")
                continue
            
            df = df.sort_values('trade_date').reset_index(drop=True)
            df['pct_chg'] = df['pct_chg'].astype(float)
            
            # 20日滚动已实现波动率 (年化)
            # tushare pct_chg已是百分比(如0.0925表示0.0925%), 需先除100转小数
            window = 20
            df['ret'] = df['pct_chg'] / 100.0
            df['rv20'] = df['ret'].rolling(window=window).std() * np.sqrt(252) * 100
            
            # 取近252个交易日的rv20用于分位计算
            rv_series = df['rv20'].dropna().tail(252)
            if len(rv_series) < 60:
                print(f"  [警告] {name} 有效波动率数据不足, 跳过")
                continue
            
            current_rv = rv_series.iloc[-1]
            percentile = (rv_series < current_rv).sum() / len(rv_series) * 100
            
            # 报警判断
            if percentile >= 80:
                alert = '🔴高波预警'
                alert_type = 'high'
            elif percentile <= 20:
                alert = '🔴低波预警'
                alert_type = 'low'
            else:
                alert = '🟢正常'
                alert_type = 'normal'
            
            results.append({
                'name': name,
                'ts_code': ts_code,
                'vix': round(current_rv, 2),
                'percentile': round(percentile, 1),
                'alert': alert,
                'alert_type': alert_type,
            })
            print(f"  {name}: VIX={current_rv:.2f}%, 分位={percentile:.1f}%, {alert}")
            
        except Exception as e:
            print(f"  [错误] {name}({ts_code}): {e}")
            continue
    
    return results


def generate_html_report(daily_data, dates, vix_data=None, ar_data=None, hindenburg_data=None, iv_ma_data=None, margin_data=None):
    """生成HTML报告"""
    dates_str = [datetime.strptime(d, '%Y%m%d').strftime('%m-%d') for d in dates]
    liquidity_scores = [d['liquidity_score'] for d in daily_data]
    risk_scores = [d['risk_score'] for d in daily_data]
    
    # 计算平均分
    avg_liquidity = sum(liquidity_scores) / len(liquidity_scores)
    avg_risk = sum(risk_scores) / len(risk_scores)
    
    # 评级
    def get_rating(score, total):
        ratio = score / total * 100
        if ratio >= 80:
            return '🟢 宽松/乐观'
        elif ratio >= 60:
            return '🟡 适中'
        else:
            return '🔴 收紧/谨慎'
    
    today = datetime.now().strftime('%Y-%m-%d')
    
    # AR表格区块
    ar_table_html = ''
    if ar_data:
        signal_color = '#ff6b6b' if ar_data['final_signal'] == 1 else '#00ff88' if ar_data['final_signal'] == -1 else '#ffcc00'
        ar_table_html = f'''
        <div class="indicator-table">
            <h3 style="color: #00d4ff; margin-bottom: 15px;">📊 吸收率因子 (Absorption Ratio)</h3>
            <table>
                <thead>
                    <tr>
                        <th>指标</th>
                        <th>数值</th>
                        <th>说明</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td>当前AR</td>
                        <td>{ar_data['ar']:.4f}</td>
                        <td>前3主成分解释方差比例</td>
                    </tr>
                    <tr>
                        <td>AR_5日均值</td>
                        <td>{ar_data['ar_5d']:.4f}</td>
                        <td>短期趋势</td>
                    </tr>
                    <tr>
                        <td>DAR_long</td>
                        <td style="color: {'#ff6b6b' if ar_data['dar_long'] > 1 else '#00ff88' if ar_data['dar_long'] < -1 else '#ffcc00'};">{ar_data['dar_long']:.2f}</td>
                        <td>偏离250日均值标准差</td>
                    </tr>
                    <tr>
                        <td>DAR_short</td>
                        <td style="color: {'#ff6b6b' if ar_data['dar_short'] > 1 else '#00ff88' if ar_data['dar_short'] < -1 else '#ffcc00'};">{ar_data['dar_short']:.2f}</td>
                        <td>偏离125日均值标准差</td>
                    </tr>
                    <tr>
                        <td>PC1解释方差</td>
                        <td>{ar_data['pc1_explained']:.2f}%</td>
                        <td>第一主成分</td>
                    </tr>
                    <tr>
                        <td>PC3累计方差</td>
                        <td>{ar_data['pc3_explained']:.2f}%</td>
                        <td>前3主成分累计</td>
                    </tr>
                    <tr>
                        <td>有效行业数</td>
                        <td>{ar_data['n_industries']}</td>
                        <td>申万一级行业</td>
                    </tr>
                    <tr>
                        <td style="font-weight: bold;">最终信号</td>
                        <td style="font-weight: bold; color: {signal_color}; font-size: 18px;">{ar_data['signal_desc']}</td>
                        <td>风险共振状态</td>
                    </tr>
                </tbody>
            </table>
            <div style="margin-top: 15px; padding: 12px; background: rgba(0,0,0,0.3); border-radius: 8px;">
                <p style="color: #888; font-size: 12px; line-height: 1.6;">
                    <strong style="color: #00d4ff;">AR含义</strong>：AR越高，市场集中度越高，系统性风险越大；AR越低，资金分散，风险偏好高<br>
                    <strong style="color: #ff6b6b;">🔴 AR尖峰</strong>：DAR>1，预示市场可能见顶 | <strong style="color: #00ff88;">🟢 AR低谷</strong>：DAR<-1，预示市场可能见底
                </p>
            </div>
        </div>
        '''
    
    # 兴登堡表格区块
    hindenburg_table_html = ''
    if hindenburg_data:
        triggered_color = '#ff6b6b' if hindenburg_data['hindenburg_triggered'] else '#00ff88'
        hindenburg_table_html = f'''
        <div class="indicator-table">
            <h3 style="color: #00d4ff; margin-bottom: 15px;">⚠️ 兴登堡指标 (Hindenburg Omen)</h3>
            <table>
                <thead>
                    <tr>
                        <th>指标</th>
                        <th>数值</th>
                        <th>说明</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td>120日新高</td>
                        <td>{hindenburg_data['new_highs_count']} 只</td>
                        <td>占全市场 {hindenburg_data['high_pct']:.2f}%</td>
                    </tr>
                    <tr>
                        <td>120日新低</td>
                        <td>{hindenburg_data['new_lows_count']} 只</td>
                        <td>占全市场 {hindenburg_data['low_pct']:.2f}%</td>
                    </tr>
                    <tr>
                        <td>上证指数</td>
                        <td>{hindenburg_data['index_close']:.2f}</td>
                        <td>50日均线: {'✅ 上方' if hindenburg_data['index_above_50ma'] else '❌ 下方'}</td>
                    </tr>
                    <tr>
                        <td>宽度代理指标</td>
                        <td>{hindenburg_data['mcclellan_proxy']}</td>
                        <td>high120-low120变化</td>
                    </tr>
                    <tr>
                        <td style="font-weight: bold;">触发状态</td>
                        <td style="font-weight: bold; color: {triggered_color}; font-size: 18px;">{'⚠️ 警告' if hindenburg_data['hindenburg_triggered'] else '✅ 正常'}</td>
                        <td>{hindenburg_data['hindenburg_desc']}</td>
                    </tr>
                </tbody>
            </table>
            <div style="margin-top: 15px; padding: 12px; background: rgba(0,0,0,0.3); border-radius: 8px;">
                <p style="color: #888; font-size: 12px; line-height: 1.6;">
                    <strong style="color: #ff6b6b;">兴登堡条件</strong>：新高占比>2.2% + 新低占比>2.2% + 指数在50日线上方 → 市场可能见顶信号<br>
                    <strong style="color: #ffcc00;">注意</strong>：该指标仅作为参考，需结合其他指标综合判断
                </p>
            </div>
        </div>
        '''

    # 两融余额监控区块
    margin_html = ''
    if margin_data and margin_data.get('current'):
        mc = margin_data['current']
        mchanges = margin_data.get('changes', {})
        mpercentiles = margin_data.get('percentiles', {})
        mdata_points = margin_data.get('data_points', 0)

        def _fmt_pct(val):
            if val is None:
                return '--'
            color = '#ff6b6b' if val > 0 else '#00ff88' if val < 0 else '#888'
            return f'<span style="color:{color}">{val:+.2f}%</span>'

        def _fmt_trillion(val):
            if val is None or val == 0:
                return '--'
            return f'{val/1e12:.3f}万亿'

        # 分位数进度条
        pct_val = mpercentiles.get('rzrqye_percentile')
        pct_bar_html = ''
        if pct_val is not None:
            pct_color = '#ff6b6b' if pct_val >= 80 else '#ffcc00' if pct_val >= 50 else '#00ff88'
            pct_bar_html = f'''
                <div style="margin-top: 8px;">
                    <div style="display: flex; justify-content: space-between; font-size: 12px; color: #888; margin-bottom: 4px;">
                        <span>历史低位 (20%)</span>
                        <span style="color: {pct_color}; font-weight: bold;">分位 {pct_val}%</span>
                        <span>历史高位 (80%)</span>
                    </div>
                    <div style="height: 10px; background: rgba(255,255,255,0.1); border-radius: 5px; position: relative; overflow: hidden;">
                        <div style="position: absolute; left: 0; top: 0; bottom: 0; width: {pct_val}%; background: linear-gradient(90deg, #00ff88, #ffcc00, #ff6b6b); border-radius: 5px;"></div>
                        <div style="position: absolute; left: 20%; top: -2px; bottom: -2px; width: 2px; background: rgba(255,255,255,0.5);"></div>
                        <div style="position: absolute; left: 80%; top: -2px; bottom: -2px; width: 2px; background: rgba(255,255,255,0.5);"></div>
                    </div>
                </div>
            '''

        # 告警状态
        margin_alerts = check_margin_alerts(margin_data)
        alert_html = ''
        if margin_alerts:
            alert_badges = []
            for a in margin_alerts:
                badge_color = '#ff4444' if a['level'] == 'critical' else '#ff9900' if a['level'] == 'warning' else '#6699ff'
                alert_badges.append(f'<span style="display:inline-block;padding:3px 10px;background:{badge_color};color:#fff;border-radius:12px;font-size:12px;margin-right:6px;">{a["title"]}</span>')
            alert_html = f'<div style="margin-top:12px;">{" ".join(alert_badges)}</div>'

        margin_html = f'''
        <div class="indicator-table">
            <h3 style="color: #00d4ff; margin-bottom: 15px;">💰 融资融券余额监控</h3>
            <table>
                <thead>
                    <tr>
                        <th>指标</th>
                        <th>当前值</th>
                        <th>日变动</th>
                        <th>周变动</th>
                        <th>月变动</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td>融资余额</td>
                        <td>{_fmt_trillion(mc.get('rzye'))}</td>
                        <td>{_fmt_pct(mchanges.get('day_pct'))}</td>
                        <td>{_fmt_pct(mchanges.get('week_pct'))}</td>
                        <td>{_fmt_pct(mchanges.get('month_pct'))}</td>
                    </tr>
                    <tr>
                        <td>融券余额</td>
                        <td>{_fmt_trillion(mc.get('rqye'))}</td>
                        <td>--</td>
                        <td>--</td>
                        <td>--</td>
                    </tr>
                    <tr style="background: rgba(0, 212, 255, 0.05);">
                        <td style="font-weight: bold;">两融总余额</td>
                        <td style="font-weight: bold; font-size: 16px;">{_fmt_trillion(mc.get('rzrqye'))}</td>
                        <td style="font-weight: bold;">{_fmt_pct(mchanges.get('day_pct'))}</td>
                        <td style="font-weight: bold;">{_fmt_pct(mchanges.get('week_pct'))}</td>
                        <td style="font-weight: bold;">{_fmt_pct(mchanges.get('month_pct'))}</td>
                    </tr>
                </tbody>
            </table>
            {pct_bar_html}
            <div style="margin-top: 10px; display: flex; justify-content: space-between; font-size: 12px; color: #888;">
                <span>统计区间: 近{mdata_points}天</span>
                <span>历史最高: {_fmt_trillion(mpercentiles.get('rzrqye_high'))} | 最低: {_fmt_trillion(mpercentiles.get('rzrqye_low'))}</span>
            </div>
            {alert_html}
            <div style="margin-top: 12px; padding: 10px; background: rgba(0,0,0,0.3); border-radius: 8px;">
                <p style="color: #888; font-size: 12px; line-height: 1.6;">
                    <strong style="color: #ff6b6b;">告警阈值</strong>：日变动 &gt; 50% | 历史分位 &gt; 80% 或 &lt; 20%<br>
                    <strong style="color: #ffcc00;">说明</strong>：数据每日累积到本地缓存，分位数随数据量增加而更准确
                </p>
            </div>
        </div>
        '''

    # 期权IV均线监控区块
    iv_ma_html = ''
    if iv_ma_data:
        iv_rows_html = ''
        for iv in iv_ma_data:
            if iv['data_source'] == 'missing' or iv['current_iv'] is None:
                iv_rows_html += f'''
                    <tr>
                        <td>{iv['name']}</td>
                        <td colspan="3" style="color: #888;">数据缺失</td>
                        <td>数据缺失</td>
                    </tr>'''
                continue

            cur = iv['current_iv']
            m40 = iv['ma40']
            m80 = iv['ma80']
            stars = iv['stars']
            signal = iv['signal']
            src = iv['data_source']
            src_label = '期权IV' if src == 'tushare' else 'RV代替' if src == 'rv' else '未知'

            # 计算相对位置（用于横线可视化）
            vals = [v for v in [cur, m40, m80] if v is not None]
            vmin = min(vals)
            vmax = max(vals)
            vrange = vmax - vmin if vmax > vmin else 1.0
            vmin_pad = vmin - vrange * 0.2
            vmax_pad = vmax + vrange * 0.2
            vrange_pad = vmax_pad - vmin_pad

            def pct(v):
                return max(0, min(100, (v - vmin_pad) / vrange_pad * 100))

            iv_pos = pct(cur)
            ma40_pos = pct(m40) if m40 else 0
            ma80_pos = pct(m80) if m80 else 0

            star_str = '★' * stars if stars > 0 else ''
            iv_color = '#ff6b6b' if stars >= 2 else '#ffcc00' if stars >= 1 else '#00ff88'
            signal_color = iv_color

            # 横线可视化：用一个容器，内有3条横线，通过top定位显示相对高低
            vis_html = f'''
                <div style="position: relative; width: 200px; height: 80px; margin: 0 auto; border-left: 2px solid rgba(255,255,255,0.2); border-right: 2px solid rgba(255,255,255,0.2);">
                    <!-- IV 线 -->
                    <div style="position: absolute; left: 0; right: 0; top: {100 - iv_pos}%; height: 3px; background: {iv_color}; transform: translateY(-50%); border-radius: 2px;">
                        <span style="position: absolute; right: -80px; top: 50%; transform: translateY(-50%); color: {iv_color}; font-size: 12px; white-space: nowrap;">IV {cur:.2f}% {star_str}</span>
                    </div>
                    <!-- MA40 线 -->
                    <div style="position: absolute; left: 0; right: 0; top: {100 - ma40_pos}%; height: 2px; background: #00d4ff; transform: translateY(-50%); opacity: 0.8;">
                        <span style="position: absolute; left: -80px; top: 50%; transform: translateY(-50%); color: #00d4ff; font-size: 11px; white-space: nowrap;">MA40 {m40:.2f}%</span>
                    </div>
                    <!-- MA80 线 -->
                    <div style="position: absolute; left: 0; right: 0; top: {100 - ma80_pos}%; height: 2px; background: #9b59b6; transform: translateY(-50%); opacity: 0.8;">
                        <span style="position: absolute; right: -80px; top: 50%; transform: translateY(-50%); color: #9b59b6; font-size: 11px; white-space: nowrap;">MA80 {m80:.2f}%</span>
                    </div>
                </div>'''

            iv_rows_html += f'''
                    <tr>
                        <td style="font-weight: bold;">{iv['name']}</td>
                        <td style="color: {iv_color}; font-weight: bold;">{cur:.2f}% {star_str}</td>
                        <td style="color: #00d4ff;">{m40:.2f}%</td>
                        <td style="color: #9b59b6;">{m80:.2f}%</td>
                        <td style="color: {signal_color}; font-weight: bold;">{signal}</td>
                    </tr>
                    <tr>
                        <td colspan="5" style="padding: 20px 10px;">
                            <div style="display: flex; justify-content: center; align-items: center; padding: 0 80px;">
                                {vis_html}
                            </div>
                            <div style="text-align: center; color: #666; font-size: 11px; margin-top: 5px;">数据源: {src_label} · 共{iv['data_days']}个交易日</div>
                        </td>
                    </tr>'''

        iv_ma_html = f'''
        <div class="indicator-table">
            <h3 style="color: #00d4ff; margin-bottom: 15px;">📈 期权IV均线监控</h3>
            <table>
                <thead>
                    <tr>
                        <th>标的</th>
                        <th>当前IV</th>
                        <th>MA40</th>
                        <th>MA80</th>
                        <th>信号</th>
                    </tr>
                </thead>
                <tbody>
                    {iv_rows_html}
                </tbody>
            </table>
            <div style="margin-top: 15px; padding: 12px; background: rgba(0,0,0,0.3); border-radius: 8px;">
                <p style="color: #888; font-size: 12px; line-height: 1.6;">
                    <strong style="color: #00d4ff;">IV均线规则</strong>：IV > MA80 → ★★极度恐慌/超买；MA40 < IV ≤ MA80 → ★偏强；IV < MA40 → 平稳<br>
                    <strong style="color: #ffcc00;">含义</strong>：IV站上长期均线表明市场波动率抬升，恐慌情绪升温；IV低于短期均线表明情绪平稳，资金观望
                </p>
            </div>
        </div>
        '''

    html_content = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>市场流动性与风险偏好监控</title>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            min-height: 100vh;
            padding: 20px;
            color: #fff;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        .header {{
            text-align: center;
            margin-bottom: 30px;
        }}
        .header h1 {{
            font-size: 28px;
            margin-bottom: 10px;
            background: linear-gradient(90deg, #00d4ff, #7b2cbf);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .header .date {{
            color: #888;
            font-size: 14px;
        }}
        .summary-cards {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }}
        .card {{
            background: rgba(255,255,255,0.1);
            border-radius: 16px;
            padding: 20px;
            backdrop-filter: blur(10px);
        }}
        .card-title {{
            font-size: 14px;
            color: #888;
            margin-bottom: 8px;
        }}
        .card-value {{
            font-size: 32px;
            font-weight: bold;
            color: #00d4ff;
        }}
        .card-value.risk {{
            color: #ff6b6b;
        }}
        .card-label {{
            font-size: 12px;
            color: #666;
            margin-top: 4px;
        }}
        .chart-container {{
            background: rgba(255,255,255,0.05);
            border-radius: 16px;
            padding: 20px;
            margin-bottom: 30px;
        }}
        #chart {{
            width: 100%;
            height: 400px;
        }}
        .indicator-table {{
            background: rgba(255,255,255,0.05);
            border-radius: 16px;
            padding: 20px;
            margin-bottom: 30px;
            overflow-x: auto;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
        }}
        th, td {{
            padding: 12px;
            text-align: center;
            border-bottom: 1px solid rgba(255,255,255,0.1);
        }}
        th {{
            background: rgba(0,212,255,0.2);
            color: #00d4ff;
            font-weight: 500;
        }}
        td {{
            color: #ccc;
        }}
        .score-high {{
            color: #00ff88;
        }}
        .score-mid {{
            color: #ffcc00;
        }}
        .score-low {{
            color: #ff6b6b;
        }}
        .footer {{
            text-align: center;
            padding: 20px;
            color: #666;
            font-size: 12px;
            border-top: 1px solid rgba(255,255,255,0.1);
        }}
        .disclaimer {{
            color: #ff6b6b;
            margin-top: 10px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>市场流动性与风险偏好监控</h1>
            <div class="date">数据更新日期: {today}</div>
        </div>
        
        <div class="summary-cards">
            <div class="card">
                <div class="card-title">流动性综合评分</div>
                <div class="card-value">{avg_liquidity:.0f}/60</div>
                <div class="card-label">评级: {get_rating(avg_liquidity, 60)}</div>
            </div>
            <div class="card">
                <div class="card-title">风险偏好综合评分</div>
                <div class="card-value risk">{avg_risk:.0f}/40</div>
                <div class="card-label">评级: {get_rating(avg_risk, 40)}</div>
            </div>
            <div class="card">
                <div class="card-title">综合市场状态</div>
                <div class="card-value">{(avg_liquidity+avg_risk):.0f}/100</div>
                <div class="card-label">评级: {get_rating(avg_liquidity+avg_risk, 100)}</div>
            </div>
            <div class="card">
                <div class="card-title">市场情绪</div>
                <div class="card-value" style="font-size: 24px;">
                    {'🟢 乐观' if avg_risk >= 28 else '🟡 中性' if avg_risk >= 16 else '🔴 谨慎'}
                </div>
                <div class="card-label">基于风险偏好</div>
            </div>
        </div>
        
        <div class="chart-container">
            <div id="chart"></div>
        </div>
        
        <div class="indicator-table">
            <table>
                <thead>
                    <tr>
                        <th>日期</th>
                        <th>SHIBOR 7D</th>
                        <th>成交额</th>
                        <th>前5%占比</th>
                        <th>涨停/跌停</th>
                        <th>北向资金</th>
                        <th>流动性</th>
                        <th>风险偏好</th>
                    </tr>
                </thead>
                <tbody>
                    {''.join([f'''
                    <tr>
                        <td>{dates_str[i]}</td>
                        <td>{d["rate_7d"]:.2f}%</td>
                        <td>{d["volume"]:.2f}万亿</td>
                        <td class="{"score-high" if d["concentration"] < 25 else "score-mid" if d["concentration"] < 35 else "score-low"}">{d["concentration"]:.1f}%</td>
                        <td>{d["up_limit"]}/{d["down_limit"]}</td>
                        <td>{d["north_money"]:.0f}亿</td>
                        <td class="{"score-high" if d["liquidity_score"] >= 45 else "score-mid" if d["liquidity_score"] >= 30 else "score-low"}">{d["liquidity_score"]}</td>
                        <td class="{"score-high" if d["risk_score"] >= 28 else "score-mid" if d["risk_score"] >= 16 else "score-low"}">{d["risk_score"]}</td>
                    </tr>''' for i, d in enumerate(daily_data)])}
                </tbody>
            </table>
            
            <div style="margin-top: 20px; padding: 15px; background: rgba(0,0,0,0.3); border-radius: 8px;">
                <h4 style="color: #00d4ff; margin-bottom: 10px;">📊 评分说明</h4>
                <p style="color: #888; font-size: 13px; line-height: 1.8;">
                    <strong style="color: #00d4ff;">流动性评分(60分)</strong>：SHIBOR 7天利率(30分) + 全A成交额(30分)<br>
                    <strong style="color: #ff6b6b;">风险偏好评分(40分)</strong>：成交额前5%占比(18分,占比越低分越高) + 涨停家数(8分) + 跌停家数(7分) + 北向资金(7分)<br>
                    <strong style="color: #ffcc00;">前5%占比含义</strong>：成交额最大的5%股票占全市场比例，低=资金分散(风险偏好高)，高=资金集中(风险偏好低)
                </p>
            </div>
        </div>
        {f'''
        <div class="indicator-table">
            <h3 style="color: #00d4ff; margin-bottom: 15px;">📊 指数VIX分位数（近1年）</h3>
            <table>
                <thead>
                    <tr>
                        <th>指数</th>
                        <th>VIX(%)</th>
                        <th>分位(%)</th>
                        <th>状态</th>
                        <th>分位柱</th>
                    </tr>
                </thead>
                <tbody>
                    {''.join([f"""
                    <tr>
                        <td>{v['name']}</td>
                        <td>{v['vix']:.2f}</td>
                        <td style="color: {'#ff6b6b' if v['alert_type'] != 'normal' else '#00ff88'}; font-weight: bold;">{v['percentile']:.1f}</td>
                        <td style="color: {'#ff6b6b' if v['alert_type'] != 'normal' else '#00ff88'};">{v['alert']}</td>
                        <td style="width: 200px;">
                            <div style="background: rgba(255,255,255,0.1); border-radius: 4px; height: 20px; position: relative;">
                                <div style="background: {'#ff6b6b' if v['alert_type'] != 'normal' else 'linear-gradient(90deg, #00d4ff, #0066ff)'}; height: 100%; width: {v['percentile']}%; border-radius: 4px;"></div>
                                <div style="position: absolute; left: 20%; top: 0; height: 100%; border-left: 2px dashed rgba(255,107,107,0.6);"></div>
                                <div style="position: absolute; left: 80%; top: 0; height: 100%; border-left: 2px dashed rgba(255,107,107,0.6);"></div>
                            </div>
                        </td>
                    </tr>""" for v in (vix_data or [])])}
                </tbody>
            </table>
            <div style="margin-top: 15px; padding: 12px; background: rgba(0,0,0,0.3); border-radius: 8px;">
                <p style="color: #888; font-size: 12px; line-height: 1.6;">
                    VIX = 20日已实现波动率(年化)；分位数基于近252个交易日；<span style="color:#ff6b6b;">🔴 分位≥80%高波预警 / ≤20%低波预警</span>；虚线为20%/80%警戒线
                </p>
            </div>
        </div>
        ''' if vix_data else ''}
        
        {ar_table_html}
        {hindenburg_table_html}
        {margin_html}
        {iv_ma_html}
        
        <div class="footer">
            <p>本报告仅供参考，不构成任何投资建议</p>
            <p class="disclaimer">市场有风险，投资需谨慎</p>
        </div>
    </div>
    
    <script>
        var chart = echarts.init(document.getElementById('chart'));
        var option = {{
            title: {{
                text: '近7日流动性与风险偏好趋势',
                left: 'center',
                textStyle: {{ color: '#fff', fontSize: 18 }}
            }},
            tooltip: {{
                trigger: 'axis',
                axisPointer: {{ type: 'shadow' }},
                formatter: function(params) {{
                    var date = params[0].name;
                    var result = date + '<br/>';
                    params.forEach(function(item) {{
                        result += item.marker + ' ' + item.seriesName + ': ' + item.value + '分<br/>';
                    }});
                    return result;
                }}
            }},
            legend: {{
                data: ['流动性评分(满分60)', '风险偏好评分(满分40)'],
                top: 30,
                textStyle: {{ color: '#888' }}
            }},
            grid: {{
                left: '3%',
                right: '4%',
                bottom: '3%',
                top: '80px',
                containLabel: true
            }},
            xAxis: {{
                type: 'category',
                data: {json.dumps(dates_str)},
                axisLabel: {{ color: '#888' }},
                axisLine: {{ lineStyle: {{ color: '#444' }} }}
            }},
            yAxis: {{
                type: 'value',
                max: 100,
                axisLabel: {{ color: '#888' }},
                axisLine: {{ lineStyle: {{ color: '#444' }} }},
                splitLine: {{ lineStyle: {{ color: 'rgba(255,255,255,0.1)' }} }}
            }},
            series: [
                {{
                    name: '流动性评分(满分60)',
                    type: 'bar',
                    barWidth: '35%',
                    itemStyle: {{
                        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                            {{ offset: 0, color: '#00d4ff' }},
                            {{ offset: 1, color: '#0066ff' }}
                        ]),
                        borderRadius: [4, 4, 0, 0]
                    }},
                    data: {json.dumps(liquidity_scores)},
                    label: {{
                        show: true,
                        position: 'top',
                        color: '#00d4ff',
                        formatter: '{{c}}'
                    }}
                }},
                {{
                    name: '风险偏好评分(满分40)',
                    type: 'bar',
                    barWidth: '35%',
                    itemStyle: {{
                        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                            {{ offset: 0, color: '#ff6b6b' }},
                            {{ offset: 1, color: '#c92a2a' }}
                        ]),
                        borderRadius: [4, 4, 0, 0]
                    }},
                    data: {json.dumps(risk_scores)},
                    label: {{
                        show: true,
                        position: 'top',
                        color: '#ff6b6b',
                        formatter: '{{c}}'
                    }}
                }}
            ]
        }};
        chart.setOption(option);
        window.addEventListener('resize', function() {{
            chart.resize();
        }});
    </script>
</body>
</html>'''
    
    return html_content


def main():
    """主函数"""
    print("=" * 50)
    print("市场流动性与风险偏好监控系统")
    print("=" * 50)
    
    # ---- 数据采集重试机制 ----
    MAX_RETRY = 3
    RETRY_INTERVAL = 60  # 秒

    def _collect_all_data():
        """采集一次完整数据，返回 (daily_data, dates, vix_data, ar_data, hindenburg_data, iv_ma_data, margin_data, missing_count)"""
        _dates = get_trade_dates(10)
        print(f"\n分析日期范围: {_dates[0]} ~ {_dates[-1]}")

        _daily_data = []
        for date in _dates:
            print(f"\n正在获取 {date} 数据...")
            rate_7d = get_shibor_7d()
            volume, concentration = get_trading_volume(date)
            up_limit, down_limit = get_limit_list_data(date)
            north_money = get_north_money(date)
            liquidity_score = calculate_liquidity_score(rate_7d, volume)
            risk_score = calculate_risk_appetite_score(concentration, up_limit, down_limit, north_money)
            data = {
                'date': date,
                'rate_7d': rate_7d,
                'volume': volume,
                'concentration': concentration,
                'up_limit': up_limit,
                'down_limit': down_limit,
                'north_money': north_money,
                'liquidity_score': liquidity_score,
                'risk_score': risk_score
            }
            _daily_data.append(data)
            print(f"  - SHIBOR 7天利率: {rate_7d:.2f}%")
            print(f"  - 全A成交额: {volume:.2f}万亿")
            print(f"  - 成交额前5%占比: {concentration:.1f}%")
            print(f"  - 涨停: {up_limit}家, 跌停: {down_limit}家")
            print(f"  - 北向资金: {north_money:.2f}亿")
            print(f"  - 流动性评分: {liquidity_score}/60")
            print(f"  - 风险偏好评分: {risk_score}/40")

        print("\n--- 计算各指数VIX分位数 ---")
        _vix_data = get_vix_percentile()
        _ar_data = calculate_absorption_ratio()
        _hindenburg_data = calculate_hindenburg_omen()
        _iv_ma_data = calculate_iv_ma_data()

        print("\n--- 获取两融余额数据 ---")
        _margin_data = get_margin_balance_data()
        if _margin_data and _margin_data.get('current'):
            c = _margin_data['current']
            print(f"  - 两融总余额: {c['rzrqye']/1e12:.2f}万亿")
            pct = _margin_data.get('percentiles', {}).get('rzrqye_percentile')
            if pct is not None:
                print(f"  - 历史分位: {pct}%")
            print(f"  - 历史数据点: {_margin_data['data_points']}天")
        else:
            print(f"  ⚠️ 两融余额数据获取失败")

        # 计算最新一天缺失的核心指标数
        latest = _daily_data[-1] if _daily_data else {}
        _missing = 0
        if latest.get('rate_7d', 0) == 0: _missing += 1
        if latest.get('volume', 0) == 0: _missing += 1
        if latest.get('up_limit', 0) == 0: _missing += 1
        if latest.get('north_money', 0) == 0 and latest.get('north_money') is not None: _missing += 1  # 北向可能为0但不应该经常是0
        if not _vix_data or len(_vix_data) == 0: _missing += 1
        return _daily_data, _dates, _vix_data, _ar_data, _hindenburg_data, _iv_ma_data, _margin_data, _missing

    # 重试循环
    daily_data = []
    dates = []
    vix_data = None
    ar_data = None
    hindenburg_data = None
    iv_ma_data = None
    margin_data = None

    for attempt in range(1, MAX_RETRY + 1):
        print(f"\n{'='*50}")
        print(f"🔄 数据采集 第 {attempt}/{MAX_RETRY} 次尝试")
        print(f"{'='*50}")
        daily_data, dates, vix_data, ar_data, hindenburg_data, iv_ma_data, margin_data, missing = _collect_all_data()

        # 核心指标缺失超过2个才重试
        need_retry = missing >= 3
        if not need_retry:
            print(f"\n✅ 第{attempt}次采集数据质量达标（缺失{missing}项），停止重试")
            break
        elif attempt < MAX_RETRY:
            print(f"\n⚠️  核心数据缺失{missing}项，{RETRY_INTERVAL}秒后进行第{attempt+1}次重试...")
            import time as _t
            _t.sleep(RETRY_INTERVAL)
        else:
            print(f"\n❌ {MAX_RETRY}次重试后数据仍不完整（缺失{missing}项），将带告警推送")
    
    # 生成HTML报告
    html_content = generate_html_report(daily_data, dates, vix_data, ar_data, hindenburg_data, iv_ma_data, margin_data)
    
    # 保存报告
    today_str = datetime.now().strftime('%Y-%m-%d')
    output_file = os.path.join(OUTPUT_DIR, f'{today_str}_流动性风险偏好.html')

    # 幂等检查：当天已推送过则永久跳过（用.pushed标记文件）
    import time as _time
    pushed_flag = output_file + '.pushed'
    skip_push = False
    if os.path.exists(pushed_flag):
        skip_push = True
        print(f"\n⚠️  今日 {today_str} 流动性报告已推送过，跳过飞书推送（幂等保护）")
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    print("\n" + "=" * 50)
    print(f"报告已生成: {output_file}")
    print("=" * 50)
    
    # 生成飞书推送版md摘要
    latest = daily_data[-1] if daily_data else None
    if latest:
        # 评分等级
        total_score = latest['liquidity_score'] + latest['risk_score']
        if total_score >= 80:
            overall = "🟢 良好"
        elif total_score >= 60:
            overall = "🟡 中性"
        elif total_score >= 40:
            overall = "🟠 偏弱"
        else:
            overall = "🔴 极弱"
        
        md_lines = [f"# 💧 市场流动性与风险偏好 | {latest['date']}\n"]
        md_lines.append("> 📎 此消息来自 Coze Agent 蓁宝\n")
        md_lines.append(f"## 综合评分: {latest['liquidity_score'] + latest['risk_score']}/100 {overall}\n")
        md_lines.append("### 流动性指标\n")
        md_lines.append(f"- SHIBOR 7天: {latest['rate_7d']:.2f}%")
        md_lines.append(f"- 全A成交额: {latest['volume']:.2f}万亿")
        md_lines.append(f"- 流动性评分: {latest['liquidity_score']}/60")
        md_lines.append("\n### 风险偏好指标\n")
        md_lines.append(f"- 成交额集中度: {latest['concentration']:.1f}%")
        md_lines.append(f"- 涨停/跌停: {latest['up_limit']}/{latest['down_limit']}家")
        md_lines.append(f"- 北向资金: {latest['north_money']:+.2f}亿")
        md_lines.append(f"- 风险偏好评分: {latest['risk_score']}/40")
        
        # VIX分位数表
        if vix_data:
            md_lines.append("\n### 📊 指数VIX分位数（近1年）\n")
            md_lines.append("| 指数 | VIX(%) | 分位(%) | 状态 |")
            md_lines.append("|------|--------|---------|------|")
            for v in vix_data:
                md_lines.append(f"| {v['name']} | {v['vix']:.2f} | {v['percentile']:.1f} | {v['alert']} |")
            # 检查是否有报警
            alerts = [v for v in vix_data if v['alert_type'] != 'normal']
            if alerts:
                md_lines.append("\n⚠️ **波动率预警**：")
                for a in alerts:
                    if a['alert_type'] == 'high':
                        md_lines.append(f"- {a['name']} VIX={a['vix']:.2f}% 分位={a['percentile']:.1f}% **高波预警**")
                    elif a['alert_type'] == 'low':
                        md_lines.append(f"- {a['name']} VIX={a['vix']:.2f}% 分位={a['percentile']:.1f}% **低波预警**")
        
        # 吸收率因子
        if ar_data:
            md_lines.append("\n### 📊 吸收率因子 (Absorption Ratio)\n")
            md_lines.append(f"| 指标 | 数值 |")
            md_lines.append("|------|------|")
            md_lines.append(f"| AR | {ar_data['ar']:.4f} |")
            md_lines.append(f"| AR_5日均值 | {ar_data['ar_5d']:.4f} |")
            md_lines.append(f"| DAR_long | {ar_data['dar_long']:.2f} |")
            md_lines.append(f"| DAR_short | {ar_data['dar_short']:.2f} |")
            md_lines.append(f"| PC1解释方差 | {ar_data['pc1_explained']:.2f}% |")
            md_lines.append(f"| PC3累计方差 | {ar_data['pc3_explained']:.2f}% |")
            md_lines.append(f"| **最终信号** | **{ar_data['signal_desc']}** |")
        
        # 兴登堡指标
        if hindenburg_data:
            md_lines.append("\n### ⚠️ 兴登堡指标 (Hindenburg Omen)\n")
            md_lines.append(f"| 指标 | 数值 |")
            md_lines.append("|------|------|")
            md_lines.append(f"| 120日新高 | {hindenburg_data['new_highs_count']}只 ({hindenburg_data['high_pct']:.2f}%) |")
            md_lines.append(f"| 120日新低 | {hindenburg_data['new_lows_count']}只 ({hindenburg_data['low_pct']:.2f}%) |")
            md_lines.append(f"| 上证指数 | {hindenburg_data['index_close']:.2f} |")
            md_lines.append(f"| 50日均线 | {'上方 ✅' if hindenburg_data['index_above_50ma'] else '下方 ❌'} |")
            trigger_emoji = "⚠️" if hindenburg_data['hindenburg_triggered'] else "✅"
            md_lines.append(f"| **触发状态** | **{trigger_emoji} {hindenburg_data['hindenburg_desc']}** |")
        
        # 两融余额监控
        if margin_data and margin_data.get('current'):
            mc = margin_data['current']
            mchanges = margin_data.get('changes', {})
            mpercentiles = margin_data.get('percentiles', {})
            md_lines.append("\n### 💰 融资融券余额\n")
            md_lines.append("| 指标 | 数值 | 日变动 | 周变动 | 月变动 |")
            md_lines.append("|------|------|--------|--------|--------|")
            rzrq_yi = f"{mc['rzrqye']/1e12:.3f}万亿" if mc.get('rzrqye') else '--'
            def _p(v): return f"{v:+.2f}%" if v is not None else '--'
            md_lines.append(f"| 融资余额 | {mc['rzye']/1e12:.3f}万亿 | {_p(mchanges.get('day_pct'))} | {_p(mchanges.get('week_pct'))} | {_p(mchanges.get('month_pct'))} |")
            md_lines.append(f"| 融券余额 | {mc['rqye']/1e12:.3f}万亿 | -- | -- | -- |")
            md_lines.append(f"| **两融总余额** | **{rzrq_yi}** | **{_p(mchanges.get('day_pct'))}** | **{_p(mchanges.get('week_pct'))}** | **{_p(mchanges.get('month_pct'))}** |")
            pct_val = mpercentiles.get('rzrqye_percentile')
            if pct_val is not None:
                pct_status = "🟠 高位警戒" if pct_val >= 80 else "🔵 低位区域" if pct_val <= 20 else "🟢 正常区间"
                md_lines.append(f"\n**历史分位: {pct_val}%** {pct_status} （统计{margin_data.get('data_points',0)}天）")
            # 告警
            margin_alerts_feishu = check_margin_alerts(margin_data)
            if margin_alerts_feishu:
                md_lines.append("\n⚠️ **两融余额告警**：")
                for a in margin_alerts_feishu:
                    md_lines.append(f"- {a['title']}: {a['message']}")
        
        # 期权IV均线监控
        if iv_ma_data:
            md_lines.append("\n### 📈 期权IV均线监控\n")
            md_lines.append("| 标的 | 当前IV | MA40 | MA80 | 信号 |")
            md_lines.append("|------|--------|------|------|------|")
            for iv in iv_ma_data:
                if iv['data_source'] == 'missing' or iv['current_iv'] is None:
                    md_lines.append(f"| {iv['name']} | 数据缺失 | - | - | 数据缺失 |")
                else:
                    star_str = '★' * iv['stars'] if iv['stars'] > 0 else ''
                    src_label = '期权IV' if iv['data_source'] == 'tushare' else 'RV代替' if iv['data_source'] == 'rv' else '未知'
                    md_lines.append(f"| {iv['name']} | {iv['current_iv']:.2f}% {star_str} | {iv['ma40']:.2f}% | {iv['ma80']:.2f}% | {iv['signal']} ({src_label}) |")
            # 文字版横线可视化
            md_lines.append("\n**均线相对位置：**\n")
            md_lines.append("```")
            for iv in iv_ma_data:
                if iv['data_source'] != 'missing' and iv['current_iv'] is not None:
                    md_lines.append(f"【{iv['name']}】")
                    md_lines.append(_iv_bar_visual(iv['current_iv'], iv['ma40'], iv['ma80'], width=40))
                    md_lines.append("")
            md_lines.append("```")
        
        feishu_md = '\n'.join(md_lines)
        feishu_file = os.path.join(OUTPUT_DIR, f'{today_str}_流动性风险偏好_飞书推送.md')
        with open(feishu_file, 'w', encoding='utf-8') as f:
            f.write(feishu_md)
        print(f"飞书推送版已保存: {feishu_file}")
        
        # 飞书推送（交互式卡片）
        import sys as _sys
        _sys.path.insert(0, os.environ.get('CASE_WORKSPACE') or '/Coze/Drive/蓁宝/所有对话/主对话')
        from feishu_card_utils import build_market_monitor_card
        from feishu_push import push_card_to_feishu

        # ---- 数据缺失检测 ----
        liq_warnings = []
        # 核心指标检查
        if latest.get('rate_7d') is None or latest.get('rate_7d') == 0:
            liq_warnings.append("⚠️ SHIBOR利率数据缺失")
        if latest.get('volume') is None or latest.get('volume') == 0:
            liq_warnings.append("⚠️ 全A成交额数据缺失")
        if latest.get('north_money') is None:
            liq_warnings.append("⚠️ 北向资金数据缺失")
        if latest.get('up_limit') is None or latest.get('down_limit') is None:
            liq_warnings.append("⚠️ 涨跌停数据缺失")
        # 吸收率因子
        if not ar_data:
            liq_warnings.append("⚠️ 吸收率因子(AR)数据缺失")
        # 兴登堡指标
        if not hindenburg_data:
            liq_warnings.append("⚠️ 兴登堡指标数据缺失")

        if liq_warnings:
            print(f"  ⚠️ 数据缺失告警: {len(liq_warnings)}项")
            for w in liq_warnings:
                print(f"    {w}")

        date_display = latest.get('date', today_str)
        # 预计算两融告警（避免卡片里循环导入）
        _margin_alerts = check_margin_alerts(margin_data) if margin_data else []
        card = build_market_monitor_card(
            date_display,
            latest.get('liquidity_score', 0),
            latest.get('risk_score', 0),
            latest.get('concentration', 0),
            latest,
            vix_data=vix_data,
            ar_data=ar_data,
            hindenburg_data=hindenburg_data,
            iv_ma_data=iv_ma_data,
            margin_data=margin_data,
            margin_alerts=_margin_alerts,
            data_warnings=liq_warnings,
        )
        if skip_push:
            print("今天已推送，跳过飞书推送（幂等保护）")
        else:
            success = push_card_to_feishu(card_dict=card)
            if success:
                with open(pushed_flag, 'w') as _pf:
                    _pf.write(_time.strftime('%Y-%m-%d %H:%M:%S'))
                print("✅ 推送成功，已写幂等标记")
        
        # ---- 两融余额邮件告警 ----
        print("\n--- 两融余额告警检查 ---")
        if _margin_alerts:
            print(f"  发现 {len(_margin_alerts)} 条告警，准备发送邮件...")
            # 幂等：同一天同一类型告警只发一次
            margin_alert_flag = os.path.join(OUTPUT_DIR, f'margin_alert_{today_str}.json')
            if os.path.exists(margin_alert_flag):
                print(f"  ⚠️  今日两融告警邮件已发送，跳过（幂等保护）")
            else:
                send_margin_alert_email(_margin_alerts, margin_data)
                with open(margin_alert_flag, 'w') as _maf:
                    import json as _json
                    _json.dump({
                        'date': today_str,
                        'alerts': [a['type'] for a in _margin_alerts],
                        'sent_at': _time.strftime('%Y-%m-%d %H:%M:%S'),
                    }, _maf)
        else:
            _pct = margin_data.get('percentiles', {}).get('rzrqye_percentile') if margin_data else None
            _day = margin_data.get('changes', {}).get('day_pct') if margin_data else None
            print(f"  ✅ 无告警（日变动={_day}%，分位={_pct}%）" if margin_data else "  ⚠️ 两融数据缺失，跳过告警检查")
    
    return output_file


if __name__ == '__main__':
    main()
