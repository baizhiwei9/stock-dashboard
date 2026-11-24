#!/usr/bin/env python3
"""
TDA流形旋转策略 - 数据获取模块
================================
使用Tushare + akshare获取:
1. 8只ETF日线数据(前复权)
2. 个股日线数据
3. 申万行业分类
4. 市值数据(Top2000筛选)
5. 交易日历

Tushare IP限频处理: sleep + 重试
"""

# ===== akshare 禁用 IPv6 (防止 RemoteDisconnected) =====
import socket
socket.setdefaulttimeout(30)
_orig_getaddrinfo = socket.getaddrinfo
socket.getaddrinfo = lambda *args, **kw: [r for r in _orig_getaddrinfo(*args, **kw) if r[0] == socket.AF_INET]

import time
import json
import sys
import os
import datetime
import numpy as np
import pandas as pd

# ---- dataHub 统一取数（首选）--------------------------------------------
_HUB_REPO = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/projects/datahub-three-tier"
_HUB_DEPS = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/py_deps"
for _p in (_HUB_REPO, _HUB_DEPS):
    if _p not in sys.path and os.path.isdir(_p):
        sys.path.insert(0, _p)


def _hub():
    try:
        from data_hub import edb
        return edb
    except Exception as exc:  # 缺依赖时回退原通道
        print(f"[dataHub] 不可用，回退原源: {type(exc).__name__}: {exc}")
        return None

import akshare as ak
import requests

# Tushare配置
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ts_config import init_tushare

_pro = None
_last_tushare_call = 0.0
_TUSHARE_MIN_INTERVAL = 1.5  # 秒，每次Tushare API调用最小间隔(代理限制10并发)

def get_pro():
    """懒加载Tushare pro对象"""
    global _pro
    if _pro is None:
        _pro = init_tushare()
    return _pro

def _tushare_throttle():
    """Tushare请求限频: 确保两次调用间隔至少_TUSHARE_MIN_INTERVAL秒"""
    global _last_tushare_call
    now = time.time()
    elapsed = now - _last_tushare_call
    if elapsed < _TUSHARE_MIN_INTERVAL:
        time.sleep(_TUSHARE_MIN_INTERVAL - elapsed)
    _last_tushare_call = time.time()

def ts_call(func, **kwargs):
    """带限频的Tushare API调用包装"""
    _tushare_throttle()
    pro = get_pro()
    api_func = getattr(pro, func)
    for attempt in range(3):
        try:
            result = api_func(**kwargs)
            return result
        except Exception as e:
            err = str(e).lower()
            if 'limit' in err or 'freq' in err or 'ip' in err or 'too many' in err:
                print(f"  Tushare限频, 等待5秒... ({func})")
                time.sleep(5)
                _last_tushare_call = time.time()
            else:
                if attempt < 2:
                    time.sleep(2)
                else:
                    raise
    return None

# ========== 策略常量 ==========

# 8个风格因子及其代理ETF
FACTOR_ETF_MAP = {
    '科技成长': {'etf': '159807', 'exchange': 'SZ', 'sw_industry': ['电子', '计算机']},
    '高端制造': {'etf': '159638', 'exchange': 'SZ', 'sw_industry': ['机械设备', '国防军工']},
    '新能源':   {'etf': '516090', 'exchange': 'SH', 'sw_industry': ['电力设备']},
    '医药健康': {'etf': '512010', 'exchange': 'SH', 'sw_industry': ['医药生物']},
    '大消费':   {'etf': '159928', 'exchange': 'SZ', 'sw_industry': ['食品饮料', '家用电器', '商贸零售']},
    '价值周期': {'etf': '512700', 'exchange': 'SH', 'sw_industry': ['银行', '非银金融']},
    '红利低波': {'etf': '512890', 'exchange': 'SH', 'sw_industry': ['交通运输', '公用事业', '钢铁']},
    '动量':     {'etf': '159967', 'exchange': 'SZ', 'sw_industry': ['综合', '轻工制造']},
}

ETF_CODES = [f"{v['etf']}" for v in FACTOR_ETF_MAP.values()]
ETF_NAMES = list(FACTOR_ETF_MAP.keys())

# 沪深300作为基准
BENCHMARK_CODE = '000300'
BENCHMARK_NAME = '沪深300'

