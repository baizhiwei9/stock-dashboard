# -*- coding: utf-8 -*-
"""
期权SKEW计算脚本 - 基于BS模型

使用tushare opt_daily数据计算CBOE SKEW指数

监控标的：
- 300ETF(510300), SSE  
- 500ETF(510500), SSE
- 易方达创业板ETF(159915), SZSE
- 科创50ETF(588000), SSE
- 中证1000股指期权(MO/CFFEX), 标的为000852指数

用法：
    python skew_calculator.py [YYYYMMDD] [ETF价格]
    例如：python skew_calculator.py 20240520 510300=4.852,510500=5.500

Author: SKEW Calculator
"""

import sys
import os
import json
import math
import datetime
from typing import Optional, Dict, List, Tuple
from scipy.stats import norm

# 添加data_utils到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_utils import get_opt_basic, get_opt_daily, get_a_quote_realtime, tencent_to_ts_code

# Tushare Token
TS_TOKEN = "a7c1ab22e3162aac86bf77323755b2d99e9cadfad4ae631a50574265"
import tushare as ts
ts.set_token(TS_TOKEN)
pro = ts.pro_api()


# ==================== Black-Scholes模型 ====================

def bs_call_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """
    Black-Scholes看涨期权定价
    
    Args:
        S: 标的资产价格
        K: 行权价
        T: 剩余到期时间（年）
        r: 无风险利率
        sigma: 波动率
    
    Returns:
        期权价格
    """
    if T <= 0 or sigma <= 0:
        return max(S - K, 0)
    
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    
    call_price = S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    return call_price


def bs_put_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """
    Black-Scholes看跌期权定价
    
    Args:
        S: 标的资产价格
        K: 行权价
        T: 剩余到期时间（年）
        r: 无风险利率
        sigma: 波动率
    
    Returns:
        期权价格
    """
    if T <= 0 or sigma <= 0:
        return max(K - S, 0)
    
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    
    put_price = K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
    return put_price


def bs_iv_call(S: float, K: float, T: float, r: float, market_price: float, 
               sigma_low: float = 0.001, sigma_high: float = 5.0, tol: float = 1e-6) -> float:
    """
    使用Newton-Raphson方法求解看涨期权的隐含波动率
    
    Args:
        S: 标的资产价格
        K: 行权价
        T: 剩余到期时间（年）
        r: 无风险利率
        market_price: 市场价格
        sigma_low: 波动率下界
        sigma_high: 波动率上界
        tol: 收敛容差
    
    Returns:
        隐含波动率
    """
    if T <= 0:
        return 0.0
    
    if market_price <= 0:
        return 0.0
    
    # 边界检查
    intrinsic = max(S - K, 0)
    if market_price < intrinsic:
        return 0.0
    
    sigma = 0.5  # 初始值
    for _ in range(100):
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        
        price = S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
        # Vega: 期权价格对波动率的敏感度
        vega = S * math.sqrt(T) * norm.pdf(d1)
        
        diff = market_price - price
        
        if abs(diff) < tol:
            break
        
        # Newton-Raphson迭代
        if abs(vega) > 1e-10:
            sigma = sigma + diff / vega
        
        if sigma < sigma_low:
            sigma = sigma_low
        elif sigma > sigma_high:
            sigma = sigma_high
    
    return sigma


def bs_iv_put(S: float, K: float, T: float, r: float, market_price: float,
              sigma_low: float = 0.001, sigma_high: float = 5.0, tol: float = 1e-6) -> float:
    """
    使用Newton-Raphson方法求解看跌期权的隐含波动率
    
    Args:
        S: 标的资产价格
        K: 行权价
        T: 剩余到期时间（年）
        r: 无风险利率
        market_price: 市场价格
        sigma_low: 波动率下界
        sigma_high: 波动率上界
        tol: 收敛容差
    
    Returns:
        隐含波动率
    """
    if T <= 0:
        return 0.0
    
    if market_price <= 0:
        return 0.0
    
    # 边界检查
    intrinsic = max(K - S, 0)
    if market_price < intrinsic:
        return 0.0
    
    sigma = 0.5  # 初始值
    for _ in range(100):
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        
        price = K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
        # Vega: 期权价格对波动率的敏感度
        vega = S * math.sqrt(T) * norm.pdf(d1)
        
        diff = market_price - price
        
        if abs(diff) < tol:
            break
        
        # Newton-Raphson迭代
        if abs(vega) > 1e-10:
            sigma = sigma + diff / vega
        
        if sigma < sigma_low:
            sigma = sigma_low
        elif sigma > sigma_high:
            sigma = sigma_high
    
    return sigma


