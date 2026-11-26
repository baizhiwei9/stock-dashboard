#!/usr/bin/env python3
"""
期权综合监控脚本 - SKEW + GEX + 升贴水 三合一
合并自: skew_calculator.py + gex_calculator.py + basis_quick.py
核心优化: 期权数据只取一次，SKEW和GEX共享，避免API限流

用法: python option_monitor.py [YYYYMMDD]
输出: JSON到 stdout，同时保存到 市场情绪监控/outputs/{date}_option_data.json
"""

import os, sys, json, math, time, socket, warnings
import datetime
from datetime import timedelta
from typing import Optional, Dict, List, Tuple
from collections import defaultdict

import numpy as np
import pandas as pd
from scipy.stats import norm
from scipy.optimize import brentq

warnings.filterwarnings('ignore')
socket.setdefaulttimeout(15)

# tushare
import tushare as ts
TS_TOKEN = "a7c1ab22e3162aac86bf77323755b2d99e9cadfad4ae631a50574265"
ts.set_token(TS_TOKEN)
pro = ts.pro_api()

API_SLEEP = 0.15  # API请求间隔(秒)

# ============ 标的配置 ============
OPTION_TARGETS = [
    {"name": "50ETF",    "code": "510050", "exchange": "SSE",   "opt_code": "OP510050.SH"},
    {"name": "300ETF",   "code": "510300", "exchange": "SSE",   "opt_code": "OP510300.SH"},
    {"name": "500ETF",   "code": "510500", "exchange": "SSE",   "opt_code": "OP510500.SH"},
    {"name": "创业板ETF","code": "159915", "exchange": "SZSE",  "opt_code": "OP159915.SZ"},
    {"name": "科创50ETF","code": "588000", "exchange": "SSE",   "opt_code": "OP588000.SH"},
    {"name": "中证1000", "code": "MO",     "exchange": "CFFEX", "opt_code": "OP000852.SH", "underlying_code": "000852"},
]

BASIS_PRODUCTS = {
    'IF': {'idx': '000300.SH', 'name': '沪深300'},
    'IC': {'idx': '000905.SH', 'name': '中证500'},
    'IM': {'idx': '000852.SH', 'name': '中证1000'},
}
BASIS_QUARTERS = [3, 6, 9, 12]

# ============ BS模型 ============

def bs_d1(S, K, T, r, sigma, q=0):
    if T <= 0 or sigma <= 0: return None
    try:
        return (math.log(S/K) + (r - q + 0.5*sigma**2)*T) / (sigma*math.sqrt(T))
    except: return None

def bs_call(S, K, T, r, sigma, q=0):
    d1 = bs_d1(S, K, T, r, sigma, q)
    if d1 is None: return max(S-K, 0)
    d2 = d1 - sigma*math.sqrt(T)
    try: return S*math.exp(-q*T)*norm.cdf(d1) - K*math.exp(-r*T)*norm.cdf(d2)
    except: return max(S-K, 0)

def bs_put(S, K, T, r, sigma, q=0):
    d1 = bs_d1(S, K, T, r, sigma, q)
    if d1 is None: return max(K-S, 0)
    d2 = d1 - sigma*math.sqrt(T)
    try: return K*math.exp(-r*T)*norm.cdf(-d2) - S*math.exp(-q*T)*norm.cdf(-d1)
    except: return max(K-S, 0)

def bs_iv(S, K, T, r, mkt_price, opt_type='call', q=0):
    """Brent法求IV"""
    if T <= 0 or mkt_price <= 0: return None
    intrinsic = max(S-K, 0) if opt_type == 'call' else max(K-S, 0)
    if mkt_price < intrinsic: return None
    def obj(sigma):
        p = bs_call(S, K, T, r, sigma, q) if opt_type == 'call' else bs_put(S, K, T, r, sigma, q)
        return p - mkt_price if p is not None else 1e10
    try:
        iv = brentq(obj, 0.001, 5.0, xtol=1e-6)
        return iv if 0.01 < iv < 3.0 else None
    except: return None

def bs_gamma(S, K, T, r, sigma, q=0):
    if T <= 0 or sigma <= 0 or S <= 0: return None
    d1 = bs_d1(S, K, T, r, sigma, q)
    if d1 is None: return None
    try: return math.exp(-q*T)*norm.pdf(d1) / (S*sigma*math.sqrt(T))
    except: return None

def delta_to_strike(S, T, r, sigma, target_delta, is_put=True):
    if T <= 0: return S
    d1 = norm.ppf(target_delta if not is_put else 1-target_delta)
    return S * math.exp(-(r + 0.5*sigma**2)*T + sigma*math.sqrt(T)*d1)

# ============ 数据获取 ============

def _spot_akshare_index(underlying_code):
    """akshare降级：指数现货价（新浪实时）"""
    try:
        import akshare as ak
        df = ak.stock_zh_index_spot_sina()
        # 代码格式: sh000300 / sz000001
        target = f"sh{underlying_code}"
        row = df[df['代码'] == target]
        if len(row) > 0:
            return float(row.iloc[0]['最新价'])
        target2 = f"sz{underlying_code}"
        row = df[df['代码'] == target2]
        if len(row) > 0:
            return float(row.iloc[0]['最新价'])
    except: pass
    return None

def _spot_akshare_etf(code, exchange):
    """akshare降级：ETF现货价（东财实时）"""
    try:
        import akshare as ak
        df = ak.fund_etf_spot_em()
        row = df[df['代码'] == code]
        if len(row) > 0:
            return float(row.iloc[0]['最新价'])
    except: pass
    return None

def get_spot_price(code, exchange, trade_date, underlying_code=None):
    """获取标的现货价格（Tushare主 + akshare降级）"""
    if exchange == 'CFFEX' and underlying_code:
        # 指数: tushare index_daily
        try:
            df = pro.index_daily(ts_code=f"{underlying_code}.SH", start_date=trade_date, end_date=trade_date)
            if df is not None and len(df) > 0:
                return float(df.iloc[0]['close'])
        except: pass
        # 备选: 取最近
        try:
            end = trade_date
            start = (datetime.datetime.strptime(trade_date,'%Y%m%d') - timedelta(days=10)).strftime('%Y%m%d')
            df = pro.index_daily(ts_code=f"{underlying_code}.SH", start_date=start, end_date=end)
            if df is not None and len(df) > 0:
                return float(df.sort_values('trade_date', ascending=False).iloc[0]['close'])
        except: pass
        # akshare降级
        price = _spot_akshare_index(underlying_code)
        if price is not None:
            return price
        return None

    # ETF: tushare fund_daily
    ts_code = f"{code}.SH" if exchange == 'SSE' else f"{code}.SZ"
    try:
        df = pro.fund_daily(ts_code=ts_code, trade_date=trade_date)
        if df is not None and len(df) > 0:
            return float(df.iloc[0]['close'])
    except: pass
    # akshare降级
    price = _spot_akshare_etf(code, exchange)
    if price is not None:
        return price
    return None