# ========== 交易日历 ==========

_trade_calendar = None

def get_trade_calendar(start_date='20250101', end_date='20261231'):
    """获取交易日历"""
    global _trade_calendar
    if _trade_calendar is not None:
        return _trade_calendar
    
    for attempt in range(3):
        try:
            df = ts_call('trade_cal', exchange='SSE', start_date=start_date, end_date=end_date)
            if df is not None and len(df) > 0:
                _trade_calendar = df[df['is_open'] == 1]['cal_date'].sort_values().tolist()
                return _trade_calendar
        except Exception as e:
            print(f"获取交易日历失败(尝试{attempt+1}): {e}")
            time.sleep(3)
    
    # fallback: 用akshare
    try:
        df = ak.tool_trade_date_hist_sina()
        df['trade_date'] = df['trade_date'].astype(str).str.replace('-', '')
        _trade_calendar = df[df['trade_date'] >= start_date][df['trade_date'] <= end_date]['trade_date'].sort_values().tolist()
        return _trade_calendar
    except Exception as e:
        print(f"akshare获取交易日历也失败: {e}")
        return []

def is_trading_day(date_str=None):
    """判断是否为交易日"""
    if date_str is None:
        date_str = datetime.date.today().strftime('%Y%m%d')
    
    cal = get_trade_calendar()
    return date_str in cal

def get_recent_trading_days(n=5, end_date=None):
    """获取最近n个交易日"""
    if end_date is None:
        end_date = datetime.date.today().strftime('%Y%m%d')
    
    cal = get_trade_calendar()
    past = [d for d in cal if d <= end_date]
    return past[-n:] if len(past) >= n else past

# ========== ETF数据获取 ==========

_etf_cache = {}

def _etf_code_to_sina_tx(etf_code):
    """ETF代码转为新浪/腾讯格式: 159807->sz159807, 516090->sh516090"""
    if etf_code.startswith('5'):
        return f"sh{etf_code}"
    else:
        return f"sz{etf_code}"

def _fetch_etf_tushare(etf_code, start_date, end_date):
    """ETF 日线：优先 dataHub（CN.MARKET.ETF.PRICE，同源 fund_daily），失败回退 ts_call。"""
    ts_code = f"{etf_code}.SH" if etf_code.startswith('5') else f"{etf_code}.SZ"
    e = _hub()
    if e is not None:
        try:
            hub = e.get_dataset("CN.MARKET.ETF.PRICE", codes=[ts_code],
                                start_date=str(start_date), end_date=str(end_date))
            if hub is not None and len(hub):
                return hub.sort_values("trade_date").reset_index(drop=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[dataHub] ETF {ts_code} 失败，回退 tushare: {str(exc)[:80]}")
    df = ts_call('fund_daily', ts_code=ts_code, start_date=start_date, end_date=end_date)
    if df is not None and len(df) > 0:
        df = df.sort_values('trade_date').reset_index(drop=True)
        return df
    return None

def _fetch_etf_em(etf_code, start_date, end_date, adjust='qfq'):
    """东方财富: ak.fund_etf_hist_em"""
    df = ak.fund_etf_hist_em(symbol=etf_code, period='daily',
                             start_date=start_date, end_date=end_date, adjust=adjust)
    if df is not None and len(df) > 0:
        df['trade_date'] = df['日期'].astype(str).str.replace('-', '')
        df = df.sort_values('trade_date').reset_index(drop=True)
        return df
    return None

def _fetch_etf_sina(etf_code, start_date, end_date, adjust='qfq'):
    """新浪: ak.fund_etf_hist_sina"""
    sina_code = _etf_code_to_sina_tx(etf_code)
    df = ak.fund_etf_hist_sina(symbol=sina_code)
    if df is not None and len(df) > 0:
        # 新浪返回列名: date, open, high, low, close, volume
        df['trade_date'] = df['date'].astype(str).str.replace('-', '')
        df = df[(df['trade_date'] >= start_date) & (df['trade_date'] <= end_date)]
        df = df.sort_values('trade_date').reset_index(drop=True)
        # 统一列名(补齐 日期 列用于下游兼容)
        if '日期' not in df.columns:
            df['日期'] = df['date']
        return df
    return None

def _fetch_etf_tx(etf_code, start_date, end_date, adjust='qfq'):
    """腾讯: 直接HTTP请求 web.ifzq.gtimg.cn"""
    tx_code = _etf_code_to_sina_tx(etf_code)
    start_fmt = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}"
    end_fmt = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}"
    # qfq=前复权, hfq=后复权, 空字符串=不复权
    adjust_param = adjust if adjust in ('qfq', 'hfq') else ''
    url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={tx_code},day,{start_fmt},{end_fmt},640,{adjust_param}"
    resp = requests.get(url, timeout=15)
    data = resp.json()
    # 腾讯返回结构: {"data": {"sz159807": {"qfqday": [[date, open, close, high, low, volume], ...]}}}
    tx_data = data.get('data', {}).get(tx_code, {})
    kline_key = f'{adjust_param}day' if adjust_param else 'day'
    if kline_key not in tx_data:
        # 尝试其他可能的key
        for k in ('qfqday', 'hfqday', 'day'):
            if k in tx_data:
                kline_key = k
                break
    klines = tx_data.get(kline_key, [])
    if not klines:
        return None
    rows = []
    for item in klines:
        # [date, open, close, high, low, volume]
        if len(item) >= 5:
            rows.append({
                'trade_date': str(item[0]).replace('-', ''),
                '日期': item[0],
                'open': float(item[1]),
                'close': float(item[2]),
                'high': float(item[3]),
                'low': float(item[4]),
                'volume': float(item[5]) if len(item) > 5 else 0,
            })
    df = pd.DataFrame(rows)
    if len(df) > 0:
        df = df.sort_values('trade_date').reset_index(drop=True)
        return df
    return None