def delta_to_strike(S: float, T: float, r: float, sigma: float, target_delta: float, is_put: bool = True) -> float:
    """
    根据Delta反推行权价
    
    Args:
        S: 标的资产价格
        T: 剩余到期时间（年）
        r: 无风险利率
        sigma: 波动率
        target_delta: 目标Delta
        is_put: 是否为看跌期权
    
    Returns:
        行权价
    """
    if T <= 0:
        return S
    
    d1 = norm.ppf(target_delta if not is_put else 1 - target_delta)
    K = S * math.exp(-(r + 0.5 * sigma ** 2) * T + sigma * math.sqrt(T) * d1)
    return K


# ==================== SKEW计算 ====================

def calculate_skew(etf_code: str, etf_name: str, etf_price: float, 
                   trade_date: str, r: float = 0.03) -> Optional[Dict]:
    """
    计算单个ETF的SKEW指标
    
    Args:
        etf_code: ETF代码，如'510300'
        etf_name: ETF名称，用于筛选期权合约
        etf_price: ETF当前价格
        trade_date: 交易日期，格式YYYYMMDD
        r: 无风险利率，默认3%
    
    Returns:
        包含SKEW指标的字典，失败返回None
    """
    import time  # 请求间隔
    
    # 确定交易所
    if etf_code in ('510300', '510500', '588000'):
        exchange = 'SSE'
    elif etf_code == '159915':
        exchange = 'SZSE'
    elif etf_code == 'MO':
        exchange = 'CFFEX'
    else:
        print(f"[WARN] 未知ETF/期权代码: {etf_code}")
        return None
    
    # opt_code筛选：格式如'OP510300.SH'表示300ETF期权
    # 精确匹配对应ETF的期权，注意后缀是.SH不是.SSE
    # CFFEX股指期权opt_code格式如'OP000852.SH'表示中证1000
    opt_code_map = {
        '510300': 'OP510300.SH',  # 沪深300ETF期权
        '510500': 'OP510500.SH',  # 中证500ETF期权
        '159915': 'OP159915.SZ',  # 创业板ETF期权（深交所）
        '588000': 'OP588000.SH',  # 科创50ETF期权
        'MO':     'OP000852.SH',  # 中证1000股指期权（CFFEX）
    }
    opt_code_target = opt_code_map.get(etf_code)
    
    try:
        # Step 1: 获取期权合约列表
        opt_list = pro.opt_basic(exchange=exchange)
        if opt_list is None or opt_list.empty:
            print(f"[ERROR] 获取期权列表失败")
            return None
        
        # Step 2: 用opt_code字段精确筛选对应ETF的期权
        if opt_code_target:
            opt_list = opt_list[opt_list['opt_code'] == opt_code_target]
        
        if opt_list.empty:
            print(f"[WARN] 未找到ETF期权({opt_code_target})")
            return None
        
        print(f"  找到{len(opt_list)}个期权合约")
        
        # Step 3: 用maturity_date筛选活跃合约（到期日 >= 当天）
        # maturity_date字段格式是YYYYMMDD字符串，字符串比较可以工作
        active = opt_list[opt_list['maturity_date'] >= trade_date]
        
        if active.empty:
            print(f"[WARN] 无活跃合约（到期日>= {trade_date}）")
            return None
        
        # Step 4: 找最近到期日，取最近到期的合约
        nearest_expire = active['maturity_date'].min()
        target_contracts = active[active['maturity_date'] == nearest_expire]
        
        if target_contracts.empty:
            print(f"[WARN] 当月合约列表为空")
            return None
        
        print(f"  最近到期日: {nearest_expire}, 合约数: {len(target_contracts)}")
        
        # Step 5: 获取opt_daily日线数据
        # CFFEX: 批量查询exchange='CFFEX'，再按ts_code过滤
        # SSE/SZSE: 逐个合约查询（单个ts_code查询方式）
        contracts = []
        
        if exchange == 'CFFEX':
            # CFFEX批量查询：一次拉取当日全部CFFEX期权日线
            try:
                daily_df = pro.opt_daily(exchange='CFFEX', start_date=trade_date, end_date=trade_date)
                if daily_df is None or daily_df.empty:
                    print(f"[WARN] CFFEX批量日线数据为空")
                else:
                    # 用opt_code过滤出MO品种（OP000852.SH）
                    target_ts_codes = set(target_contracts['ts_code'].tolist())
                    daily_filtered = daily_df[daily_df['ts_code'].isin(target_ts_codes)]
                    print(f"  CFFEX批量查询: {len(daily_df)}条, MO过滤后: {len(daily_filtered)}条")
                    
                    for _, row in target_contracts.iterrows():
                        ts_code = row['ts_code']
                        daily_row = daily_filtered[daily_filtered['ts_code'] == ts_code]
                        if not daily_row.empty:
                            contracts.append({
                                'code': ts_code,
                                'name': row['name'],
                                'exercise_price': float(row['exercise_price']),
                                'call_put': row['call_put'],
                                'maturity_date': row['maturity_date'],
                                'close': float(daily_row.iloc[0]['close']),
                                'volume': int(daily_row.iloc[0]['vol']),
                                'open_interest': int(daily_row.iloc[0]['oi']),
                                'amount': float(daily_row.iloc[0].get('amount', 0)),
                            })
            except Exception as e:
                print(f"[ERROR] CFFEX批量查询失败: {e}")
        else:
            # SSE/SZSE: 逐个合约查询
            for _, row in target_contracts.iterrows():
                opt_code = row['ts_code']  # tushare用ts_code字段
                try:
                    # 获取日线数据
                    df = pro.opt_daily(ts_code=opt_code, start_date=trade_date, end_date=trade_date)
                    if df is not None and not df.empty:
                        contracts.append({
                            'code': opt_code,
                            'name': row['name'],
                            'exercise_price': float(row['exercise_price']),  # 行权价
                            'call_put': row['call_put'],  # 'C'或'P'
                            'maturity_date': row['maturity_date'],
                            'close': float(df.iloc[0]['close']),  # 期权报价（元/份），BS模型直接用
                            'volume': int(df.iloc[0]['vol']),
                            'open_interest': int(df.iloc[0]['oi']),
                            'amount': float(df.iloc[0].get('amount', 0)),
                        })
                except Exception as e:
                    continue
                
                # 请求间隔，避免频率超限
                time.sleep(0.15)
        
        if len(contracts) < 5:
            print(f"[WARN] 有效合约数量不足: {len(contracts)}")
            return None
        
        # 分离看涨和看跌合约
        calls = [c for c in contracts if c['call_put'] == 'C']
        puts = [c for c in contracts if c['call_put'] == 'P']
        
        # 按行权价排序
        calls.sort(key=lambda x: x['exercise_price'])
        puts.sort(key=lambda x: x['exercise_price'])
        
        if not calls or not puts:
            print(f"[WARN] 看涨或看跌合约不足")
            return None
        
        # 找ATM行权价
        atm_strike = min(contracts, key=lambda x: abs(x['exercise_price'] - etf_price))['exercise_price']
        
        # 计算ATM附近的合约
        atm_calls = [c for c in calls if abs(c['exercise_price'] - atm_strike) < etf_price * 0.1]
        atm_puts = [c for c in puts if abs(c['exercise_price'] - atm_strike) < etf_price * 0.1]
        
        if not atm_calls or not atm_puts:
            print(f"[WARN] ATM附近合约不足")
            return None
        
        # 选择最接近ATM的合约
        atm_call = min(atm_calls, key=lambda x: abs(x['exercise_price'] - atm_strike))
        atm_put = min(atm_puts, key=lambda x: abs(x['exercise_price'] - atm_strike))
        
        # 计算到期时间（年）
        expiry_str = nearest_expire  # YYYYMMDD格式
        expiry_date = datetime.datetime.strptime(expiry_str, "%Y%m%d")
        trade_date_dt = datetime.datetime.strptime(trade_date, "%Y%m%d")
        T = max((expiry_date - trade_date_dt).days / 365.0, 1/365)
        
        # 计算隐含波动率
        atm_call_iv = bs_iv_call(etf_price, atm_call['exercise_price'], T, r, atm_call['close'])
        atm_put_iv = bs_iv_put(etf_price, atm_put['exercise_price'], T, r, atm_put['close'])
        atm_iv = (atm_call_iv + atm_put_iv) / 2
        
        # 寻找25-delta对应行权价
        # 使用ATM IV估算
        if atm_iv > 0.01:
            # 25-delta put对应的行权价
            put_25d_strike = delta_to_strike(etf_price, T, r, atm_iv, 0.25, is_put=True)
            # 25-delta call对应的行权价
            call_25d_strike = delta_to_strike(etf_price, T, r, atm_iv, 0.25, is_put=False)
        else:
            # 使用近似：ATM ± 5%
            put_25d_strike = atm_strike * 0.95
            call_25d_strike = atm_strike * 1.05
        
        # 找最接近的25-delta合约
        near_put_25d = min(puts, key=lambda x: abs(x['exercise_price'] - put_25d_strike))
        near_call_25d = min(calls, key=lambda x: abs(x['exercise_price'] - call_25d_strike))
        
        # 计算25-delta IV
        put_25d_iv = bs_iv_put(etf_price, near_put_25d['exercise_price'], T, r, near_put_25d['close'])
        call_25d_iv = bs_iv_call(etf_price, near_call_25d['exercise_price'], T, r, near_call_25d['close'])
        
        # 计算IV Skew
        iv_skew = put_25d_iv - call_25d_iv
        
        # 计算CBOE SKEW
        # SKEW = 100 - 10 * (-3 * (put_25d_IV - atm_iv) / atm_iv)
        if atm_iv > 0.01:
            cboe_skew = 100 - 10 * (-3 * (put_25d_iv - atm_iv) / atm_iv)
        else:
            cboe_skew = 100
        
        # 计算PCR（成交量/持仓量/成交额）
        total_call_vol = sum(c['volume'] for c in calls)
        total_put_vol = sum(p['volume'] for p in puts)
        total_call_oi = sum(c['open_interest'] for c in calls)
        total_put_oi = sum(p['open_interest'] for p in puts)
        total_call_amount = sum(c['amount'] for c in calls)
        total_put_amount = sum(p['amount'] for p in puts)
        
        vol_pcr = total_put_vol / total_call_vol if total_call_vol > 0 else 0
        oi_pcr = total_put_oi / total_call_oi if total_call_oi > 0 else 0
        amt_pcr = total_put_amount / total_call_amount if total_call_amount > 0 else 0
        
        # 找最大持仓行权价
        oi_by_strike = {}
        for c in calls:
            strike = c['exercise_price']
            oi_by_strike[strike] = oi_by_strike.get(strike, 0) + c['open_interest']
        for p in puts:
            strike = p['exercise_price']
            oi_by_strike[strike] = oi_by_strike.get(strike, 0) + p['open_interest']
        
        if oi_by_strike:
            max_oi_strike = max(oi_by_strike.items(), key=lambda x: x[1])
            support = max_oi_strike[0] if max_oi_strike[0] < atm_strike else None
            resistance = max_oi_strike[0] if max_oi_strike[0] > atm_strike else None
        else:
            support = None
            resistance = None
        
        # 方向判断
        if iv_skew > 0.05:
            direction = "偏多"  # Skew较高，市场担忧下跌
        elif iv_skew < -0.05:
            direction = "偏空"  # Skew较低，市场担忧上涨
        else:
            direction = "中性"
        
        # 组装结果
        result = {
            'etf_code': etf_code,
            'etf_name': etf_name,
            'trade_date': trade_date,
            'etf_price': etf_price,
            'atm_strike': atm_strike,
            'atm_iv': round(atm_iv * 100, 2),
            'put_25d_iv': round(put_25d_iv * 100, 2),
            'call_25d_iv': round(call_25d_iv * 100, 2),
            'iv_skew': round(iv_skew * 100, 2),
            'cboe_skew': round(cboe_skew, 2),
            'vol_pcr': round(vol_pcr, 2),
            'oi_pcr': round(oi_pcr, 2),
            'amt_pcr': round(amt_pcr, 2),
            'support': support,
            'resistance': resistance,
            'direction': direction,
            'expiry_date': expiry_str,
            'T': round(T * 365, 0),
            'total_call_vol': total_call_vol,
            'total_put_vol': total_put_vol,
            'atm_call_code': atm_call['code'],
            'atm_put_code': atm_put['code'],
        }
        
        return result
        
    except Exception as e:
        print(f"[ERROR] 计算SKEW失败: {e}")
        import traceback
        traceback.print_exc()
        return None


