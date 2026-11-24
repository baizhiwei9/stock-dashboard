#!/usr/bin/env python3
"""
TDA流形旋转策略 - 债券端模块
==============================
1. 数据获取: 国债期货主力合约T/TF/TS + 国债收益率10Y/1Y + DR007 + 信用利差
2. 宏观状态判定: 宽货币/紧货币 x 宽信用/紧信用
3. TDA震荡识别: 国债期货60日价格 UMAP 1D降维 + 持久同调 -> 震荡打分
4. 多空方向判定: 综合宏观状态和TDA状态 -> 做多/做空/观望
5. ATR动态止损
"""

import os
import sys
import time
import numpy as np
import pandas as pd
import akshare as ak

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ts_config import init_tushare
from data_fetcher import get_trade_calendar, format_date_display

# ========== 债券端数据获取 ==========

_bond_cache = {}

def fetch_bond_futures(symbol='T0', start_date='20250601', end_date='20260818'):
    """
    获取国债期货主力合约日线
    symbol: T0=10年期主力, TF0=5年期主力, TS0=2年期主力
    """
    cache_key = f"bond_{symbol}_{start_date}_{end_date}"
    if cache_key in _bond_cache:
        return _bond_cache[cache_key]
    
    try:
        df = ak.futures_main_sina(symbol=symbol, start_date=start_date, end_date=end_date)
        if df is not None and len(df) > 0:
            df['trade_date'] = df['日期'].astype(str).str.replace('-', '')
            df = df.sort_values('trade_date').reset_index(drop=True)
            _bond_cache[cache_key] = df
            return df
    except Exception as e:
        print(f"  获取国债期货{symbol}失败: {e}")
    
    return pd.DataFrame()

def fetch_bond_futures_all(start_date, end_date):
    """获取T/TF/TS三个国债期货主力合约"""
    results = {}
    for name, sym in [('T(10年)', 'T0'), ('TF(5年)', 'TF0'), ('TS(2年)', 'TS0')]:
        df = fetch_bond_futures(sym, start_date, end_date)
        if not df.empty:
            results[name] = df
            print(f"  {name}: {len(df)}条数据")
        else:
            print(f"  {name}: 无数据")
        time.sleep(0.3)
    return results

def fetch_bond_yield(start_date, end_date):
    """
    获取国债收益率曲线
    返回: DataFrame with columns [trade_date, 1Y, 10Y, 30Y, 10Y-1Y利差]
    """
    cache_key = f"yield_{start_date}_{end_date}"
    if cache_key in _bond_cache:
        return _bond_cache[cache_key]
    
    try:
        df = ak.bond_china_yield(start_date=start_date, end_date=end_date)
        if df is not None and len(df) > 0:
            # 只取国债收益率曲线
            df = df[df['曲线名称'] == '中债国债收益率曲线'].copy()
            df['trade_date'] = df['日期'].astype(str).str.replace('-', '')
            df = df.sort_values('trade_date').reset_index(drop=True)
            # 计算10Y-1Y利差
            df['10Y-1Y'] = df['10年'] - df['1年']
            _bond_cache[cache_key] = df
            return df
    except Exception as e:
        print(f"  获取国债收益率失败: {e}")
    
    return pd.DataFrame()

def fetch_credit_spread(start_date, end_date):
    """
    获取信用利差 (AAA中短期票据收益率 - 国债收益率)
    """
    cache_key = f"credit_spread_{start_date}_{end_date}"
    if cache_key in _bond_cache:
        return _bond_cache[cache_key]
    
    try:
        df = ak.bond_china_yield(start_date=start_date, end_date=end_date)
        if df is not None and len(df) > 0:
            # 提取国债和AAA信用债
            gov = df[df['曲线名称'] == '中债国债收益率曲线'].copy()
            credit = df[df['曲线名称'] == '中债中短期票据收益率曲线(AAA)'].copy()
            
            if gov.empty or credit.empty:
                return pd.DataFrame()
            
            gov['trade_date'] = gov['日期'].astype(str).str.replace('-', '')
            credit['trade_date'] = credit['日期'].astype(str).str.replace('-', '')
            
            # 合并计算信用利差
            merged = gov[['trade_date', '1年', '10年']].merge(
                credit[['trade_date', '1年', '10年']], 
                on='trade_date', 
                suffixes=('_gov', '_credit')
            )
            merged['credit_spread_1Y'] = merged['1年_credit'] - merged['1年_gov']
            merged['credit_spread_10Y'] = merged['10年_credit'] - merged['10年_gov']
            merged = merged.sort_values('trade_date').reset_index(drop=True)
            
            _bond_cache[cache_key] = merged
            return merged
    except Exception as e:
        print(f"  获取信用利差失败: {e}")
    
    return pd.DataFrame()