def fetch_etf_daily(etf_code, start_date, end_date, adjust='qfq'):
    """
    获取ETF日线数据(前复权)
    多源切换: Tushare -> 东方财富 -> 新浪 -> 腾讯
    """
    cache_key = f"{etf_code}_{start_date}_{end_date}_{adjust}"
    if cache_key in _etf_cache:
        return _etf_cache[cache_key]

    sources = [
        ('Tushare',  lambda: _fetch_etf_tushare(etf_code, start_date, end_date)),
        ('东方财富',  lambda: _fetch_etf_em(etf_code, start_date, end_date, adjust)),
        ('腾讯',     lambda: _fetch_etf_tx(etf_code, start_date, end_date, adjust)),
    ]

    for src_name, fetcher in sources:
        try:
            df = fetcher()
            if df is not None and len(df) > 0:
                _etf_cache[cache_key] = df
                return df
        except Exception as e:
            print(f"  {src_name}获取ETF {etf_code} 失败: {e}")

    print(f"  ETF {etf_code} 全部数据源均失败!")
    return pd.DataFrame()

def fetch_all_etf_prices(start_date, end_date):
    """
    获取全部8只ETF的日线收盘价
    返回 DataFrame: index=trade_date, columns=因子名称
    """
    all_prices = {}
    
    for factor_name, info in FACTOR_ETF_MAP.items():
        etf_code = info['etf']
        df = fetch_etf_daily(etf_code, start_date, end_date)
        
        if df is not None and len(df) > 0:
            if '收盘' in df.columns:
                prices = df.set_index('trade_date')['收盘']
            elif 'close' in df.columns:
                prices = df.set_index('trade_date')['close']
            else:
                print(f"ETF {etf_code} 缺少收盘价列, columns={df.columns.tolist()}")
                prices = pd.Series(dtype=float)
            
            all_prices[factor_name] = prices
            print(f"  {factor_name}({etf_code}): {len(prices)}条数据")
        else:
            print(f"  {factor_name}({etf_code}): 无数据!")
            all_prices[factor_name] = pd.Series(dtype=float)
        
        time.sleep(0.5)  # 避免限频
    
    price_df = pd.DataFrame(all_prices)
    price_df = price_df.sort_index()
    return price_df