def infer_etf_price_from_options(opt_underlying: str, trade_date: str, exchange: str) -> Optional[float]:
    """
    从期权数据使用put-call parity反推ETF价格
    
    Args:
        opt_underlying: 期权标的，如'SH-510300'
        trade_date: 交易日期
    
    Returns:
        反推的ETF价格，失败返回None
    """
    try:
        # 获取期权列表
        opt_list = pro.opt_basic(exchange=exchange)
        if opt_list is None or opt_list.empty:
            return None
        
        # 筛选ATM附近的合约
        this_month_str = f"{trade_date[:2]}{trade_date[2:4]}"
        month_contracts = opt_list[opt_list['opt_code'].str.contains(f'-{this_month_str}')]
        
        # 获取ATM附近的合约数据
        atm_data = []
        for _, row in month_contracts.head(10).iterrows():
            opt_code = row['opt_code']
            df = pro.opt_daily(ts_code=opt_code, start_date=trade_date, end_date=trade_date)
            if df is not None and not df.empty:
                atm_data.append({
                    'call_put': row['call_put'],
                    'exercise_price': float(row['exercise_price']),
                    'close': float(df.iloc[0]['close']),
                })
        
        # 使用put-call parity反推
        # C - P = S - K*e^(-rT) => S = C - P + K*e^(-rT)
        calls = [d for d in atm_data if d['call_put'] == 'C']
        puts = [d for d in atm_data if d['call_put'] == 'P']
        
        if calls and puts:
            # 找最接近的call和put
            call = min(calls, key=lambda x: abs(x['exercise_price'] - 3.0))
            put = min(puts, key=lambda x: abs(x['exercise_price'] - 3.0))
            
            # 假设利率为3%
            S = call['close'] - put['close'] + call['exercise_price'] * 0.997  # 大约1天到期的折现
            return S if S > 0 else None
        
        return None
        
    except Exception as e:
        print(f"[WARN] 反推ETF价格失败: {e}")
        return None


