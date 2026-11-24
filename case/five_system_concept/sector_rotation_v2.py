#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
行业ETF板块轮动信号 -- 腾讯API直连版
20日动量排名 + MA40趋势过滤
"""
import requests
import pandas as pd
import numpy as np
import json
import sys
import time

# 18只行业ETF (RMT系统) + 补充
INDUSTRY_ETFS = {
    'sz159928': '消费',
    'sh512010': '医药',
    'sh512800': '银行',
    'sh512880': '证券',
    'sh512200': '房地产',
    'sh512660': '军工',
    'sh515030': '新能源车',
    'sh515790': '光伏',
    'sh512480': '半导体',
    'sh515880': '通信',
    'sh512720': '计算机',
    'sh515170': '食品饮料',
    'sh512400': '有色金属',
    'sh512980': '传媒',
    'sh515220': '煤炭',
    'sh516950': '基建',
    'sh512580': '环保',
    'sh512690': '白酒',
    'sh515050': '5G',
    'sh512760': '电子50',
    'sh515070': '人工智能',
    'sh562500': '机器人',
    'sh588000': '科创50',
    'sh515080': '中药',
    'sz159825': '养殖',
}

SESSION = requests.Session()
SESSION.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
})

def fetch_tencent(code, count=120):
    """腾讯API拉取ETF日线(前复权)"""
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    params = {
        'param': f"{code},day,,,{count},qfq",
    }
    try:
        resp = SESSION.get(url, params=params, timeout=15)
        data = resp.json()
        kline = data.get('data', {}).get(code, {})
        # 腾讯返回 qfq 数据在 'qfqday' 或 'day' 字段
        bars = kline.get('qfqday') or kline.get('day')
        if not bars:
            return None
        # 字段: [日期, 开, 收, 高, 低, 成交量]
        df = pd.DataFrame(bars, columns=['date', 'open', 'close', 'high', 'low', 'volume'])
        df['close'] = pd.to_numeric(df['close'], errors='coerce')
        df = df.dropna(subset=['close'])
        return df
    except Exception as e:
        # 尝试备用URL
        try:
            url2 = f"https://web.ifzq.gtimg.cn/appstock/app/kline/kline"
            params2 = {'param': f"{code},day,,,{count},qfq"}
            resp2 = SESSION.get(url2, params=params2, timeout=15)
            data2 = resp2.json()
            bars2 = data2.get('data', {}).get(code, {}).get('qfqday') or data2.get('data', {}).get(code, {}).get('day')
            if not bars2:
                return None
            df = pd.DataFrame(bars2, columns=['date', 'open', 'close', 'high', 'low', 'volume'])
            df['close'] = pd.to_numeric(df['close'], errors='coerce')
            df = df.dropna(subset=['close'])
            return df
        except:
            print(f"  [ERR] {code} 腾讯API失败: {e}", file=sys.stderr)
            return None

def fetch_eastmoney(code_raw, count=120):
    """东财API直接请求(备用)"""
    # code_raw like '159928' or '512010'
    market = 1 if code_raw.startswith(('5', '11')) else 0
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        'secid': f"{market}.{code_raw}",
        'fields1': 'f1,f2,f3,f4,f5,f6',
        'fields2': 'f51,f52,f53,f54,f55,f56,f57',
        'klt': '101',  # daily
        'fqt': '1',    # qfq
        'lmt': str(count),
        'end': '20500101',
    }
    try:
        resp = SESSION.get(url, params=params, timeout=15)
        data = resp.json()
        klines = data.get('data', {}).get('klines', [])
        if not klines:
            return None
        rows = [k.split(',') for k in klines]
        df = pd.DataFrame(rows, columns=['date', 'open', 'close', 'high', 'low', 'volume', 'amount'])
        df['close'] = pd.to_numeric(df['close'], errors='coerce')
        df = df.dropna(subset=['close'])
        return df
    except Exception as e:
        print(f"  [ERR] {code_raw} 东财API失败: {e}", file=sys.stderr)
        return None

def fetch_sina(code_raw, count=120):
    """新浪API拉取日K"""
    # 5开头=sh, 0/3开头=sz, 1开头=sz
    if code_raw.startswith('5'):
        prefix = 'sh'
    else:
        prefix = 'sz'
    url = f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
    params = {
        'symbol': f"{prefix}{code_raw}",
        'scale': '240',  # daily
        'datalen': str(count),
    }
    try:
        resp = SESSION.get(url, params=params, timeout=15)
        data = json.loads(resp.text)
        if not data:
            return None
        df = pd.DataFrame(data)
        df['close'] = pd.to_numeric(df['close'], errors='coerce')
        df = df.dropna(subset=['close'])
        return df
    except Exception as e:
        print(f"  [ERR] {code_raw} 新浪API失败: {e}", file=sys.stderr)
        return None

def fetch_etf(code_tencent, count=120):
    """尝试多个数据源"""
    code_raw = code_tencent[2:]  # remove sh/sz prefix
    
    # 1. 腾讯
    df = fetch_tencent(code_tencent, count)
    if df is not None and len(df) >= 41:
        return df
    
    # 2. 东财
    time.sleep(0.3)
    df = fetch_eastmoney(code_raw, count)
    if df is not None and len(df) >= 41:
        return df
    
    # 3. 新浪
    time.sleep(0.3)
    df = fetch_sina(code_raw, count)
    if df is not None and len(df) >= 41:
        return df
    
    return None

def compute_signals(df):
    """计算动量和趋势信号"""
    close = df['close'].values
    n = len(close)
    if n < 41:
        return None
    
    latest = close[-1]
    
    mom20 = (latest / close[-21] - 1) * 100 if n >= 21 else np.nan
    mom5 = (latest / close[-6] - 1) * 100 if n >= 6 else np.nan
    mom10 = (latest / close[-11] - 1) * 100 if n >= 11 else np.nan
    mom60 = (latest / close[-61] - 1) * 100 if n >= 61 else np.nan
    
    ma40 = np.mean(close[-40:]) if n >= 40 else np.nan
    above_ma40 = bool(latest > ma40) if not np.isnan(ma40) else False
    ma40_gap = (latest / ma40 - 1) * 100 if not np.isnan(ma40) else np.nan
    
    ma20 = np.mean(close[-20:]) if n >= 20 else np.nan
    above_ma20 = bool(latest > ma20) if not np.isnan(ma20) else False
    
    rets = np.diff(np.log(close[-21:])) if n >= 21 else np.array([])
    vol20 = np.std(rets) * np.sqrt(252) * 100 if len(rets) > 0 else np.nan
    
    return {
        'latest': round(latest, 3),
        'mom5': round(mom5, 2),
        'mom10': round(mom10, 2),
        'mom20': round(mom20, 2),
        'mom60': round(mom60, 2),
        'above_ma40': above_ma40,
        'above_ma20': above_ma20,
        'ma40_gap': round(ma40_gap, 2) if not np.isnan(ma40_gap) else None,
        'vol20': round(vol20, 2) if not np.isnan(vol20) else None,
        'last_date': df.iloc[-1]['date'] if 'date' in df.columns else '',
    }

def main():
    print("=" * 95)
    print("行业ETF板块轮动信号 -- RMT(20日动量Top5) + Hodge(MA40趋势过滤)")
    print("=" * 95)
    
    results = []
    for code, name in INDUSTRY_ETFS.items():
        print(f"  拉取 {code} {name}...", file=sys.stderr)
        df = fetch_etf(code, 120)
        if df is None:
            print(f"  [SKIP] {code} {name} 无数据", file=sys.stderr)
            continue
        sig = compute_signals(df)
        if sig is None:
            print(f"  [SKIP] {code} {name} 数据不足", file=sys.stderr)
            continue
        sig['code'] = code[2:]  # raw code
        sig['name'] = name
        results.append(sig)
        time.sleep(0.2)
    
    if not results:
        print("ERROR: 所有数据源均失败!", file=sys.stderr)
        sys.exit(1)
    
    df_res = pd.DataFrame(results)
    df_sorted = df_res.sort_values('mom20', ascending=False).reset_index(drop=True)
    
    # 日期
    dates = df_res['last_date'].dropna().unique()
    latest_date = max(dates) if len(dates) > 0 else 'N/A'
    
    print(f"\n数据截止日: {latest_date}")
    print(f"成功获取: {len(results)}/{len(INDUSTRY_ETFS)} 只ETF\n")
    
    # --- RMT规则: 20日动量Top5 ---
    print("-" * 95)
    print(">>> RMT系统选板块: 20日动量Top5 <<<")
    print("-" * 95)
    print(f"{'排名':<5} {'代码':<8} {'板块':<10} {'20日涨幅%':<12} {'10日涨幅%':<12} {'5日涨幅%':<12} {'MA40线上':<8}")
    print("-" * 95)
    for i, row in df_sorted.head(5).iterrows():
        ma40_str = "是" if row['above_ma40'] else "否"
        print(f"{i+1:<5} {row['code']:<8} {row['name']:<10} {row['mom20']:<12.2f} {row['mom10']:<12.2f} {row['mom5']:<12.2f} {ma40_str:<8}")
    
    # --- Hodge规则: MA40线上 + 动量排序 ---
    df_above = df_res[df_res['above_ma40'] == True].sort_values('mom20', ascending=False).reset_index(drop=True)
    
    print(f"\n{'-'*95}")
    print(f">>> Hodge系统: MA40趋势过滤(线上) + 动量排名 (共{len(df_above)}只在线上) <<<")
    print("-" * 95)
    print(f"{'排名':<5} {'代码':<8} {'板块':<10} {'20日涨幅%':<12} {'MA40偏离%':<12} {'60日涨幅%':<12} {'波动率%':<10}")
    print("-" * 95)
    for i, row in df_above.iterrows():
        ma40_gap = row['ma40_gap'] if row['ma40_gap'] is not None else 0
        print(f"{i+1:<5} {row['code']:<8} {row['name']:<10} {row['mom20']:<12.2f} {ma40_gap:<12.2f} {row['mom60']:<12.2f} {row['vol20']:<10.2f}")
    
    # --- 全部完整排名 ---
    print(f"\n{'-'*95}")
    print(">>> 全部行业ETF完整排名 (按20日动量降序) <<<")
    print("-" * 95)
    print(f"{'排名':<5} {'代码':<8} {'板块':<10} {'5日%':<9} {'10日%':<9} {'20日%':<9} {'60日%':<9} {'MA40':<6} {'MA20':<6} {'偏离%':<8} {'波动%':<7}")
    print("-" * 95)
    for i, row in df_sorted.iterrows():
        ma40_str = "Y" if row['above_ma40'] else "N"
        ma20_str = "Y" if row['above_ma20'] else "N"
        ma40_gap = row['ma40_gap'] if row['ma40_gap'] is not None else 0
        print(f"{i+1:<5} {row['code']:<8} {row['name']:<10} {row['mom5']:<9.2f} {row['mom10']:<9.2f} {row['mom20']:<9.2f} {row['mom60']:<9.2f} {ma40_str:<6} {ma20_str:<6} {ma40_gap:<8.2f} {row['vol20']:<7.2f}")
    
    print(f"\n{'='*95}")
    print("说明:")
    print("  RMT系统: 平时满仓20日动量Top5, RMT极端状态(delta_s阈值)触发减仓干预")
    print("  Hodge系统: MA40趋势过滤(线上做多) + Hodge谱角距离变点检测(减仓/空仓)")
    print("  动量 = (今日收盘 / N日前收盘 - 1) * 100")
    print(f"{'='*95}")

if __name__ == '__main__':
    main()