def get_option_contracts_tushare(exchange, opt_code_filter, trade_date):
    """Tushare主链路：获取期权合约列表+日线数据"""
    time.sleep(API_SLEEP)
    try:
        opt_list = pro.opt_basic(exchange=exchange)
    except Exception as e:
        print(f"  [Tushare] opt_basic失败: {e}")
        return []

    if opt_list is None or opt_list.empty:
        print(f"  [Tushare] opt_basic返回空")
        return []

    # 按opt_code过滤
    if opt_code_filter:
        opt_list = opt_list[opt_list['opt_code'] == opt_code_filter]
    if opt_list.empty:
        print(f"  [Tushare] 过滤后无合约: {opt_code_filter}")
        return []

    # 只取活跃合约
    active = opt_list[opt_list['maturity_date'] >= trade_date]
    if active.empty:
        print(f"  [Tushare] 无活跃合约")
        return []

    # 取最近两个到期日
    expiry_dates = sorted(active['maturity_date'].unique())[:2]
    target = active[active['maturity_date'].isin(expiry_dates)]
    target_codes = set(target['ts_code'].tolist())

    # 获取日线数据（重试2次）
    daily_df = None
    for retry in range(2):
        time.sleep(API_SLEEP)
        try:
            daily_df = pro.opt_daily(exchange=exchange, start_date=trade_date, end_date=trade_date)
            if daily_df is not None and not daily_df.empty:
                break
            print(f"  [Tushare] opt_daily返回空 (重试{retry+1}/2)")
        except Exception as e:
            print(f"  [Tushare] opt_daily失败: {e} (重试{retry+1}/2)")
            time.sleep(1)

    if daily_df is None or daily_df.empty:
        print(f"  [Tushare] 日线数据获取失败，全部重试后仍为空")
        return []

    daily_filtered = daily_df[daily_df['ts_code'].isin(target_codes)]
    print(f"  [Tushare] 批量查询: {len(daily_df)}条 -> 过滤后 {len(daily_filtered)}条")

    # 组装
    contracts = []
    for _, basic_row in target.iterrows():
        ts_code = basic_row['ts_code']
        daily_row = daily_filtered[daily_filtered['ts_code'] == ts_code]
        if daily_row.empty: continue

        d = daily_row.iloc[0]
        # 判断C/P
        cp = str(basic_row.get('call_put', ''))
        if cp not in ('C', 'P'):
            name = str(basic_row.get('name', ''))
            cp = 'C' if ('购' in name or 'C' in name) else 'P'

        contracts.append({
            'ts_code': ts_code,
            'call_put': cp,
            'exercise_price': float(basic_row['exercise_price']),
            'maturity_date': str(basic_row['maturity_date']),
            'close': float(d['close']),
            'volume': int(d.get('vol', 0) or 0),
            'oi': int(d.get('oi', 0) or 0),
            'amount': float(d.get('amount', 0) or 0),
        })

    return contracts


def _get_akshare_sse_contracts(etf_code, trade_date):
    """akshare备用：上交所ETF期权（合约列表来自option_current_day_sse，价格来自option_finance_board）"""
    import akshare as ak
    
    # ETF代码 -> 看板名称映射
    board_name_map = {
        '510050': '华夏上证50ETF期权',
        '510300': '华泰柏瑞沪深300ETF期权',
        '510500': '南方中证500ETF期权',
        '588000': '华夏科创50ETF期权',
    }
    # ETF代码 -> 合约简称前缀
    name_prefix_map = {
        '510050': '50ETF',
        '510300': '300ETF',
        '510500': '500ETF',
        '588000': '科创50',
    }
    
    board_name = board_name_map.get(etf_code)
    name_prefix = name_prefix_map.get(etf_code, etf_code)
    
    if not board_name:
        print(f"  [Akshare] 未知上交所ETF: {etf_code}")
        return []
    
    # Step 1: 获取全部合约列表（取行权价、到期日）
    try:
        time.sleep(0.3)
        all_contracts = ak.option_current_day_sse()
        if all_contracts is None or all_contracts.empty:
            print(f"  [Akshare] option_current_day_sse返回空")
            return []
        # 按标的筛选
        target = all_contracts[all_contracts['合约简称'].str.startswith(name_prefix)]
        if target.empty:
            print(f"  [Akshare] 未找到{name_prefix}相关合约")
            return []
    except Exception as e:
        print(f"  [Akshare] 获取合约列表失败: {e}")
        return []
    
    # 取最近两个到期月
    target['到期日'] = target['到期日'].astype(str)
    expiry_dates = sorted(target['到期日'].unique())[:2]
    target = target[target['到期日'].isin(expiry_dates)]
    
    # Step 2: 获取价格数据（option_finance_board按月取）
    price_map = {}  # key: 合约交易代码 -> {close, volume, oi}
    for exp in expiry_dates:
        # 到期日格式: YYYYMMDD -> YYMM
        end_month = exp[2:6]
        try:
            time.sleep(0.3)
            df = ak.option_finance_board(symbol=board_name, end_month=end_month)
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    code = str(row['合约交易代码'])
                    # 用前结价作为近似（当日还没收盘的话用当前价）
                    price = float(row.get('前结价', 0) or 0)
                    if price <= 0:
                        price = float(row.get('当前价', 0) or 0)
                    if price > 0:
                        price_map[code] = {
                            'close': price,
                            'volume': 0,  # 看板没有成交量
                            'oi': 0,      # 看板没有持仓量
                        }
        except Exception as e:
            print(f"  [Akshare] {board_name} {end_month} 行情失败: {e}")
    
    # 组装合约
    contracts = []
    has_price = 0
    for _, row in target.iterrows():
        code = str(row['合约交易代码'])
        cp_type = str(row.get('类型', ''))
        cp = 'C' if ('购' in cp_type or '认购' in cp_type) else 'P'
        
        price_info = price_map.get(code, {})
        close = price_info.get('close', 0)
        
        contracts.append({
            'ts_code': code,
            'call_put': cp,
            'exercise_price': float(row['行权价']),
            'maturity_date': str(row['到期日']),
            'close': close,
            'volume': 0,
            'oi': 0,
            'amount': 0,
        })
        if close > 0:
            has_price += 1
    
    print(f"  [Akshare] 合约{len(contracts)}个，有价格{has_price}个")
    return contracts


def _get_akshare_szse_contracts(etf_code, trade_date):
    """akshare备用：深交所ETF期权（合约列表+持仓+前结价来自option_current_day_szse）"""
    import akshare as ak
    
    name_prefix_map = {
        '159915': '创业板ETF',
    }
    name_prefix = name_prefix_map.get(etf_code, etf_code)
    
    try:
        time.sleep(0.3)
        all_contracts = ak.option_current_day_szse()
        if all_contracts is None or all_contracts.empty:
            print(f"  [Akshare] option_current_day_szse返回空")
            return []
        # 按标的筛选
        target = all_contracts[all_contracts['合约简称'].str.startswith(name_prefix)]
        if target.empty:
            print(f"  [Akshare] 未找到{name_prefix}相关合约")
            return []
    except Exception as e:
        print(f"  [Akshare] 获取深交所合约失败: {e}")
        return []
    
    # 取最近两个到期月
    target['到期日'] = pd.to_datetime(target['到期日']).dt.strftime('%Y%m%d')
    expiry_dates = sorted(target['到期日'].unique())[:2]
    target = target[target['到期日'].isin(expiry_dates)]
    
    contracts = []
    for _, row in target.iterrows():
        cp_type = str(row.get('合约类型', ''))
        cp = 'C' if ('购' in cp_type or '认购' in cp_type) else 'P'
        
        # 用前结算价作为价格近似
        close = float(row.get('前结算价', 0) or 0)
        oi = int(float(row.get('合约总持仓', 0) or 0))
        
        contracts.append({
            'ts_code': str(row['合约代码']),
            'call_put': cp,
            'exercise_price': float(row['行权价']),
            'maturity_date': str(row['到期日']),
            'close': close,
            'volume': 0,
            'oi': oi,
            'amount': 0,
        })
    
    has_price = sum(1 for c in contracts if c['close'] > 0)
    has_oi = sum(1 for c in contracts if c['oi'] > 0)
    print(f"  [Akshare] 合约{len(contracts)}个，有价格{has_price}个，有持仓{has_oi}个")
    return contracts