def fetch_benchmark_prices(start_date, end_date):
    """获取沪深300指数日线数据, 多源切换: Tushare -> 东方财富 -> 腾讯 -> 新浪"""
    # dataHub（优先，同源 index_daily）
    try:
        e = _hub()
        if e is not None:
            hub = e.get_dataset("CN.MARKET.INDEX.DAILY", codes=['000300.SH'],
                                start_date=str(start_date), end_date=str(end_date))
            if hub is not None and len(hub):
                hub = hub.copy()
                hub['trade_date'] = hub['trade_date'].astype(str)
                return hub.sort_values('trade_date').set_index('trade_date')['close']
    except Exception as e:  # noqa: BLE001
        print(f"  dataHub获取沪深300失败: {str(e)[:90]}")

    # Tushare
    try:
        df = ts_call('index_daily', ts_code='000300.SH', start_date=start_date, end_date=end_date)
        if df is not None and len(df) > 0:
            return df.sort_values('trade_date').set_index('trade_date')['close']
    except Exception as e:
        print(f"  Tushare获取沪深300失败: {e}")

    # 东方财富
    try:
        df = ak.stock_zh_index_daily_em(symbol='sh000300')
        if df is not None and len(df) > 0:
            df['trade_date'] = df['date'].astype(str).str.replace('-', '')
            df = df[(df['trade_date'] >= start_date) & (df['trade_date'] <= end_date)]
            df = df.sort_values('trade_date').reset_index(drop=True)
            return df.set_index('trade_date')['close']
    except Exception as e:
        print(f"  东方财富获取沪深300失败: {e}")

    # 腾讯
    try:
        df = ak.stock_zh_index_daily_tx(symbol='sh000300')
        if df is not None and len(df) > 0:
            df['trade_date'] = df['date'].astype(str).str.replace('-', '')
            df = df[(df['trade_date'] >= start_date) & (df['trade_date'] <= end_date)]
            df = df.sort_values('trade_date').reset_index(drop=True)
            return df.set_index('trade_date')['close']
    except Exception as e:
        print(f"  腾讯获取沪深300失败: {e}")

    # 新浪
    try:
        df = ak.stock_zh_index_daily_sina(symbol='sh000300')
        if df is not None and len(df) > 0:
            df['trade_date'] = df['date'].astype(str).str.replace('-', '')
            df = df[(df['trade_date'] >= start_date) & (df['trade_date'] <= end_date)]
            df = df.sort_values('trade_date').reset_index(drop=True)
            return df.set_index('trade_date')['close']
    except Exception as e:
        print(f"  新浪获取沪深300失败: {e}")

    return pd.Series(dtype=float)

# ========== 个股数据获取 ==========

_stock_basic_cache = None

def get_stock_basic():
    """获取全部A股基本信息(含行业分类)"""
    global _stock_basic_cache
    if _stock_basic_cache is not None:
        return _stock_basic_cache
    
    e = _hub()
    if e is not None:
        try:
            hub = e.get_dataset("CN.MARKET.STOCK.BASIC")
            if hub is not None and len(hub):
                hub = hub[~hub['name'].str.contains('ST|退', na=False)]
                _stock_basic_cache = hub
                print(f"获取A股基本信息(dataHub): {len(hub)}只股票")
                return hub
        except Exception as exc:  # noqa: BLE001
            print(f"dataHub股票基础信息失败，回退 tushare: {str(exc)[:80]}")
    for attempt in range(3):
        try:
            df = ts_call('stock_basic', exchange='', list_status='L', fields='ts_code,symbol,name,area,industry,list_date')
            if df is not None and len(df) > 0:
                # 过滤ST股
                df = df[~df['name'].str.contains('ST', na=False)]
                # 过滤退市股
                df = df[~df['name'].str.contains('退', na=False)]
                _stock_basic_cache = df
                print(f"获取A股基本信息: {len(df)}只股票")
                return df
        except Exception as e:
            print(f"获取股票基本信息失败(尝试{attempt+1}): {e}")
            time.sleep(3)
    
    return pd.DataFrame()

def get_stocks_by_industry(industries):
    """
    按申万行业获取股票列表
    Tushare stock_basic的industry字段对应申万行业
    """
    stock_basic = get_stock_basic()
    if stock_basic.empty:
        return []
    
    mask = stock_basic['industry'].isin(industries)
    filtered = stock_basic[mask]
    return filtered['ts_code'].tolist()

