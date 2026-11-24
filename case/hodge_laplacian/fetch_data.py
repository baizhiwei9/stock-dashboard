#!/usr/bin/env python3
"""Fetch ETF daily close data (front-adjusted) from Tencent API.
Segments of max 800 bars are merged and deduplicated by date.
"""
import os, json, time, requests
import pandas as pd
import numpy as np

# 18 ETFs from the design doc
ETFS = {
    '512480': 'sh', '512760': 'sh', '515880': 'sh', '515050': 'sh',
    '510300': 'sh', '510500': 'sh', '512100': 'sh', '512890': 'sh',
    '512200': 'sh', '512010': 'sh', '512690': 'sh', '512800': 'sh',
    '512660': 'sh', '510050': 'sh', '510880': 'sh', '512720': 'sh',
    '159915': 'sz', '159928': 'sz',
}

ETF_NAMES = {
    '512480': '半导体ETF', '512760': '芯片ETF', '515880': '通信ETF',
    '515050': '5G通信ETF', '510300': '沪深300ETF', '510500': '中证500ETF',
    '512100': '中证1000ETF', '512890': '红利低波ETF', '512200': '房地产ETF',
    '512010': '医药ETF', '512690': '酒ETF', '512800': '银行ETF',
    '512660': '军工ETF', '159915': '创业板ETF', '510050': '上证50ETF',
    '510880': '红利ETF', '512720': '计算机ETF', '159928': '消费ETF',
}

# Segments for long history (max 800 bars per request)
SEGMENTS = [
    ('2016-12-01', '2019-12-01'),
    ('2019-11-01', '2022-12-01'),
    ('2022-11-01', '2024-12-01'),
    ('2024-11-01', '2026-08-14'),
]

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data_long')
os.makedirs(DATA_DIR, exist_ok=True)

URL = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get'

def fetch_segment(code, prefix, start, end):
    """Fetch one segment of kline data."""
    full_code = f'{prefix}{code}'
    params = {'param': f'{full_code},day,{start},{end},800,qfq'}
    try:
        r = requests.get(URL, params=params, timeout=20)
        data = r.json()
    except Exception as e:
        print(f'  ERROR fetching {full_code} {start}-{end}: {e}')
        return []
    
    if 'data' not in data or full_code not in data['data']:
        return []
    
    kline = data['data'][full_code]
    if isinstance(kline, dict):
        # Try qfqday first, then day
        kline = kline.get('qfqday', kline.get('day', []))
    if not isinstance(kline, list):
        return []
    
    return kline  # [[date, open, close, high, low, volume], ...]

def fetch_etf(code, prefix):
    """Fetch all segments for one ETF and merge."""
    all_bars = {}
    for start, end in SEGMENTS:
        bars = fetch_segment(code, prefix, start, end)
        for bar in bars:
            if len(bar) >= 3:
                date = bar[0]
                close = float(bar[2])  # close price
                all_bars[date] = close
        time.sleep(0.15)  # rate limit
    
    if not all_bars:
        print(f'  WARNING: no data for {code}')
        return None
    
    dates = sorted(all_bars.keys())
    closes = [all_bars[d] for d in dates]
    df = pd.DataFrame({'date': dates, 'close': closes})
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)
    return df

def fetch_index_hs300():
    """Fetch CSI 300 index for benchmark."""
    all_bars = {}
    for start, end in SEGMENTS:
        params = {'param': f'sh000300,day,{start},{end},800,qfq'}
        try:
            r = requests.get(URL, params=params, timeout=20)
            data = r.json()
            kline = data.get('data', {}).get('sh000300', {})
            if isinstance(kline, dict):
                kline = kline.get('day', kline.get('qfqday', []))
            for bar in kline:
                if len(bar) >= 3:
                    all_bars[bar[0]] = float(bar[2])
        except:
            pass
        time.sleep(0.15)
    
    dates = sorted(all_bars.keys())
    closes = [all_bars[d] for d in dates]
    df = pd.DataFrame({'date': dates, 'close': closes})
    df['date'] = pd.to_datetime(df['date'])
    return df.sort_values('date').reset_index(drop=True)

def main():
    print('=== Fetching ETF data ===')
    all_data = {}
    for code, prefix in ETFS.items():
        name = ETF_NAMES.get(code, code)
        csv_path = os.path.join(DATA_DIR, f'{code}.csv')
        if os.path.exists(csv_path):
            df = pd.read_csv(csv_path, parse_dates=['date'])
            print(f'  {code} {name}: cached {len(df)} bars')
        else:
            print(f'  Fetching {code} {name}...')
            df = fetch_etf(code, prefix)
            if df is not None:
                df.to_csv(csv_path, index=False)
                print(f'    -> {len(df)} bars ({df["date"].min().date()} to {df["date"].max().date()})')
            else:
                print(f'    -> FAILED')
                continue
        all_data[code] = df
    
    # Fetch HS300 benchmark
    hs300_path = os.path.join(DATA_DIR, 'hs300.csv')
    if os.path.exists(hs300_path):
        hs300 = pd.read_csv(hs300_path, parse_dates=['date'])
        print(f'  HS300: cached {len(hs300)} bars')
    else:
        print('  Fetching HS300 index...')
        hs300 = fetch_index_hs300()
        hs300.to_csv(hs300_path, index=False)
        print(f'    -> {len(hs300)} bars')
    
    # Build close price matrix
    all_dates = set()
    for df in all_data.values():
        all_dates.update(df['date'])
    all_dates = sorted(all_dates)
    
    close_matrix = pd.DataFrame({'date': all_dates})
    close_matrix['date'] = pd.to_datetime(close_matrix['date'])
    for code, df in all_data.items():
        merged = close_matrix.merge(df[['date', 'close']], on='date', how='left')
        close_matrix[code] = merged['close']
    
    close_matrix = close_matrix.sort_values('date').reset_index(drop=True)
    
    # Save combined
    combined_path = os.path.join(DATA_DIR, 'close_matrix.csv')
    close_matrix.to_csv(combined_path, index=False)
    
    hs300.to_csv(hs300_path, index=False)
    
    print(f'\n=== Summary ===')
    print(f'Total trading days: {len(close_matrix)}')
    print(f'Date range: {close_matrix["date"].min().date()} to {close_matrix["date"].max().date()}')
    print(f'ETFs with data: {len(all_data)}')
    for code in ETFS:
        if code in close_matrix.columns:
            first_valid = close_matrix[code].first_valid_index()
            if first_valid is not None:
                print(f'  {code} {ETF_NAMES.get(code,"")}: from {close_matrix.loc[first_valid, "date"].date()}, {close_matrix[code].notna().sum()} bars')
    
    print(f'\nData saved to {DATA_DIR}/')

if __name__ == '__main__':
    main()