def _get_akshare_cffex_mo_contracts(trade_date):
    """akshare备用：中金所中证1000股指期权（MO）"""
    import akshare as ak
    
    try:
        # 获取合约月份列表
        time.sleep(0.3)
        result = ak.option_cffex_zz1000_list_sina()
        if not result or '中证1000指数' not in result:
            print(f"  [Akshare] MO合约月份列表为空")
            return []
        months = result['中证1000指数']
        if not months:
            return []
        # 取最近两个月
        months = sorted(months)[:2]
    except Exception as e:
        print(f"  [Akshare] MO合约列表失败: {e}")
        return []
    
    all_contracts = []
    for month_symbol in months:
        try:
            time.sleep(0.3)
            df = ak.option_cffex_zz1000_spot_sina(symbol=month_symbol)
            if df is None or df.empty:
                continue
            
            # 到期日：从合约标识推断，mo2608 -> 2026年8月第三个周五
            year = 2000 + int(month_symbol[2:4])
            month = int(month_symbol[4:6])
            
            for _, row in df.iterrows():
                strike = float(row['行权价'])
                call_id = str(row.get('看涨合约-标识', ''))
                put_id = str(row.get('看跌合约-标识', ''))
                
                # 看涨
                call_price = float(row.get('看涨合约-最新价', 0) or 0)
                call_oi = int(float(row.get('看涨合约-持仓量', 0) or 0))
                if call_price > 0:
                    all_contracts.append({
                        'ts_code': call_id,
                        'call_put': 'C',
                        'exercise_price': strike,
                        'maturity_month': (year, month),
                        'close': call_price,
                        'volume': 0,
                        'oi': call_oi,
                        'amount': 0,
                    })
                
                # 看跌
                put_price = float(row.get('看跌合约-最新价', 0) or 0)
                put_oi = int(float(row.get('看跌合约-持仓量', 0) or 0))
                if put_price > 0:
                    all_contracts.append({
                        'ts_code': put_id,
                        'call_put': 'P',
                        'exercise_price': strike,
                        'maturity_month': (year, month),
                        'close': put_price,
                        'volume': 0,
                        'oi': put_oi,
                        'amount': 0,
                    })
        except Exception as e:
            print(f"  [Akshare] MO {month_symbol} 行情失败: {e}")
    
    # 补充到期日（每月第三个周五）
    from calendar import monthrange
    for c in all_contracts:
        y, m = c.pop('maturity_month')
        # 找第三个周五
        first_day, days = monthrange(y, m)
        # first_day: 0=周一 ... 4=周五 ... 6=周日
        fri_offset = (4 - first_day) % 7  # 第一个周五是第几天
        third_fri = fri_offset + 15  # 第三个周五
        expiry = f"{y}{m:02d}{third_fri:02d}"
        c['maturity_date'] = expiry
    
    print(f"  [Akshare] MO合约: {len(all_contracts)}个")
    return all_contracts


def get_option_contracts_akshare(etf_code, exchange, trade_date):
    """akshare备用链路：获取期权合约数据（按交易所路由）"""
    print(f"  [降级] 尝试akshare备用数据源...")
    
    if exchange == 'SSE':
        contracts = _get_akshare_sse_contracts(etf_code, trade_date)
    elif exchange == 'SZSE':
        contracts = _get_akshare_szse_contracts(etf_code, trade_date)
    elif exchange == 'CFFEX':
        contracts = _get_akshare_cffex_mo_contracts(trade_date)
    else:
        print(f"  [Akshare] 不支持的交易所: {exchange}")
        return []
    
    # 过滤: 有有效价格的合约才保留（用于SKEW计算）
    valid = [c for c in contracts if c['close'] > 0 and c['exercise_price'] > 0]
    calls = [c for c in valid if c['call_put'] == 'C']
    puts = [c for c in valid if c['call_put'] == 'P']
    
    if len(calls) < 3 or len(puts) < 3:
        print(f"  [Akshare] 有效合约不足: 购{len(calls)}个/沽{len(puts)}个 (各需至少3个)")
        return []
    
    print(f"  [Akshare] 有效合约: 购{len(calls)}个/沽{len(puts)}个")
    return valid


def get_option_contracts(exchange, opt_code_filter, trade_date, etf_code=None):
    """获取期权合约列表+日线数据（Tushare主 + akshare备，每个标的独立降级）
    
    Args:
        exchange: 交易所 SSE/SZSE/CFFEX
        opt_code_filter: Tushare opt_code过滤
        trade_date: 交易日期 YYYYMMDD
        etf_code: ETF标的代码（备用链路用）
    """
    # 主链路: Tushare
    contracts = get_option_contracts_tushare(exchange, opt_code_filter, trade_date)
    
    # 检查是否有足够有效合约
    calls = [c for c in contracts if c['call_put'] == 'C']
    puts = [c for c in contracts if c['call_put'] == 'P']
    
    if len(calls) >= 3 and len(puts) >= 3:
        return contracts
    
    # 合约不足，降级到akshare
    print(f"  [WARN] Tushare合约不足(购{len(calls)}/沽{len(puts)})，尝试akshare备用...")
    fallback = get_option_contracts_akshare(etf_code, exchange, trade_date)
    if fallback:
        return fallback
    
    print(f"  [ERROR] 所有数据源均失败，返回空列表")
    return []

# ============ SKEW计算 ============