def fetch_dr007(start_date, end_date):
    """
    获取DR007回购利率
    """
    cache_key = f"dr007_{start_date}_{end_date}"
    if cache_key in _bond_cache:
        return _bond_cache[cache_key]
    
    try:
        from data_fetcher import ts_call
        df = ts_call('repo_daily', start_date=start_date, end_date=end_date)
        if df is not None and len(df) > 0:
            # 筛选DR007
            dr007 = df[df['repo_maturity'] == 'DR007'].copy()
            dr007 = dr007.sort_values('trade_date').reset_index(drop=True)
            _bond_cache[cache_key] = dr007
            return dr007
    except Exception as e:
        print(f"  获取DR007失败: {e}")
    
    return pd.DataFrame()

def fetch_shibor(date_str):
    """获取Shibor利率报价"""
    cache_key = f"shibor_{date_str}"
    if cache_key in _bond_cache:
        return _bond_cache[cache_key]
    
    try:
        from data_fetcher import ts_call
        df = ts_call('shibor_quote', date=date_str)
        if df is not None and len(df) > 0:
            _bond_cache[cache_key] = df
            return df
    except Exception as e:
        print(f"  获取Shibor失败: {e}")
    
    return pd.DataFrame()

# ========== 宏观状态判定 ==========

def judge_macro_state(dr007_df, credit_spread_df, lookback=20):
    """
    判定宏观货币/信用状态
    
    货币状态: DR007趋势 (20日均值 vs 60日均值)
      - 20日均值 < 60日均值 -> 宽货币 (利率下行)
      - 20日均值 > 60日均值 -> 紧货币 (利率上行)
    
    信用状态: 信用利差趋势
      - 利差收窄 -> 宽信用
      - 利差走阔 -> 紧信用
    
    返回:
      (monetary_state, credit_state, macro_state)
      macro_state: '宽货币+宽信用', '宽货币+紧信用', '紧货币+宽信用', '紧货币+紧信用'
    """
    # ===== 货币状态 =====
    monetary_state = '中性'
    dr007_trend = 0
    
    if dr007_df is not None and len(dr007_df) >= 10:
        dr007_rates = dr007_df['close'].astype(float).values
        n = len(dr007_rates)
        recent_20 = np.mean(dr007_rates[-min(20, n):])
        
        if n >= 40:
            recent_60 = np.mean(dr007_rates[-min(60, n):])
        else:
            recent_60 = np.mean(dr007_rates)
        
        dr007_trend = recent_20 - recent_60
        
        # 降低阈值: 0.005 = 0.5bp
        if dr007_trend < -0.005:
            monetary_state = '宽货币'
        elif dr007_trend > 0.005:
            monetary_state = '紧货币'
        else:
            # 进一步判断方向
            if dr007_trend < 0:
                monetary_state = '宽货币'
            elif dr007_trend > 0:
                monetary_state = '紧货币'
    
    # ===== 信用状态 =====
    credit_state = '中性'
    credit_trend = 0
    
    if credit_spread_df is not None and len(credit_spread_df) >= 10:
        spreads = credit_spread_df['credit_spread_10Y'].astype(float).values
        n = len(spreads)
        recent_20 = np.mean(spreads[-min(20, n):])
        
        if n >= 40:
            recent_60 = np.mean(spreads[-min(60, n):])
        else:
            recent_60 = np.mean(spreads)
        
        credit_trend = recent_20 - recent_60
        
        # 降低阈值
        if credit_trend < -0.005:
            credit_state = '宽信用'
        elif credit_trend > 0.005:
            credit_state = '紧信用'
        else:
            if credit_trend < 0:
                credit_state = '宽信用'
            elif credit_trend > 0:
                credit_state = '紧信用'
    
    # ===== 综合宏观状态 =====
    macro_state = f"{monetary_state}+{credit_state}"
    
    # 债券含义 (核心逻辑: 宽货币→利率下行→债券涨价→偏多; 紧货币→利率上行→债券跌→偏空)
    # 信用面影响次要: 宽信用→风险偏好上升→资金流出国债→略偏空; 紧信用→避险→略偏多
    # 货币政策主导方向, 信用面仅做修正
    bond_implication = {
        '宽货币+宽信用': '中性偏多',   # 货币偏多为主, 信用偏空为辅 → 净偏多
        '宽货币+紧信用': '偏多',       # 货币偏多 + 信用偏多 → 强多
        '紧货币+宽信用': '偏空',       # 货币偏空 + 信用偏空 → 强空
        '紧货币+紧信用': '中性偏空',   # 货币偏空为主, 信用偏多为辅 → 净偏空
    }.get(macro_state, '中性')
    
    return {
        'monetary_state': monetary_state,
        'credit_state': credit_state,
        'macro_state': macro_state,
        'bond_implication': bond_implication,
        'dr007_trend': round(dr007_trend, 4),
        'credit_trend': round(credit_trend, 4),
    }

