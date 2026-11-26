#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股多头排列筛选器 - baostock多线程版
案例分析学习材料，不构成投资建议
"""

import baostock as bs
import pandas as pd
import numpy as np
import os
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# 全局锁
lock = threading.Lock()
all_data = []
codes_to_fetch = []

def get_kline_data_thread(code, start_date, end_date):
    """获取单只股票日线数据"""
    try:
        rs = bs.query_history_k_data_plus(
            code,
            "date,code,open,high,low,close,volume,amount",
            start_date=start_date, end_date=end_date,
            frequency="d", adjustflag="2"  # 前复权
        )
        
        data_list = []
        while (rs.error_code == '0') & rs.next():
            data_list.append(rs.get_row_data())
        
        if data_list:
            df = pd.DataFrame(data_list, columns=['date', 'code', 'open', 'high', 'low', 'close', 'volume', 'amount'])
            for col in ['open', 'high', 'low', 'close', 'volume', 'amount']:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            return df
    except Exception as e:
        pass
    return None

def worker(code):
    global all_data
    df = get_kline_data_thread(code, START_DATE, TODAY_STR)
    if df is not None and len(df) >= 160:
        with lock:
            all_data.append(df)
    return code

def main():
    global START_DATE, TODAY_STR, all_data, codes_to_fetch
    
    print("=" * 60)
    print("A股多头排列筛选器 (baostock多线程版)")
    print("=" * 60)
    
    TODAY_STR = datetime.now().strftime('%Y-%m-%d')
    START_DATE = (datetime.now() - timedelta(days=400)).strftime('%Y-%m-%d')
    FIRST_DAY = f"{datetime.now().year}-01-05"
    
    print(f"筛选基准日: {TODAY_STR}")
    print(f"数据起始日: {START_DATE}")
    
    # 登录
    lg = bs.login()
    if lg.error_code != '0':
        print(f"登录失败")
        return
    print("登录成功")
    
    # 获取股票列表
    print("\n获取股票列表...")
    rs = bs.query_stock_basic(code_name='')
    stock_list = []
    while (rs.error_code == '0') & rs.next():
        stock_list.append(rs.get_row_data())
    stock_df = pd.DataFrame(stock_list, columns=rs.fields)
    
    # 过滤沪深主板
    stock_df = stock_df[
        ((stock_df['code'].str.startswith('sh.6')) | 
         (stock_df['code'].str.startswith('sz.0')) |
         (stock_df['code'].str.startswith('sz.3'))) &
        (stock_df['type'] == '1')
    ]
    stock_df = stock_df[~stock_df['code_name'].str.contains('ST|退', na=False)]
    
    codes_to_fetch = stock_df['code'].tolist()
    name_map = dict(zip(stock_df['code'], stock_df['code_name']))
    print(f"待处理: {len(codes_to_fetch)} 只")
    
    # 多线程获取
    print("\n并行获取日线数据(20线程)...")
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(worker, code): code for code in codes_to_fetch}
        
        completed = 0
        for future in as_completed(futures):
            completed += 1
            if completed % 200 == 0:
                print(f"进度: {completed}/{len(codes_to_fetch)}, 已获取有效数据: {len(all_data)}")
    
    bs.logout()
    print(f"获取完成, 共 {len(all_data)} 只股票有足够数据")
    
    if not all_data:
        print("无有效数据")
        return
    
    daily_df = pd.concat(all_data, ignore_index=True)
    
    # 筛选
    print("\n筛选多头排列股票...")
    results = []
    
    for code in codes_to_fetch:
        stock_daily = daily_df[daily_df['code'] == code].copy()
        if len(stock_daily) < 160:
            continue
        
        stock_daily = stock_daily.sort_values('date').dropna(subset=['close'])
        if len(stock_daily) < 160:
            continue
        
        stock_daily['ma20'] = stock_daily['close'].rolling(20).mean()
        stock_daily['ma40'] = stock_daily['close'].rolling(40).mean()
        stock_daily['ma80'] = stock_daily['close'].rolling(80).mean()
        stock_daily['ma160'] = stock_daily['close'].rolling(160).mean()
        
        latest = stock_daily.iloc[-1]
        
        if (pd.notna(latest['ma160']) and 
            latest['close'] > latest['ma20'] > latest['ma40'] > 
            latest['ma80'] > latest['ma160']):
            
            ytd_df = stock_daily[stock_daily['date'] >= FIRST_DAY.replace('-', '')]
            ytd_return = (latest['close'] / ytd_df.iloc[0]['close'] - 1) * 100 if len(ytd_df) >= 2 else None
            
            exchange = 'SH' if code.startswith('sh.') else 'SZ'
            stock_code = code.replace('sh.', '').replace('sz.', '')
            
            results.append({
                '股票代码': stock_code,
                '交易所': exchange,
                '股票简称': name_map.get(code, stock_code),
                '当前价': round(latest['close'], 2),
                'MA20': round(latest['ma20'], 2),
                'MA40': round(latest['ma40'], 2),
                'MA80': round(latest['ma80'], 2),
                'MA160': round(latest['ma160'], 2),
                '年内涨幅': round(ytd_return, 2) if ytd_return else None
            })
    
    print(f"筛选完成! 共 {len(results)} 只满足条件")
    
    if results:
        results_df = pd.DataFrame(results)
        results_df = results_df.sort_values('年内涨幅', ascending=False)
        top20 = results_df.head(20)
        
        print("\n📊 年内涨幅Top20：")
        for _, row in top20.iterrows():
            print(f"  {row['交易所']}.{row['股票代码']} {row['股票简称']} 价:{row['当前价']} 涨:{row['年内涨幅']}%")
        
        md_content = f"# {TODAY_STR} 全市场A股多头排列筛选Top20\n\n"
        md_content += f"**筛选基准日**: {TODAY_STR}\n\n"
        md_content += "**筛选条件**: 股价 > MA20 > MA40 > MA80 > MA160\n\n"
        md_content += "**排序方式**: 按年内涨幅降序排列\n\n"
        md_content += f"**筛选总数**: {len(results_df)} 只股票满足条件\n\n"
        md_content += "| 股票代码 | 股票简称 | 当前价 | MA20 | MA40 | MA80 | MA160 | 年内涨幅(%) |\n"
        md_content += "|----------|----------|--------|------|------|-------|-------|-------------|\n"
        
        for _, row in top20.iterrows():
            code_full = f"{row['交易所']}.{row['股票代码']}"
            md_content += f"| {code_full} | {row['股票简称']} | {row['当前价']} | {row['MA20']} | {row['MA40']} | {row['MA80']} | {row['MA160']} | {row['年内涨幅']} |\n"
        
        md_content += f"\n*数据来源: baostock*\n*案例分析学习材料，不构成投资建议*\n"
        
        output_dir = "outputs/股票筛选"
        os.makedirs(output_dir, exist_ok=True)
        output_file = os.path.join(output_dir, f"{TODAY_STR}_多头排列Top20.md")
        
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(md_content)
        
        print(f"\n✅ 结果已保存到: {output_file}")

if __name__ == "__main__":
    main()