def calc_skew(contracts, spot_price, trade_date, r=0.03):
    """从合约数据计算SKEW指标"""
    calls = [c for c in contracts if c['call_put'] == 'C']
    puts = [c for c in contracts if c['call_put'] == 'P']
    if len(calls) < 3 or len(puts) < 3: return None

    # 过滤掉dte<3天的合约（避免临到期扭曲IV）
    trade_dt = datetime.datetime.strptime(trade_date, "%Y%m%d")
    valid_contracts = [c for c in contracts if (datetime.datetime.strptime(c['maturity_date'], "%Y%m%d") - trade_dt).days >= 3]
    if len(valid_contracts) < 6:  # 不够用则放宽到>=2天
        valid_contracts = [c for c in contracts if (datetime.datetime.strptime(c['maturity_date'], "%Y%m%d") - trade_dt).days >= 2]
    if not valid_contracts:
        valid_contracts = contracts  # 最后兜底

    calls = [c for c in valid_contracts if c['call_put'] == 'C']
    puts = [c for c in valid_contracts if c['call_put'] == 'P']
    if len(calls) < 3 or len(puts) < 3:
        calls = [c for c in contracts if c['call_put'] == 'C']
        puts = [c for c in contracts if c['call_put'] == 'P']

    calls.sort(key=lambda x: x['exercise_price'])
    puts.sort(key=lambda x: x['exercise_price'])

    # ATM
    atm_strike = min(valid_contracts, key=lambda x: abs(x['exercise_price'] - spot_price))['exercise_price']
    atm_call = min(calls, key=lambda x: abs(x['exercise_price'] - atm_strike))
    atm_put = min(puts, key=lambda x: abs(x['exercise_price'] - atm_strike))

    # 最近到期日（基于过滤后的合约）
    nearest_expire = min(c['maturity_date'] for c in valid_contracts)
    expiry_dt = datetime.datetime.strptime(nearest_expire, "%Y%m%d")
    T = max((expiry_dt - trade_dt).days / 365.0, 1/365)

    # ATM IV
    atm_call_iv = bs_iv(spot_price, atm_call['exercise_price'], T, r, atm_call['close'], 'call')
    atm_put_iv = bs_iv(spot_price, atm_put['exercise_price'], T, r, atm_put['close'], 'put')
    atm_iv = ((atm_call_iv or 0) + (atm_put_iv or 0)) / 2
    if atm_iv < 0.01: atm_iv = atm_call_iv or atm_put_iv or 0.2

    # 25-delta strikes
    if atm_iv > 0.01:
        put_25d_K = delta_to_strike(spot_price, T, r, atm_iv, 0.25, is_put=True)
        call_25d_K = delta_to_strike(spot_price, T, r, atm_iv, 0.25, is_put=False)
    else:
        put_25d_K = atm_strike * 0.95
        call_25d_K = atm_strike * 1.05

    near_put25 = min(puts, key=lambda x: abs(x['exercise_price'] - put_25d_K))
    near_call25 = min(calls, key=lambda x: abs(x['exercise_price'] - call_25d_K))

    put_25d_iv = bs_iv(spot_price, near_put25['exercise_price'], T, r, near_put25['close'], 'put')
    call_25d_iv = bs_iv(spot_price, near_call25['exercise_price'], T, r, near_call25['close'], 'call')

    iv_skew = (put_25d_iv or 0) - (call_25d_iv or 0)

    # CBOE SKEW
    if atm_iv > 0.01 and put_25d_iv is not None:
        cboe_skew = 100 - 10 * (-3 * (put_25d_iv - atm_iv) / atm_iv)
    else:
        cboe_skew = 100.0

    # PCR
    total_call_vol = sum(c['volume'] for c in calls)
    total_put_vol = sum(p['volume'] for p in puts)
    total_call_oi = sum(c['oi'] for c in calls)
    total_put_oi = sum(p['oi'] for p in puts)
    total_call_amt = sum(c['amount'] for c in calls)
    total_put_amt = sum(p['amount'] for p in puts)

    vol_pcr = total_put_vol / total_call_vol if total_call_vol > 0 else 0
    oi_pcr = total_put_oi / total_call_oi if total_call_oi > 0 else 0
    amt_pcr = total_put_amt / total_call_amt if total_call_amt > 0 else 0

    # 支撑/压力
    call_max_oi = max(calls, key=lambda x: x['oi']) if calls else None
    put_max_oi = max(puts, key=lambda x: x['oi']) if puts else None
    resistance = call_max_oi['exercise_price'] if call_max_oi and call_max_oi['oi'] > 0 else None
    support = put_max_oi['exercise_price'] if put_max_oi and put_max_oi['oi'] > 0 else None

    # 方向
    if cboe_skew > 120: direction = "🔴严重偏空"
    elif cboe_skew > 105: direction = "🟠偏空"
    elif cboe_skew < 95: direction = "🟢偏多"
    elif cboe_skew < 100: direction = "🟢偏多"
    else: direction = "⚪中性"

    return {
        'spot_price': round(spot_price, 4),
        'atm_iv': round(atm_iv * 100, 2),
        'put_25d_iv': round((put_25d_iv or 0) * 100, 2),
        'call_25d_iv': round((call_25d_iv or 0) * 100, 2),
        'iv_skew': round(iv_skew * 100, 2),
        'cboe_skew': round(cboe_skew, 2),
        'vol_pcr': round(vol_pcr, 2),
        'oi_pcr': round(oi_pcr, 2),
        'amt_pcr': round(amt_pcr, 2),
        'support': support,
        'resistance': resistance,
        'direction': direction,
        'expiry': nearest_expire,
        'dte': round(T * 365),
    }