# ========== TDA 震荡识别 ==========

def compute_bond_tda_score(bond_prices, window=60):
    """
    对国债期货价格做UMAP 1D降维 + 持久同调 -> 震荡打分
    
    参数:
        bond_prices: Series, 国债期货收盘价序列
        window: 回看窗口
    
    返回:
        tda_score: 0-1, 低=趋势明确, 高=震荡
    """
    from tda_core import umap_reduce, compute_persistent_homology, extract_topological_features
    
    # 取最近window天
    if len(bond_prices) > window:
        prices = bond_prices.tail(window)
    else:
        prices = bond_prices
    
    # 构建特征矩阵: 价格 + 收益率 + 波动率
    ret = prices.pct_change().dropna()
    vol = ret.rolling(5).std().dropna()
    
    # 对齐长度
    min_len = min(len(ret), len(vol))
    features = np.column_stack([
        prices.values[-min_len:],
        ret.values[-min_len:],
        vol.values[-min_len:],
    ])
    features = np.nan_to_num(features, nan=0.0)
    
    # UMAP 1D降维
    try:
        embedding = umap_reduce(features, n_components=1, n_neighbors=min(15, max(2, min_len-1)))
    except:
        return 0.5  # 默认中等震荡
    
    # 持久同调
    persistence = compute_persistent_homology(embedding, maxdim=1)
    tda_features = extract_topological_features(persistence)
    
    # 震荡打分
    beta_1 = tda_features['beta_1']
    max_pers_1 = tda_features['max_persistence_1']
    
    # β₁高 = 结构洞多 = 震荡
    # max_persistence_1低 = 结构不稳定 = 震荡
    score = min(1.0, beta_1 * 0.15 + (1 - min(1.0, max_pers_1 * 5)) * 0.3 + 0.3)
    
    return round(score, 4)

# ========== 多空方向判定 ==========

def decide_bond_direction(macro_state, tda_score, threshold=0.75, bond_prices=None):
    """
    综合宏观状态和TDA震荡打分决定多空方向
    
    逻辑:
    - 宏观偏多 + TDA震荡低 -> 做多
    - 宏观偏空 + TDA震荡低 -> 做空
    - TDA震荡高 -> 观望/轻仓
    - 宏观数据不足时 -> 用国债期货价格趋势作为fallback
    
    返回:
        direction: +1(做多), -1(做空), 0(观望)
        confidence: 0-1
    """
    bond_impl = macro_state.get('bond_implication', '中性')
    
    direction = 0
    confidence = 0.0
    
    if tda_score >= threshold:
        # 震荡加剧，观望
        direction = 0
        confidence = 1.0 - tda_score
    else:
        if '偏多' in bond_impl:
            direction = 1
            confidence = (1 - tda_score) * 0.8
        elif '偏空' in bond_impl:
            direction = -1
            confidence = (1 - tda_score) * 0.8
        elif '中性偏多' in bond_impl:
            direction = 1
            confidence = (1 - tda_score) * 0.5
        elif '中性偏空' in bond_impl:
            direction = -1
            confidence = (1 - tda_score) * 0.5
        else:
            # 完全中性时，用国债期货价格趋势作为fallback
            if bond_prices is not None and len(bond_prices) >= 20:
                ma_short = bond_prices.tail(5).mean()
                ma_long = bond_prices.tail(20).mean()
                if ma_short > ma_long:
                    direction = 1  # 价格上行 -> 做多
                    confidence = 0.4
                elif ma_short < ma_long:
                    direction = -1  # 价格下行 -> 做空
                    confidence = 0.4
                else:
                    direction = 0
                    confidence = 0.3
            else:
                direction = 0
                confidence = 0.3
    
    return {
        'direction': direction,
        'direction_str': '做多' if direction > 0 else ('做空' if direction < 0 else '观望'),
        'confidence': round(confidence, 3),
        'tda_score': tda_score,
        'bond_implication': bond_impl,
    }