def fetch_daily_basic(trade_date):
    """
    获取某日全市场市值数据
    返回 DataFrame: ts_code, total_mv(总市值), circ_mv(流通市值)
    """
    e = _hub()
    if e is not None:
        try:
            hub = e.get_dataset("CN.MARKET.BASIC.DAILY", trade_date=str(trade_date))
            if hub is not None and len(hub):
                return hub
        except Exception as exc:  # noqa: BLE001
            print(f"dataHub每日指标 {trade_date} 失败，回退 tushare: {str(exc)[:80]}")
    for attempt in range(3):
        try:
            df = ts_call('daily_basic', trade_date=trade_date,
                                fields='ts_code,trade_date,turnover_rate,total_mv,circ_mv')
            if df is not None and len(df) > 0:
                return df
            time.sleep(1)
        except Exception as e:
            print(f"获取daily_basic {trade_date} 失败(尝试{attempt+1}): {e}")
            time.sleep(3)
    return pd.DataFrame()

def get_top2000_stocks(trade_date):
    """获取市值排名前2000的非ST股票"""
    basic = get_stock_basic()
    daily_basic = fetch_daily_basic(trade_date)
    
    if daily_basic.empty:
        return set()
    
    # 合并基本信息和市值数据
    merged = daily_basic.merge(basic[['ts_code', 'name', 'industry']], on='ts_code', how='inner')
    # 按总市值排序取前2000
    merged = merged.sort_values('total_mv', ascending=False)
    top2000 = merged.head(2000)
    
    return set(top2000['ts_code'].tolist())

_stock_price_cache = {}

def fetch_stock_daily(ts_code, start_date, end_date):
    """获取个股日线数据"""
    cache_key = f"{ts_code}_{start_date}_{end_date}"
    if cache_key in _stock_price_cache:
        return _stock_price_cache[cache_key]
    
    e = _hub()
    if e is not None:
        try:
            hub = e.get_dataset("CN.MARKET.STOCK.DAILY", codes=[ts_code],
                                start_date=str(start_date), end_date=str(end_date))
            if hub is not None and len(hub):
                hub = hub.sort_values('trade_date').reset_index(drop=True)
                _stock_price_cache[cache_key] = hub
                return hub
        except Exception as exc:  # noqa: BLE001
            print(f"[dataHub] 个股 {ts_code} 失败，回退 tushare: {str(exc)[:80]}")
    # fallback: akshare（前复权；未复权的 tushare daily 已从链路移除，避免口径混用）
    try:
        symbol = ts_code[:6]
        df = ak.stock_zh_a_hist(symbol=symbol, period='daily',
                                start_date=start_date, end_date=end_date, adjust='qfq')
        if df is not None and len(df) > 0:
            df['trade_date'] = df['日期'].astype(str).str.replace('-', '')
            df = df.sort_values('trade_date').reset_index(drop=True)
            _stock_price_cache[cache_key] = df
            return df
    except:
        pass
    
    return pd.DataFrame()

def fetch_stock_batch(ts_codes, start_date, end_date):
    """
    批量获取个股日线数据
    使用tushare daily接口(每次最多支持多只股票)
    """
    pro = get_pro()
    all_data = {}
    
    # 分批获取，每批50只
    batch_size = 50
    for i in range(0, len(ts_codes), batch_size):
        batch = ts_codes[i:i+batch_size]
        for attempt in range(3):
            try:
                # 用逗号连接多个ts_code
                e = _hub()
                df = None
                if e is not None:
                    try:
                        df = e.get_dataset("CN.MARKET.STOCK.DAILY", codes=list(batch),
                                           start_date=str(start_date), end_date=str(end_date))
                    except Exception as exc:  # noqa: BLE001
                        print(f"[dataHub] 批量 {len(batch)} 只失败，回退 tushare: {str(exc)[:70]}")
                        df = None
                if df is None:
                    for code in batch:
                        one = fetch_stock_daily(code, start_date, end_date)
                        if one is not None and len(one):
                            all_data[code] = one
                    break
                if df is not None and len(df) > 0:
                    for code in batch:
                        code_df = df[df['ts_code'] == code]
                        if len(code_df) > 0:
                            code_df = code_df.sort_values('trade_date').reset_index(drop=True)
                            all_data[code] = code_df
                break
            except Exception as e:
                err_msg = str(e).lower()
                if 'limit' in err_msg or 'freq' in err_msg or 'too many' in err_msg:
                    print(f"  限频，等待3秒... (batch {i//batch_size + 1})")
                    time.sleep(3)
                else:
                    print(f"  批量获取失败(尝试{attempt+1}): {e}")
                    time.sleep(1)
        
        if i > 0 and i % 100 == 0:
            print(f"  已获取 {i}/{len(ts_codes)} 只股票数据")
    
    return all_data