def calc_risk_reversal(contracts, spot_price, trade_date, r=0.03, target_delta=0.25):
    """计算 Risk Reversal（风险逆转）
    
    标准定义: RR(Δ) = IV_call(+Δ) - IV_put(-Δ)
    即相同到期日、相同绝对Delta的虚值看涨IV 减 虚值看跌IV
    
    > 0 → 看涨期权更贵 → 市场隐含方向向上（多头押注）
    < 0 → 看跌期权更贵 → 市场隐含方向向下（空头押注/避险）
    
    实现: 对每个到期日，计算所有合约的实际Delta，
         选出最接近 target_delta 的虚值看涨和最接近 -target_delta 的虚值看跌
    """
    trade_dt = datetime.datetime.strptime(trade_date, "%Y%m%d")
    
    # 过滤：至少2天到期
    valid = [c for c in contracts if (datetime.datetime.strptime(c['maturity_date'], "%Y%m%d") - trade_dt).days >= 2]
    if len(valid) < 4:
        valid = contracts  # 兜底
    
    # 按到期日分组
    by_expiry = defaultdict(list)
    for c in valid:
        by_expiry[c['maturity_date']].append(c)
    
    expiry_results = {}
    
    for exp_date, exp_contracts in by_expiry.items():
        calls = [c for c in exp_contracts if c['call_put'] == 'C']
        puts = [c for c in exp_contracts if c['call_put'] == 'P']
        if len(calls) < 3 or len(puts) < 3:
            continue
        
        exp_dt = datetime.datetime.strptime(exp_date, "%Y%m%d")
        T = max((exp_dt - trade_dt).days / 365.0, 1/365)
        dte = (exp_dt - trade_dt).days
        
        # 计算每个合约的IV和实际Delta
        call_data = []
        for c in calls:
            iv = bs_iv(spot_price, c['exercise_price'], T, r, c['close'], 'call')
            if iv and iv > 0.005 and iv < 3:
                d1 = bs_d1(spot_price, c['exercise_price'], T, r, iv)
                delta = norm.cdf(d1) if d1 is not None else None
                if delta is not None and 0 < delta < 1:
                    call_data.append({
                        'strike': c['exercise_price'],
                        'iv': iv,
                        'delta': delta,
                        'oi': c.get('oi', 0),
                    })
        
        put_data = []
        for c in puts:
            iv = bs_iv(spot_price, c['exercise_price'], T, r, c['close'], 'put')
            if iv and iv > 0.005 and iv < 3:
                d1 = bs_d1(spot_price, c['exercise_price'], T, r, iv)
                delta = norm.cdf(d1) - 1 if d1 is not None else None  # 看跌delta为负
                if delta is not None and -1 < delta < 0:
                    put_data.append({
                        'strike': c['exercise_price'],
                        'iv': iv,
                        'delta': delta,
                        'oi': c.get('oi', 0),
                    })
        
        if not call_data or not put_data:
            continue
        
        # 找虚值看涨: delta最接近 +target_delta（且K > S）
        otm_calls = [c for c in call_data if c['strike'] > spot_price and c['delta'] < 0.5]
        if not otm_calls:
            otm_calls = [c for c in call_data if c['delta'] < 0.5]
        if not otm_calls:
            continue
        
        near_call = min(otm_calls, key=lambda x: abs(x['delta'] - target_delta))
        
        # 找虚值看跌: delta最接近 -target_delta（且K < S）
        otm_puts = [p for p in put_data if p['strike'] < spot_price and p['delta'] > -0.5]
        if not otm_puts:
            otm_puts = [p for p in put_data if p['delta'] > -0.5]
        if not otm_puts:
            continue
        
        near_put = min(otm_puts, key=lambda x: abs(x['delta'] - (-target_delta)))
        
        # 配对质量检查：实际delta离目标差距太大则跳过
        call_delta_err = abs(near_call['delta'] - target_delta)
        put_delta_err = abs(near_put['delta'] - (-target_delta))
        if call_delta_err > 0.20 or put_delta_err > 0.20:
            continue  # 配对误差过大，放弃这个到期日
        
        # Risk Reversal = IV_call(+Δ) - IV_put(-Δ)
        rr = near_call['iv'] - near_put['iv']
        
        # 权重 = 两个合约OI之和
        weight = near_call['oi'] + near_put['oi']
        
        expiry_results[exp_date] = {
            'dte': dte,
            'call_strike': near_call['strike'],
            'put_strike': near_put['strike'],
            'call_iv': round(near_call['iv'] * 100, 2),
            'put_iv': round(near_put['iv'] * 100, 2),
            'rr': round(rr * 100, 2),
            'call_delta': round(near_call['delta'], 4),
            'put_delta': round(near_put['delta'], 4),
            'weight': weight,
        }
    
    if not expiry_results:
        return None
    
    # 选最近到期日作为主RR
    sorted_expiries = sorted(expiry_results.keys())
    nearest_exp = sorted_expiries[0]
    nearest_rr = expiry_results[nearest_exp]
    
    # 加权平均RR（所有到期日按OI加权）
    total_weight = sum(v['weight'] for v in expiry_results.values())
    if total_weight > 0:
        weighted_rr = sum(v['rr'] * v['weight'] for v in expiry_results.values()) / total_weight
    else:
        weighted_rr = nearest_rr['rr']
    
    # 方向判断：基于绝对阈值
    # A股期权因投资者结构，看跌天然更贵（RR常态为负）
    # 阈值基于6个标的横截面分布的经验值
    rr_val = nearest_rr['rr']
    if rr_val > -1.0:
        direction = "🟢 偏多"
    elif rr_val > -3.0:
        direction = "⚪ 中性"
    elif rr_val > -6.0:
        direction = "🟠 偏空"
    else:
        direction = "🔴 看空"
    
    return {
        'rr': round(nearest_rr['rr'], 2),
        'weighted_rr': round(weighted_rr, 2),
        'direction': direction,
        'nearest_expiry': nearest_exp,
        'nearest_dte': nearest_rr['dte'],
        'call_iv': nearest_rr['call_iv'],
        'put_iv': nearest_rr['put_iv'],
        'call_strike': nearest_rr['call_strike'],
        'put_strike': nearest_rr['put_strike'],
        'call_delta': nearest_rr.get('call_delta'),
        'put_delta': nearest_rr.get('put_delta'),
        'by_expiry': {k: {'rr': v['rr'], 'dte': v['dte'], 'call_iv': v['call_iv'], 'put_iv': v['put_iv']}
                      for k, v in expiry_results.items()},
    }


# ============ GEX计算 ============

def calc_gex(contracts, spot_price, trade_date, r=0.03):
    """从合约数据计算GEX"""
    today = datetime.datetime.strptime(trade_date, "%Y%m%d")
    strikes_data = {}

    for c in contracts:
        try:
            exp_str = c['maturity_date']
            if len(exp_str) < 8: continue
            exp_dt = datetime.datetime.strptime(exp_str[:8], "%Y%m%d")
            T = (exp_dt - today).days / 365.0
            if T < 3/365: continue

            K = c['exercise_price']
            if K <= 0 or spot_price <= 0: continue

            if K not in strikes_data:
                strikes_data[K] = {'call_oi': 0, 'put_oi': 0, 'call_gamma': None, 'put_gamma': None}

            oi = c['oi']
            mkt = c['close']
            iv = bs_iv(spot_price, K, T, r, mkt, 'call' if c['call_put'] == 'C' else 'put')
            g = bs_gamma(spot_price, K, T, r, iv) if iv else None

            if c['call_put'] == 'C':
                strikes_data[K]['call_oi'] += oi
                if g: strikes_data[K]['call_gamma'] = g
            else:
                strikes_data[K]['put_oi'] += oi
                if g: strikes_data[K]['put_gamma'] = g
        except: continue

    if not strikes_data: return None

    # 累计GEX
    total_gex = 0
    flip_point = None
    prev_sign = None
    details = []

    for K in sorted(strikes_data.keys()):
        d = strikes_data[K]
        cg = d['call_gamma'] or 0
        pg = d['put_gamma'] or 0
        net = (d['call_oi'] * cg - d['put_oi'] * pg) * 100 * (spot_price ** 2)
        total_gex += net

        details.append({'strike': K, 'call_oi': d['call_oi'], 'put_oi': d['put_oi'],
                        'net_gex': net, 'cumulative_gex': total_gex})

        cur_sign = 1 if total_gex >= 0 else -1
        if prev_sign == 1 and cur_sign == -1 and flip_point is None:
            flip_point = K
        prev_sign = cur_sign

    # 风险判断
    if total_gex < -1e9: risk = "🔴高"
    elif total_gex < -1e8: risk = "🟡中"
    else: risk = "🟢低"

    return {
        'total_gex': total_gex,
        'total_gex_yi': round(total_gex / 1e8, 2),
        'flip_point': flip_point,
        'risk': risk,
        'risk_label': risk,  # 兼容旧字段名
        'net_gex': round(total_gex / 1e8, 2),  # 兼容旧字段名
        'spot_price': None,  # 由调用方填充
        'details_count': len(details),
        'key_details': details[-10:] if len(details) > 10 else details,
    }

# ============ 升贴水计算 ============

def third_friday(y, m):
    d = datetime.date(y, m, 1)
    f = d + timedelta(days=(4-d.weekday())%7)
    return (f + timedelta(weeks=2)).strftime('%Y%m%d')

def find_cq_nq(date_str):
    td = datetime.datetime.strptime(date_str, '%Y%m%d')
    cy, cm = td.year, td.month
    qs = []
    for off in range(0, 30):
        y = cy + (cm-1+off)//12; m = (cm-1+off)%12+1
        if m in BASIS_QUARTERS:
            if third_friday(y, m) >= date_str:
                qs.append(f'{y}{m:02d}')
            if len(qs) >= 2: break
    return (qs[0], qs[1]) if len(qs) >= 2 else (qs[0] if qs else None, None)