def parse_etf_prices(price_str: str) -> Dict[str, float]:
    """
    解析ETF价格参数
    
    Args:
        price_str: 格式如'510300=4.852,510500=5.500'
    
    Returns:
        {etf_code: price}字典
    """
    result = {}
    if not price_str:
        return result
    
    pairs = price_str.split(',')
    for pair in pairs:
        if '=' in pair:
            code, price = pair.split('=')
            try:
                result[code.strip()] = float(price.strip())
            except:
                continue
    
    return result


# ==================== 主程序 ====================

def main():
    # 解析命令行参数
    if len(sys.argv) < 2:
        trade_date = datetime.datetime.now().strftime('%Y%m%d')
    else:
        trade_date = sys.argv[1]
    
    etf_prices = {}
    if len(sys.argv) >= 3:
        etf_prices = parse_etf_prices(sys.argv[2])
    
    print(f"=== SKEW计算 - {trade_date} ===\n")
    
    # ETF/指数配置
    etf_configs = [
        {'code': '510300', 'name': '300ETF', 'exchange': 'SSE'},
        {'code': '510500', 'name': '500ETF', 'exchange': 'SSE'},
        {'code': '159915', 'name': '创业板ETF', 'exchange': 'SZSE'},
        {'code': '588000', 'name': '科创50ETF', 'exchange': 'SSE'},
        {'code': 'MO', 'name': '中证1000股指', 'exchange': 'CFFEX', 'underlying_code': '000852'},
    ]
    
    all_results = []
    
    for etf in etf_configs:
        etf_code = etf['code']
        etf_name = etf['name']
        exchange = etf['exchange']
        
        print(f"处理 {etf_name} ({etf_code})...")
        
        # 获取标的资产价格（ETF或指数）
        etf_price = etf_prices.get(etf_code)
        
        if etf_price is None:
            if exchange == 'CFFEX':
                # CFFEX股指期权：获取指数价格
                underlying_code = etf.get('underlying_code', '000852')
                # 优先：腾讯行情获取指数实时价格
                try:
                    tencent_code = f"sh{underlying_code}"
                    df = get_a_quote_realtime([tencent_code])
                    if not df.empty and df.iloc[0]['price'] is not None:
                        etf_price = df.iloc[0]['price']
                        print(f"  从腾讯行情获取指数价格: {etf_price}")
                except Exception as e:
                    print(f"  腾讯行情获取指数价格失败: {e}")
                # 备选：tushare index_daily获取收盘价
                if etf_price is None:
                    try:
                        idx_df = pro.index_daily(ts_code=f"{underlying_code}.SH", start_date=trade_date, end_date=trade_date)
                        if idx_df is not None and not idx_df.empty:
                            etf_price = float(idx_df.iloc[0]['close'])
                            print(f"  从tushare获取指数收盘价: {etf_price:.2f}")
                    except Exception as e:
                        print(f"  tushare获取指数价格失败: {e}")
            else:
                # ETF期权：获取ETF价格
                try:
                    tencent_code = f"sh{etf_code}" if exchange == 'SSE' else f"sz{etf_code}"
                    df = get_a_quote_realtime([tencent_code])
                    if not df.empty and df.iloc[0]['price'] is not None:
                        etf_price = df.iloc[0]['price']
                        print(f"  从腾讯行情获取价格: {etf_price}")
                except Exception as e:
                    print(f"  获取行情失败: {e}")
        
        if etf_price is None and exchange != 'CFFEX':
            # ETF期权：尝试从期权数据反推
            opt_underlying = f"{'SH' if exchange == 'SSE' else 'SZ'}-{etf_code}"
            etf_price = infer_etf_price_from_options(opt_underlying, trade_date, exchange)
            if etf_price:
                print(f"  从期权反推价格: {etf_price:.3f}")
        
        if etf_price is None:
            print(f"  [WARN] 无法获取标的资产价格，跳过")
            continue
        
        # 计算SKEW
        result = calculate_skew(etf_code, etf_name, etf_price, trade_date)
        
        if result:
            all_results.append(result)
            print(f"  ATM IV: {result['atm_iv']:.2f}%")
            print(f"  IV Skew: {result['iv_skew']:.2f}%")
            print(f"  CBOE SKEW: {result['cboe_skew']:.2f}")
            print(f"  Vol PCR: {result['vol_pcr']:.2f}")
            print(f"  方向: {result['direction']}")
            print()
        else:
            print(f"  [ERROR] 计算失败\n")
    
    # 保存结果
    if all_results:
        output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'outputs')
        os.makedirs(output_dir, exist_ok=True)
        
        output_file = os.path.join(output_dir, f'{trade_date}_skew_data.json')
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump({
                'trade_date': trade_date,
                'calculate_time': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'results': all_results
            }, f, ensure_ascii=False, indent=2)
        
        print(f"结果已保存到: {output_file}")
    else:
        print("[ERROR] 没有有效结果")


if __name__ == '__main__':
    main()