# ========== ATR止损 ==========

def compute_atr_stop(bond_prices, current_price, direction, period=14, multiplier=1.5):
    """
    基于ATR(真实波动幅度)计算动态止损位
    
    参数:
        bond_prices: Series, 近期价格序列
        current_price: 当前价格
        direction: +1做多, -1做空
        period: ATR计算周期
        multiplier: 止损倍数
    
    返回:
        stop_price: 止损价
        atr_value: ATR值
        stop_pct: 止损比例
    """
    if len(bond_prices) < period + 1:
        # 数据不足，用固定0.8%止损
        stop_pct = 0.008
        if direction > 0:
            stop_price = current_price * (1 - stop_pct)
        elif direction < 0:
            stop_price = current_price * (1 + stop_pct)
        else:
            stop_price = current_price
        return stop_price, stop_pct * current_price, stop_pct
    
    # 计算ATR
    high = bond_prices.tail(period + 1).values
    low = bond_prices.tail(period + 1).values
    close = bond_prices.tail(period + 1).values
    
    tr_list = []
    for i in range(1, len(high)):
        tr = max(
            high[i] - low[i],
            abs(high[i] - close[i-1]),
            abs(low[i] - close[i-1])
        )
        tr_list.append(tr)
    
    atr = np.mean(tr_list)
    stop_distance = atr * multiplier
    stop_pct = stop_distance / current_price if current_price > 0 else 0.008
    
    # 限制止损比例在0.5%-1.5%
    stop_pct = np.clip(stop_pct, 0.005, 0.015)
    stop_distance = current_price * stop_pct
    
    if direction > 0:
        stop_price = current_price - stop_distance
    elif direction < 0:
        stop_price = current_price + stop_distance
    else:
        stop_price = current_price
    
    return round(stop_price, 3), round(atr, 3), round(stop_pct * 100, 2)

# ========== 债券端完整分析 ==========