def _get_spot_akshare(idx_code, start_date, end_date):
    """akshare 获取指数现货日线（不耗Tushare额度）
    idx_code: '000300.SH' 格式，转换为 akshare 的 sh000300 格式
    返回: {date_str(YYYYMMDD): close_price}
    """
    import akshare as ak
    # 转换代码格式: 000300.SH -> sh000300, 000905.SH -> sh000905
    code, exch = idx_code.split('.')
    symbol = f"sh{code}" if exch == 'SH' else f"sz{code}"
    df = ak.stock_zh_index_daily(symbol=symbol)
    if df is None or len(df) == 0:
        return {}
    df.columns = [c.lower() for c in df.columns]
    df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y%m%d')
    df = df[(df['date'] >= start_date) & (df['date'] <= end_date)]
    df = df.sort_values('date').reset_index(drop=True)
    return dict(zip(df['date'], df['close']))


def _get_futures_akshare(product, ym, start_date, end_date):
    """akshare 获取期货合约日线（新浪财经源）
    product: 'IF'/'IC'/'IM'
    ym: '202609' 格式
    返回: DataFrame 含 date(YYYYMMDD), close, volume, oi(持仓量)
    """
    import akshare as ak
    # 合约代码: IF2609
    contract_code = f"{product}{ym[2:]}"
    try:
        df = ak.futures_zh_daily_sina(symbol=contract_code)
    except Exception:
        return None
    if df is None or len(df) == 0:
        return None
    df.columns = [c.lower() for c in df.columns]
    df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y%m%d')
    df = df[(df['date'] >= start_date) & (df['date'] <= end_date)]
    df = df.sort_values('date').reset_index(drop=True)
    # 列名对齐: hold -> oi
    if 'hold' in df.columns and 'oi' not in df.columns:
        df['oi'] = df['hold']
    return df


def calc_basis(trade_date):
    """计算IF/IC/IM升贴水
    数据源优先级: akshare(主) -> tushare(备)
    返回: (rows, missing_products)
      - rows: 升贴水数据列表
      - missing_products: 数据缺失的品种列表
    """
    end = trade_date
    start = (datetime.datetime.strptime(trade_date, '%Y%m%d') - timedelta(days=700)).strftime('%Y%m%d')
    rows = []
    missing_products = []

    for p, info in BASIS_PRODUCTS.items():
        # ---- Step 1: 获取现货数据 ----
        spot = {}
        # 优先 akshare
        try:
            spot = _get_spot_akshare(info['idx'], start, end)
        except Exception:
            spot = {}

        # akshare 失败则降级 tushare
        if not spot:
            try:
                time.sleep(API_SLEEP)
                sdf = pro.index_daily(ts_code=info['idx'], start_date=start, end_date=end)
                if sdf is not None and not sdf.empty:
                    sdf = sdf.sort_values('trade_date').reset_index(drop=True)
                    sdf['trade_date'] = sdf['trade_date'].astype(str)
                    spot = dict(zip(sdf['trade_date'], sdf['close']))
            except Exception:
                pass

        if not spot:
            missing_products.append(p)
            continue

        dates = sorted(spot.keys())

        # collect needed ym
        needed = set()
        for d in dates:
            cq, nq = find_cq_nq(d)
            if cq: needed.add(cq)
            if nq: needed.add(nq)

        # ---- Step 2: 获取期货合约数据 ----
        fut = {}
        for ym in sorted(needed):
            df = None
            # 优先 akshare
            try:
                df = _get_futures_akshare(p, ym, start, end)
            except Exception:
                df = None

            # akshare 失败则降级 tushare
            if df is None or len(df) == 0:
                try:
                    tc = f'{p}{ym[2:]}.CFX'
                    time.sleep(API_SLEEP)
                    tdf = pro.fut_daily(ts_code=tc, start_date=start, end_date=end)
                    if tdf is not None and len(tdf) > 0:
                        tdf = tdf.sort_values('trade_date').reset_index(drop=True)
                        tdf['trade_date'] = tdf['trade_date'].astype(str)
                        # 列名对齐
                        tdf = tdf.rename(columns={'trade_date': 'date', 'vol': 'volume'})
                        df = tdf
                except Exception:
                    pass

            if df is not None and len(df) > 0:
                fut[ym] = df

        # 如果没有任何期货合约数据，标记为缺失
        if not fut:
            missing_products.append(p)
            continue

        # ---- Step 3: 计算当季/下季基差 ----
        product_has_data = False
        for which in ['cq', 'nq']:
            label = '当季' if which == 'cq' else '下季'
            recs = []
            for d in dates:
                cq_ym, nq_ym = find_cq_nq(d)
                ym = cq_ym if which == 'cq' else nq_ym
                if ym is None or ym not in fut: continue
                day_df = fut[ym][fut[ym]['date'] == d]
                if len(day_df) == 0 or d not in spot: continue
                exp = third_friday(int(ym[:4]), int(ym[4:]))
                dte = (datetime.datetime.strptime(exp,'%Y%m%d')-datetime.datetime.strptime(d,'%Y%m%d')).days
                close_val = day_df['close'].values[0]
                vol_val = day_df['volume'].values[0] if 'volume' in day_df.columns else 0
                oi_val = day_df['oi'].values[0] if 'oi' in day_df.columns else 0
                recs.append({
                    'date': d, 'contract': f'{p}{ym[2:]}', 'F_close': close_val,
                    'S_close': spot[d], 'volume': vol_val, 'oi': oi_val,
                    'DTE': max(dte, 0)
                })
            if not recs: continue
            df = pd.DataFrame(recs)

            df['basis_rate'] = (df['F_close']/df['S_close']-1)*(365.0/df['DTE'].clip(lower=1))
            df.loc[(df['volume']==0)|(df['oi']==0), 'basis_rate'] = np.nan
            df['br_clean'] = df['basis_rate'].where(df['DTE'] >= 5, np.nan)

            # rank_252
            df['rank_252'] = df['br_clean'].rolling(252, min_periods=60).apply(
                lambda x: pd.Series(x).rank(pct=True).iloc[-1] if len(x.dropna()) >= 30 else np.nan, raw=False)

            # z_score_90
            df['med90'] = df['br_clean'].rolling(90, min_periods=30).median()
            df['resid'] = df['br_clean'] - df['med90']
            df['std90'] = df['resid'].rolling(90, min_periods=30).std()
            df['z90'] = df['resid'] / df['std90']

            L = df.iloc[-1]
            rk = L['rank_252']; z = L['z90']
            if pd.isna(rk) or pd.isna(z): sig = '数据不足'
            elif rk < 0.05 and z < -2.0: sig = '🔴极端恐慌(做多)'
            elif rk < 0.10 and z < -1.5: sig = '🟠深度贴水(观察)'
            elif rk > 0.95 and z > 2.0: sig = '🟣异常收敛(做空)'
            elif rk < 0.25 and z < -0.5: sig = '🟡温和贴水'
            elif rk > 0.75 and z > 0.5: sig = '🟢温和升水'
            else: sig = '⚪中性'

            rows.append({
                'product': p, 'name': info['name'], 'type': label,
                'contract': L['contract'], 'dte': int(L['DTE']),
                'basis_pts': round(L['F_close'] - L['S_close'], 1),
                'basis_rate': round(L['basis_rate'] * 100, 4) if not pd.isna(L['basis_rate']) else None,
                'rank_252': round(rk, 4) if not pd.isna(rk) else None,
                'z_score': round(z, 2) if not pd.isna(z) else None,
                'signal': sig,
            })
            product_has_data = True

        if not product_has_data:
            missing_products.append(p)

    return rows, missing_products

