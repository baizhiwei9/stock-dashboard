#!/usr/bin/env python3
"""
全市场A股多头排列筛选 - 精简版
只获取160个交易日数据，加速处理
"""

import baostock as bs
import pandas as pd
import numpy as np
import os
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import warnings
warnings.filterwarnings('ignore')

TODAY = '2026-04-10'
FIRST_DAY = '2026-01-05'
OUTPUT_DIR = "outputs/股票筛选"
os.makedirs(OUTPUT_DIR, exist_ok=True)

def get_stock_data(ts_code, stock_name, ipo_date):
    """获取单只股票数据"""
    try:
        lg = bs.login()
        if lg.error_code != '0':
            bs.logout()
            return None
        
        # 只获取160个交易日
        rs = bs.query_history_k_data_plus(
            ts_code,
            "date,close",
            start_date='',
            end_date=TODAY,
            frequency="d",
            adjustflag="3"
        )
        
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
        
        # 只保留最近160个交易日
        if len(df) > 160:
            df = df.tail(160)
        
        if len(df) < 160:
            return None
        
        # 计算均线
        df['ma20'] = df['close'].rolling(window=20, min_periods=20).mean()
        df['ma40'] = df['close'].rolling(window=40, min_periods=40).mean()
        df['ma80'] = df['close'].rolling(window=80, min_periods=80).mean()
        df['ma160'] = df['close'].rolling(window=160, min_periods=160).mean()
        
        latest = df.iloc[-1]
        
        # 筛选条件
        if (pd.notna(latest['ma20']) and pd.notna(latest['ma40']) and 
            pd.notna(latest['ma80']) and pd.notna(latest['ma160'])):
            if (latest['close'] > latest['ma20'] > latest['ma40'] > latest['ma80'] > latest['ma160']):
                # 年内涨幅
                effective_start = max(FIRST_DAY, ipo_date)
                ytd_df = df[df['date'] >= pd.to_datetime(effective_start)]
                if len(ytd_df) >= 2:
                    first_price = ytd_df.iloc[0]['close']
                    ytd_return = (latest['close'] / first_price - 1) * 100
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
        
    except Exception as e:
        return None

def main():
    print("=== 全市场A股多头排列筛选 ===\n")
    print(f"筛选基准日: {TODAY}")
    
    # 获取股票列表
    lg = bs.login()
    rs = bs.query_stock_basic(code_name="")
    data_list = []
    while (rs.error_code == '0') & rs.next():
        data_list.append(rs.get_row_data())
    bs.logout()
    
    stocks_df = pd.DataFrame(data_list, columns=rs.fields)
    stocks_df = stocks_df[(stocks_df['type'] == '1') & stocks_df['code'].str.startswith(('sh.', 'sz.'))]
    print(f"获取到 {len(stocks_df)} 只股票")
    
    # 使用线程池并行处理
    results = []
    total = len(stocks_df)
    
    print("开始筛选...")
    
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {
            executor.submit(get_stock_data, row['code'], row['code_name'], row['ipoDate']): i
            for i, row in stocks_df.iterrows()
        }
        
        completed = 0
        for future in as_completed(futures):
            completed += 1
            if completed % 500 == 0:
                print(f"进度: {completed}/{total}")
            
            result = future.result()
            if result:
                results.append(result)
    
    print(f"\n✅ 筛选完成！共 {len(results)} 只股票满足条件")
    
    if results:
        results_df = pd.DataFrame(results)
        results_df = results_df.sort_values('年内涨幅', ascending=False)
        top20 = results_df.head(20)
        
        print("\n📊 Top20：")
        for _, row in top20.iterrows():
            code = f"{row['交易所']}.{row['股票代码']}"
            ytd = f"{row['年内涨幅']:.2f}%" if pd.notna(row['年内涨幅']) else "N/A"
            print(f"  {code} {row['股票简称']} 年内涨幅: {ytd}")
        
        # 保存Markdown
        md_content = f"# {TODAY} 全市场A股多头排列筛选Top20\n\n"
        md_content += f"**筛选基准日**: {TODAY}\n\n"
        md_content += "**筛选条件**: 股价 > MA20 > MA40 > MA80 > MA160\n\n"
        md_content += "**排序方式**: 按年内涨幅降序排列\n\n"
        md_content += f"**筛选总数**: {len(results_df)} 只股票\n\n"
        md_content += "| 股票代码 | 股票简称 | 当前价 | MA20 | MA40 | MA80 | MA160 | 年内涨幅(%) |\n"
        md_content += "|----------|----------|--------|------|------|-------|-------|-------------|\n"
        
        for _, row in top20.iterrows():
            code_full = f"{row['交易所']}.{row['股票代码']}"
            ytd_display = f"{row['年内涨幅']:.2f}" if pd.notna(row['年内涨幅']) else "N/A"
            md_content += f"| {code_full} | {row['股票简称']} | {row['当前价']} | {row['MA20']} | {row['MA40']} | {row['MA80']} | {row['MA160']} | {ytd_display} |\n"
        
        md_content += f"\n*数据来源: baostock*\n"
        md_content += "*仅供理论学习和理论练习使用，不构成投资建议*\n"
        
        output_file = os.path.join(OUTPUT_DIR, f"{TODAY}_多头排列Top20.md")
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(md_content)
        
        print(f"\n📁 已保存: {output_file}")
    else:
        print("⚠️ 无满足条件的股票")

if __name__ == '__main__':
    main()