def run_bond_analysis(trade_date=None, window=60):
    """
    债券端完整分析流程
    
    返回:
        dict: 包含宏观状态、TDA状态、多空方向、止损位
    """
    if trade_date is None:
        import datetime
        trade_date = datetime.date.today().strftime('%Y%m%d')
    
    print(f"  [债券端] 获取数据...")
    
    # 获取交易日历
    cal = get_trade_calendar()
    if trade_date in cal:
        idx = cal.index(trade_date)
        start_idx = max(0, idx - window - 10)
        start_date = cal[start_idx]
    else:
        start_date = (pd.Timestamp(trade_date) - pd.Timedelta(days=window*2)).strftime('%Y%m%d')
    
    # 1. 国债期货主力合约
    bond_df = fetch_bond_futures('T0', start_date, trade_date)
    if bond_df.empty:
        print(f"  [债券端] 国债期货数据获取失败")
        return None
    
    bond_prices = bond_df.set_index('trade_date')['收盘价'].astype(float)
    current_price = bond_prices.iloc[-1]
    print(f"  [债券端] T0主力合约: {len(bond_prices)}条, 最新收盘={current_price}")
    
    # 2. 国债收益率
    yield_df = fetch_bond_yield(start_date, trade_date)
    if not yield_df.empty:
        latest_yield = yield_df.iloc[-1]
        print(f"  [债券端] 10Y国债收益率: {latest_yield['10年']:.4f}%, 1Y: {latest_yield['1年']:.4f}%, 10Y-1Y: {latest_yield['10Y-1Y']:.4f}%")
    
    # 3. 信用利差
    credit_df = fetch_credit_spread(start_date, trade_date)
    
    # 4. DR007
    dr007_df = fetch_dr007(start_date, trade_date)
    if not dr007_df.empty:
        latest_dr007 = dr007_df.iloc[-1]['close']
        print(f"  [债券端] DR007: {latest_dr007}%")
    
    print(f"  [债券端] 宏观状态判定...")
    macro = judge_macro_state(dr007_df, credit_df, lookback=20)
    print(f"  [债券端] 宏观状态: {macro['macro_state']}, 债券含义: {macro['bond_implication']}")
    
    print(f"  [债券端] TDA震荡识别...")
    tda_score = compute_bond_tda_score(bond_prices, window=window)
    print(f"  [债券端] TDA震荡打分: {tda_score}")
    
    print(f"  [债券端] 多空方向判定...")
    bond_signal = decide_bond_direction(macro, tda_score, bond_prices=bond_prices)
    print(f"  [债券端] 方向: {bond_signal['direction_str']}, 置信度: {bond_signal['confidence']}")
    
    # 止损位
    stop_price, atr_val, stop_pct = compute_atr_stop(
        bond_prices, current_price, bond_signal['direction']
    )
    
    # 10Y-1Y利差
    yield_spread = latest_yield['10Y-1Y'] if not yield_df.empty else None
    
    result = {
        'trade_date': trade_date,
        'bond_price': round(current_price, 3),
        'contract': 'T0(10年期国债期货主力)',
        'macro': macro,
        'tda_score': tda_score,
        'direction': bond_signal['direction'],
        'direction_str': bond_signal['direction_str'],
        'confidence': bond_signal['confidence'],
        'stop_price': stop_price,
        'atr': atr_val,
        'stop_pct': stop_pct,
        'yield_10y': round(latest_yield['10年'], 4) if not yield_df.empty else None,
        'yield_1y': round(latest_yield['1年'], 4) if not yield_df.empty else None,
        'yield_spread': round(yield_spread, 4) if yield_spread is not None else None,
        'dr007': float(latest_dr007) if not dr007_df.empty else None,
    }
    
    return result

def compute_bond_daily_return(bond_prices, prev_direction, trade_date):
    """
    计算债券端日收益
    
    bond_return = direction × (price_today - price_yesterday) / price_yesterday
    """
    if prev_direction == 0 or trade_date not in bond_prices.index:
        return 0.0
    
    idx = bond_prices.index.tolist().index(trade_date)
    if idx < 1:
        return 0.0
    
    today_price = bond_prices.iloc[idx]
    yesterday_price = bond_prices.iloc[idx - 1]
    
    if yesterday_price == 0:
        return 0.0
    
    return prev_direction * (today_price - yesterday_price) / yesterday_price


# ========== 测试 ==========

if __name__ == '__main__':
    import warnings
    warnings.filterwarnings('ignore')
    
    print("=" * 60)
    print("债券端模块测试")
    print("=" * 60)
    
    result = run_bond_analysis('20260818')
    
    if result:
        print(f"\n=== 债券端分析结果 ===")
        print(f"  合约: {result['contract']}")
        print(f"  当前价格: {result['bond_price']}")
        print(f"  10Y收益率: {result['yield_10y']}%")
        print(f"  1Y收益率: {result['yield_1y']}%")
        print(f"  10Y-1Y利差: {result['yield_spread']}%")
        print(f"  DR007: {result['dr007']}%")
        print(f"  宏观状态: {result['macro']['macro_state']}")
        print(f"  债券含义: {result['macro']['bond_implication']}")
        print(f"  TDA震荡打分: {result['tda_score']}")
        print(f"  方向: {result['direction_str']}")
        print(f"  置信度: {result['confidence']}")
        print(f"  止损位: {result['stop_price']} (ATR={result['atr']}, 止损比例={result['stop_pct']}%)")