# ============ 主程序 ============

def main():
    import re
    raw_arg = sys.argv[1] if len(sys.argv) >= 2 else None
    if raw_arg and re.match(r'^\d{8}$', raw_arg):
        trade_date = raw_arg
    else:
        trade_date = datetime.datetime.now().strftime('%Y%m%d')
        if raw_arg:
            print(f"⚠️  日期参数格式无效: {raw_arg}，使用当天日期")
    print(f"=== 期权综合监控 (SKEW+GEX+升贴水) {trade_date} ===\n")

    result = {
        'trade_date': trade_date,
        'calculate_time': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'skew': {},
        'gex': {},
        'basis': [],
    }

    # ---- 数据采集重试机制 ----
    # 核心数据缺失超过阈值时自动重试，最多3次，每次间隔60秒
    MAX_RETRY = 3
    RETRY_INTERVAL = 60  # 秒

    def _collect_data():
        """采集一次完整数据，返回 (result_dict, missing_count)"""
        r = 0.03  # 无风险利率
        try:
            time.sleep(API_SLEEP)
            shibor_df = pro.shibor()
            if shibor_df is not None and len(shibor_df) > 0:
                yr = shibor_df[shibor_df['tenor'] == '1Y']
                if len(yr) > 0: r = float(yr.iloc[0]['rate']) / 100
        except: pass
        print(f"无风险利率: {r*100:.2f}%\n")

        _skew = {}
        _gex = {}
        _rr = {}
        for target in OPTION_TARGETS:
            name = target['name']
            code = target['code']
            exchange = target['exchange']
            opt_code = target['opt_code']
            underlying_code = target.get('underlying_code')

            print(f"[1/3] {name} ({code}) - 获取数据...")
            spot = get_spot_price(code, exchange, trade_date, underlying_code)
            if spot is None:
                print(f"  ⚠️ 无法获取价格，跳过")
                _skew[name] = None
                _gex[name] = None
                _rr[name] = None
                continue
            print(f"  现价: {spot:.3f}")

            print(f"  获取期权合约...")
            contracts = get_option_contracts(exchange, opt_code, trade_date, etf_code=code)
            if not contracts:
                print(f"  ❌ {name}: 所有数据源均无合约数据，标记为数据缺失")
                _skew[name] = None
                _gex[name] = None
                _rr[name] = None
                continue
            print(f"  有效合约: {len(contracts)}个")

            print(f"  计算SKEW...")
            skew_data = calc_skew(contracts, spot, trade_date, r)
            _skew[name] = skew_data
            if skew_data:
                print(f"  ✅ ATM IV={skew_data['atm_iv']}% SKEW={skew_data['cboe_skew']} {skew_data['direction']}")

            print(f"  计算GEX...")
            gex_data = calc_gex(contracts, spot, trade_date, r)
            _gex[name] = gex_data
            if gex_data:
                print(f"  ✅ Net GEX={gex_data['total_gex_yi']}亿 Flip={gex_data['flip_point']} 风险={gex_data['risk']}")

            print(f"  计算Risk Reversal...")
            rr_data = calc_risk_reversal(contracts, spot, trade_date, r)
            _rr[name] = rr_data
            if rr_data:
                print(f"  ✅ RR={rr_data['rr']:+.2f}% {rr_data['direction']} (DTE={rr_data['nearest_dte']})")

        print(f"\n[2/3] IF/IC/IM升贴水...")
        _basis_rows, _missing_basis = calc_basis(trade_date)
        for b in _basis_rows:
            rk = f"{b['rank_252']:.4f}" if b['rank_252'] else 'N/A'
            zs = f"{b['z_score']:+.2f}" if b['z_score'] else 'N/A'
            br = f"{b['basis_rate']:+.4f}%" if b['basis_rate'] is not None else 'N/A'
            print(f"  {b['product']} {b['type']} {b['contract']} DTE={b['dte']} 基差率={br} rank={rk} Z={zs} {b['signal']}")

        # 计算缺失数：SKEW缺失数 + 升贴水缺失品种数
        _skew_missing = sum(1 for v in _skew.values() if v is None)
        _missing = _skew_missing + len(_missing_basis)
        return _skew, _gex, _rr, _basis_rows, _missing_basis, _missing

    # 重试循环
    skew_data_all = {}
    gex_data_all = {}
    rr_data_all = {}
    basis_rows = []
    missing_basis = []
    final_missing = 0

    for attempt in range(1, MAX_RETRY + 1):
        print(f"\n{'='*50}")
        print(f"🔄 数据采集 第 {attempt}/{MAX_RETRY} 次尝试")
        print(f"{'='*50}")
        skew_data_all, gex_data_all, rr_data_all, basis_rows, missing_basis, final_missing = _collect_data()

        # 判断是否需要重试：SKEW缺失超过一半(>3) 或 升贴水全部缺失(3)
        skew_missing_count = sum(1 for v in skew_data_all.values() if v is None)
        need_retry = skew_missing_count > 3 or len(missing_basis) >= 3

        if not need_retry:
            print(f"\n✅ 第{attempt}次采集数据质量达标，停止重试")
            break
        elif attempt < MAX_RETRY:
            print(f"\n⚠️  数据缺失较多（SKEW缺{skew_missing_count}/6，升贴水缺{len(missing_basis)}/3）")
            print(f"   {RETRY_INTERVAL}秒后进行第{attempt+1}次重试...")
            time.sleep(RETRY_INTERVAL)
        else:
            print(f"\n❌ {MAX_RETRY}次重试后数据仍不完整，将带告警推送")

    result['skew'] = skew_data_all
    result['gex'] = gex_data_all
    result['risk_reversal'] = rr_data_all
    result['basis'] = basis_rows
    result['missing_basis_products'] = missing_basis
    if missing_basis:
        print(f"  ⚠️ 升贴水数据缺失品种: {', '.join(missing_basis)}")

    # ---- Part 2.5: 数据缺失检测 ----
    # SKEW 部分缺失数
    skew_missing = sum(1 for v in result['skew'].values() if v is None)
    # 升贴水缺失品种数
    basis_missing_count = len(missing_basis)

    # 收集告警信息
    data_warnings = []
    if basis_missing_count >= 3 - 2:  # 升贴水数据条数 < 3（即缺失超过1个品种，因为每个品种有当季+下季共2条，品种级缺失）
        # 计算实际缺失的品种数（3个品种中缺失的数量）
        available_products = set(b['product'] for b in basis_rows)
        missing_prod_count = len(BASIS_PRODUCTS) - len(available_products)
        if missing_prod_count > 0:
            data_warnings.append(f"⚠️ 升贴水模块数据缺失：{', '.join(missing_basis)} 无数据")
    if skew_missing > 2:
        missing_names = [n for n, v in result['skew'].items() if v is None]
        data_warnings.append(f"⚠️ SKEW模块数据缺失：{', '.join(missing_names)} 无数据（共{skew_missing}个标的）")

    result['data_warnings'] = data_warnings

    # ---- Part 3: 保存 ----
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'outputs')
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, f'{trade_date}_option_data.json')

    # 幂等检查：当天已推送过则永久跳过（用.pushed标记文件）
    import time as _time
    pushed_flag = output_file + '.pushed'
    skip_push = False
    if os.path.exists(pushed_flag):
        skip_push = True
        print(f"\n⚠️  今日 {trade_date} 期权监控已推送过，跳过飞书推送（幂等保护）")

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n[3/3] 结果已保存: {output_file}")
    
    # 生成飞书推送版md（保留用于存档）
    feishu_lines = [f"# 📊 期权综合监控 {trade_date}\n"]
    # 数据缺失告警放在最前面
    if data_warnings:
        for w in data_warnings:
            feishu_lines.append(f"> {w}")
        feishu_lines.append("")
    feishu_lines.append("## SKEW汇总\n")
    feishu_lines.append(f"|标的|ATM IV|IV Skew|SKEW|Vol PCR|方向|")
    feishu_lines.append(f"|---|---:|---:|---:|---:|---|")
    for name, d in result['skew'].items():
        if d:
            feishu_lines.append(f"|{name}|{d['atm_iv']:.2f}%|{d['iv_skew']:+.2f}%|{d['cboe_skew']:.2f}|{d['vol_pcr']:.2f}|{d['direction']}|")
        else:
            feishu_lines.append(f"|{name}|N/A|N/A|N/A|N/A|-|")
    
    # Risk Reversal
    rr_data = result.get('risk_reversal', {})
    if rr_data:
        feishu_lines.append("\n## 8.1 Risk Reversal（风险逆转）\n")
        feishu_lines.append("> 相同到期日、相同Delta的虚值看涨IV - 虚值看跌IV。正值→看涨更贵（押注向上），负值→看跌更贵（押注向下）\n")
        feishu_lines.append("|标的|RR|Call IV|Put IV|DTE|方向|")
        feishu_lines.append("|---|---:|---:|---:|---:|---|")
        for name, d in rr_data.items():
            if d:
                feishu_lines.append(f"|{name}|{d['rr']:+.2f}%|{d['call_iv']:.2f}%|{d['put_iv']:.2f}%|{d['nearest_dte']}|{d['direction']}|")
            else:
                feishu_lines.append(f"|{name}|N/A|N/A|N/A|-|-|")
    
    feishu_lines.append("\n## GEX汇总\n")
    feishu_lines.append(f"|标的|Net GEX(亿)|Flip Point|风险|")
    feishu_lines.append(f"|---|---:|---:|---|")
    for name, d in result['gex'].items():
        if d:
            fp = f"{d['flip_point']:.3f}" if d['flip_point'] else 'N/A'
            feishu_lines.append(f"|{name}|{d['total_gex_yi']:+.2f}|{fp}|{d['risk']}|")
        else:
            feishu_lines.append(f"|{name}|N/A|N/A|-|")
    
    feishu_lines.append("\n## 升贴水汇总\n")
    feishu_lines.append(f"|品种|类型|DTE|年化基差率|rank|Z|信号|")
    feishu_lines.append(f"|---|---:|---:|---:|---:|---:|---|")
    for b in basis_rows:
        rk = f"{b['rank_252']:.4f}" if b['rank_252'] else 'N/A'
        zs = f"{b['z_score']:+.2f}" if b['z_score'] else 'N/A'
        br = f"{b['basis_rate']:+.4f}%" if b['basis_rate'] is not None else 'N/A'
        feishu_lines.append(f"|{b['product']}|{b['type']}|{b['dte']}|{br}|{rk}|{zs}|{b['signal']}|")
    
    feishu_md = '\n'.join(feishu_lines)
    feishu_file = os.path.join(output_dir, f'{trade_date}_option_monitor_飞书推送.md')
    with open(feishu_file, 'w', encoding='utf-8') as f:
        f.write(feishu_md)
    print(f"飞书推送版已保存: {feishu_file}")
    
    # 飞书推送（交互式卡片）
    import sys as _sys
    _sys.path.insert(0, '/Coze/Drive/蓁宝/所有对话/主对话')
    from feishu_card_utils import build_option_monitor_card
    from feishu_push import push_card_to_feishu
    
    date_display = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}" if len(trade_date) == 8 else trade_date
    if skip_push:
        print("今天已推送，跳过飞书推送（幂等保护）")
    else:
        card = build_option_monitor_card(date_display, result['skew'], result['gex'], basis_rows,
                                         data_warnings=result.get('data_warnings', []),
                                         risk_reversal_data=result.get('risk_reversal', {}))
        success = push_card_to_feishu(card_dict=card)
        if success:
            with open(pushed_flag, 'w') as _pf:
                _pf.write(_time.strftime('%Y-%m-%d %H:%M:%S'))
            print("✅ 推送成功，已写幂等标记")
    print(f"=== 完成 ===")

    # 打印汇总表
    print("\n" + "="*80)
    print("SKEW汇总")
    print("="*80)
    print(f"{'标的':<10} {'ATM IV':>8} {'IV Skew':>9} {'SKEW':>7} {'Vol PCR':>8} {'OI PCR':>7} {'支撑':>6} {'压力':>6} {'方向'}")
    print("-"*80)
    for name, d in result['skew'].items():
        if d:
            s = f"{d['support']:.2f}" if d['support'] else '-'
            r2 = f"{d['resistance']:.2f}" if d['resistance'] else '-'
            print(f"{name:<10} {d['atm_iv']:>7.2f}% {d['iv_skew']:>+8.2f}% {d['cboe_skew']:>7.2f} {d['vol_pcr']:>8.2f} {d['oi_pcr']:>7.2f} {s:>6} {r2:>6} {d['direction']}")
        else:
            print(f"{name:<10} {'N/A':>8} {'N/A':>9} {'N/A':>7} {'N/A':>8} {'N/A':>7} {'-':>6} {'-':>6} -")

    print("\n" + "="*80)
    print("GEX汇总")
    print("="*80)
    print(f"{'标的':<10} {'Net GEX(亿)':>12} {'Flip Point':>12} {'风险'}")
    print("-"*50)
    for name, d in result['gex'].items():
        if d:
            fp = f"{d['flip_point']:.3f}" if d['flip_point'] else 'N/A'
            print(f"{name:<10} {d['total_gex_yi']:>+12.2f} {fp:>12} {d['risk']}")
        else:
            print(f"{name:<10} {'N/A':>12} {'N/A':>12} -")

    print("\n" + "="*80)
    print("升贴水汇总")
    print("="*80)
    print(f"{'品种':<5} {'类型':<5} {'合约':<9} {'DTE':>4} {'价差':>8} {'年化基差率':>11} {'rank':>7} {'Z':>7} {'信号'}")
    print("-"*75)
    for b in basis_rows:
        rk = f"{b['rank_252']:.4f}" if b['rank_252'] else 'N/A'
        zs = f"{b['z_score']:+.2f}" if b['z_score'] else 'N/A'
        br = f"{b['basis_rate']:+.4f}%" if b['basis_rate'] is not None else 'N/A'
        product = b['product']; typ = b['type']; contract = b['contract']; signal = b['signal']
        print(f"{product:<5} {typ:<5} {contract:<9} {b['dte']:>4} {b['basis_pts']:>+8.1f} {br:>11} {rk:>7} {zs:>7} {signal}")

if __name__ == '__main__':
    main()