def select_top_stocks_for_factor(factor_name, trade_date, lookback=10, top_n=3):
    """
    为指定因子选取近两周涨幅最好的Top N股票
    
    流程:
    1. 获取该因子对应的申万行业股票列表
    2. 筛选市值Top2000
    3. 获取近lookback个交易日的个股价格
    4. 计算涨幅，取Top N
    """
    info = FACTOR_ETF_MAP[factor_name]
    sw_industries = info['sw_industry']
    
    # 获取行业股票列表
    all_stocks = get_stocks_by_industry(sw_industries)
    if not all_stocks:
        print(f"  {factor_name}: 未找到行业 {sw_industries} 的股票")
        return []
    
    # 获取市值Top2000
    top2000 = get_top2000_stocks(trade_date)
    if top2000:
        candidate_stocks = [s for s in all_stocks if s in top2000]
    else:
        candidate_stocks = all_stocks
    
    if not candidate_stocks:
        print(f"  {factor_name}: 无候选股票")
        return []
    
    # 获取lookback+5个交易日的历史数据
    cal = get_trade_calendar()
    idx = cal.index(trade_date) if trade_date in cal else len(cal) - 1
    start_idx = max(0, idx - lookback - 5)
    start_date = cal[start_idx]
    
    # 批量获取价格
    stock_data = fetch_stock_batch(candidate_stocks, start_date, trade_date)
    
    # 计算近lookback个交易日的涨幅
    returns = {}
    for code, df in stock_data.items():
        if len(df) >= lookback + 1:
            recent = df.tail(lookback + 1)
            start_price = recent.iloc[0]['close'] if 'close' in recent.columns else recent.iloc[0]['收盘']
            end_price = recent.iloc[-1]['close'] if 'close' in recent.columns else recent.iloc[-1]['收盘']
            if start_price > 0:
                ret = (end_price - start_price) / start_price
                returns[code] = ret
    
    # 取Top N
    sorted_stocks = sorted(returns.items(), key=lambda x: x[1], reverse=True)[:top_n]
    
    # 获取股票名称
    basic = get_stock_basic()
    result = []
    for code, ret in sorted_stocks:
        name = basic[basic['ts_code'] == code]['name'].values[0] if len(basic[basic['ts_code'] == code]) > 0 else code
        result.append({
            'code': code,
            'name': name,
            'return_2w': ret
        })
    
    return result

def select_all_factor_stocks(trade_date):
    """为全部8个因子选取龙头股"""
    results = {}
    for factor_name in FACTOR_ETF_MAP.keys():
        print(f"  选股: {factor_name}...")
        stocks = select_top_stocks_for_factor(factor_name, trade_date)
        results[factor_name] = stocks
        time.sleep(1)  # 避免限频
    return results

# ========== 工具函数 ==========

def date_to_str(date_obj):
    """日期对象转字符串 YYYYMMDD"""
    return date_obj.strftime('%Y%m%d')

def str_to_date(date_str):
    """字符串转日期对象"""
    return datetime.datetime.strptime(date_str, '%Y%m%d').date()

def format_date_display(date_str):
    """YYYYMMDD -> YYYY-MM-DD"""
    if len(date_str) == 8:
        return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
    return date_str


if __name__ == '__main__':
    # 快速测试
    print("=== 测试数据获取 ===")
    
    # 测试交易日历
    cal = get_trade_calendar()
    print(f"交易日历: {len(cal)}天, 最近5天: {cal[-5:] if len(cal) >= 5 else cal}")
    
    # 测试ETF数据
    print("\n=== 测试ETF数据 ===")
    price_df = fetch_all_etf_prices('20250801', '20260819')
    print(f"ETF价格矩阵: {price_df.shape}")
    print(price_df.tail())
    
    # 测试基准
    print("\n=== 测试沪深300 ===")
    bench = fetch_benchmark_prices('20250801', '20260819')
    print(f"沪深300: {len(bench)}条")
    print(bench.tail())
