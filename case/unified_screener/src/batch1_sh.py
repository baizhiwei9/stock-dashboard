#!/usr/bin/env python3
"""
分批处理 - 第一批（沪市A股）
"""

import baostock as bs
import pandas as pd
import numpy as np
import os
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

TODAY = '2026-04-10'
FIRST_DAY = '2026-01-05'
OUTPUT_DIR = "outputs/股票筛选"
os.makedirs(OUTPUT_DIR, exist_ok=True)

def get_stock_data(ts_code, stock_name, ipo_date):
    try:
        lg = bs.login()
        rs = bs.query_history_k_data_plus(ts_code, "date,close", start_date='', end_date=TODAY, frequency="d", adjustflag="3")
        data_list = []
        while (rs.error_code == '0') & rs.next():
            data_list.append(rs.get_row_data())
        bs.logout()
        
        if not data_list:
            return None
        
        df = pd.DataFrame(data_list, columns=['date', 'close'])
        df['date'] = pd.to_datetime(df['date'])
        df['close'] = pd.to_numeric(df['close'], errors='coerce')
        df = df.sort_values('date').dropna(subset=['close'])
        
        if len(df) > 160:
            df = df.tail(160)
        
        if len(df) < 160:
            return None
        
        df['ma20'] = df['close'].rolling(window=20, min_periods=20).mean()
        df['ma40'] = df['close'].rolling(window=40, min_periods=40).mean()
        df['ma80'] = df['close'].rolling(window=80, min_periods=80).mean()
        df['ma160'] = df['close'].rolling(window=160, min_periods=160).mean()
        
        latest = df.iloc[-1]
        
        if (pd.notna(latest['ma20']) and pd.notna(latest['ma40']) and 
            pd.notna(latest['ma80']) and pd.notna(latest['ma160'])):
            if (latest['close'] > latest['ma20'] > latest['ma40'] > latest['ma80'] > latest['ma160']):
                effective_start = max(FIRST_DAY, ipo_date)
                ytd_df = df[df['date'] >= pd.to_datetime(effective_start)]
                if len(ytd_df) >= 2:
                    ytd_return = (latest['close'] / ytd_df.iloc[0]['close'] - 1) * 100
                else:
                    ytd_return = None
                
                return {
                    '股票代码': ts_code.replace('sh.', '').replace('sz.', ''),
                    '交易所': 'SH' if ts_code.startswith('sh.') else 'SZ',
                    '股票简称': stock_name,
                    '当前价': round(latest['close'], 2),
                    'MA20': round(latest['ma20'], 2),
                    'MA40': round(latest['ma40'], 2),
                    'MA80': round(latest['ma80'], 2),
                    'MA160': round(latest['ma160'], 2),
                    '年内涨幅': round(ytd_return, 2) if ytd_return is not None else None
                }
        return None
    except:
        return None

def main():
    print("=== 批量筛选 Batch 1: 沪市A股 ===\n")
    
    lg = bs.login()
    rs = bs.query_stock_basic(code_name="")
    data_list = []
    while (rs.error_code == '0') & rs.next():
        data_list.append(rs.get_row_data())
    bs.logout()
    
    stocks_df = pd.DataFrame(data_list, columns=rs.fields)
    # 沪市A股
    stocks_df = stocks_df[(stocks_df['type'] == '1') & stocks_df['code'].str.startswith('sh.')]
    print(f"沪市A股: {len(stocks_df)} 只")
    
    results = []
    for idx, row in stocks_df.iterrows():
        if idx % 200 == 0:
            print(f"进度: {idx}/{len(stocks_df)}")
        
        result = get_stock_data(row['code'], row['code_name'], row['ipoDate'])
        if result:
            results.append(result)
    
    print(f"\n沪市筛选完成: {len(results)} 只满足条件")
    
    # 保存中间结果
    if results:
        df = pd.DataFrame(results)
        df.to_csv(os.path.join(OUTPUT_DIR, 'batch1_sh.csv'), index=False)
        print("已保存 batch1_sh.csv")
    
    return results

if __name__ == '__main__':
    main()
