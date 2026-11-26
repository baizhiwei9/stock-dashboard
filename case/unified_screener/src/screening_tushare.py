#!/usr/bin/env python3
"""
A股多头排列筛选 - tushare并行版
"""

import tushare as ts
import pandas as pd
import numpy as np
import os
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import warnings
warnings.filterwarnings('ignore')

TODAY = datetime.now().strftime('%Y-%m-%d')
FIRST_DAY = f"{datetime.now().year}-01-05"
OUTPUT_DIR = "outputs/股票筛选"
os.makedirs(OUTPUT_DIR, exist_ok=True)

TOKEN = "a7c1ab22e3162aac86bf77323755b2d99e9cadfad4ae631a50574265"

def get_stock_data(code):
    """获取单只股票日线数据"""
    try:
        df = pro.daily(ts_code=code, start_date='20241001', end_date=TODAY.replace('-', ''))
        if df is not None and not df.empty:
            df['ts_code'] = code
            return df
        return None
    except:
        return None

def main():
    print("=== A股多头排列筛选 (tushare并行版) ===\n")
    print(f"筛选基准日: {TODAY}")
    
    global pro
    ts.set_token(TOKEN)
    pro = ts.pro_api()
    print("✅ tushare 初始化成功")
    
    # 获取A股列表
    print("获取A股列表...")
    df_sh = pro.stock_basic(exchange='SSE', list_status='L', fields='ts_code,symbol,name,list_date')
    df_sh['exchange'] = 'SH'
    df_sz = pro.stock_basic(exchange='SZSE', list_status='L', fields='ts_code,symbol,name,list_date')
    df_sz['exchange'] = 'SZ'
    stocks_df = pd.concat([df_sh, df_sz], ignore_index=True)
    stocks_df = stocks_df[~stocks_df['name'].str.contains('ST|退', na=False)]
    print(f"获取到 {len(stocks_df)} 只A股（非ST）")
    
    # 并行获取日线数据
    print("\n并行获取日线数据（使用20线程）...")
    all_data = []
    codes = stocks_df['ts_code'].unique().tolist()
    
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(get_stock_data, code): code for code in codes}
        
        completed = 0
        for future in as_completed(futures):
            completed += 1
            if completed % 1000 == 0:
                print(f"进度: {completed}/{len(codes)}")
            
            result = future.result()
            if result is not None:
                all_data.append(result)
    
    print(f"进度: {len(codes)}/{len(codes)}")
    
    if not all_data:
        print("❌ 无日线数据")
        exit(1)
    
    daily_df = pd.concat(all_data, ignore_index=True)
    daily_df['trade_date'] = pd.to_datetime(daily_df['trade_date'].astype(str), format='%Y%m%d')
    
    # 检查数据完整性
    stock_counts = daily_df.groupby('ts_code').size()
    print(f"获取到 {len(daily_df)} 条数据")
    print(f"平均每只股票: {stock_counts.mean():.0f} 条, 最少: {stock_counts.min()}, 最多: {stock_counts.max()}")
    
    # 计算均线并筛选
    print("\n计算均线并筛选...")
    results = []
    qualified = 0
    
    for idx, ts_code in enumerate(stocks_df['ts_code'].unique()):
        stock_daily = daily_df[daily_df['ts_code'] == ts_code].copy()
        stock_daily = stock_daily.sort_values('trade_date')
        
        if len(stock_daily) < 160:
            continue
        
        stock_daily['ma20'] = stock_daily['close'].rolling(window=20, min_periods=20).mean()
        stock_daily['ma40'] = stock_daily['close'].rolling(window=40, min_periods=40).mean()
        stock_daily['ma80'] = stock_daily['close'].rolling(window=80, min_periods=80).mean()
        stock_daily['ma160'] = stock_daily['close'].rolling(window=160, min_periods=160).mean()
        
        latest = stock_daily.iloc[-1]
        
        if (pd.notna(latest['ma20']) and pd.notna(latest['ma40']) and 
            pd.notna(latest['ma80']) and pd.notna(latest['ma160'])):
            if (latest['close'] > latest['ma20'] > latest['ma40'] > latest['ma80'] > latest['ma160']):
                qualified += 1
                
                ytd_df = stock_daily[stock_daily['trade_date'] >= pd.to_datetime(FIRST_DAY)]
                if len(ytd_df) >= 2:
                    ytd_return = (latest['close'] / ytd_df.iloc[0]['close'] - 1) * 100
                else:
                    ytd_return = None
                
                info = stocks_df[stocks_df['ts_code'] == ts_code].iloc[0]
                
                results.append({
                    '股票代码': ts_code.replace('.SH', '').replace('.SZ', ''),
                    '交易所': 'SH' if ts_code.endswith('.SH') else 'SZ',
                    '股票简称': info['name'],
                    '当前价': round(latest['close'], 2),
                    'MA20': round(latest['ma20'], 2),
                    'MA40': round(latest['ma40'], 2),
                    'MA80': round(latest['ma80'], 2),
                    'MA160': round(latest['ma160'], 2),
                    '年内涨幅': round(ytd_return, 2) if ytd_return is not None else None
                })
        
        if idx % 1000 == 0 and idx > 0:
            print(f"筛选进度: {idx}/{len(stocks_df['ts_code'].unique())} (已发现{qualified}只)")
    
    print(f"\n✅ 筛选完成！共 {len(results)} 只股票满足条件")
    
    if results:
        results_df = pd.DataFrame(results)
        results_df = results_df.sort_values('年内涨幅', ascending=False)
        top20 = results_df.head(20)
        
        print("\n📊 年内涨幅Top20：")
        for _, row in top20.iterrows():
            code = f"{row['交易所']}.{row['股票代码']}"
            ytd = f"{row['年内涨幅']:.2f}%" if pd.notna(row['年内涨幅']) else "N/A"
            print(f"  {code} {row['股票简称']} 价:{row['当前价']} 涨:{ytd}")
        
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
        
        md_content += f"\n*数据来源: tushare*\n"
        md_content += "*仅供理论学习和理论练习使用，不构成投资建议*\n"
        
        output_file = os.path.join(OUTPUT_DIR, f"{TODAY}_多头排列Top20.md")
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(md_content)
        
        print(f"\n📁 已保存: {output_file}")
        print("✅ 任务完成！")
    else:
        print("⚠️ 无满足条件的股票")

if __name__ == '__main__':
    main()
