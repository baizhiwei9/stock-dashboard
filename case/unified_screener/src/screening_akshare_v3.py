#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股多头排列筛选器 - 批量高效版
"""

import akshare as ak
import pandas as pd
import numpy as np
import os
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import warnings
warnings.filterwarnings('ignore')

TODAY = datetime.now().strftime('%Y-%m-%d')
FIRST_DAY = f"{datetime.now().year}-01-05"


def get_stock_data(code):
    """获取单只股票日线数据"""
    try:
        df = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date='20241001',
            end_date=TODAY.replace('-', ''),
            adjust="qfq"
        )
        if df is not None and not df.empty:
            df['code'] = code
            return df
        return None
    except:
        return None


def process_batch(batch_codes, stock_df):
    """处理一批股票"""
    results = []
    batch_data = []
    
    # 并行获取数据
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(get_stock_data, code): code for code in batch_codes}
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                batch_data.append(result)
    
    if not batch_data:
        return results
    
    daily_df = pd.concat(batch_data, ignore_index=True)
    daily_df['trade_date'] = pd.to_datetime(daily_df['日期'], errors='coerce')
    daily_df['close'] = daily_df['收盘']
    
    for code in batch_codes:
        stock_daily = daily_df[daily_df['code'] == code].copy()
        
        if len(stock_daily) < 160:
            continue
        
        stock_daily = stock_daily.sort_values('trade_date')
        
        # 计算均线
        stock_daily['ma20'] = stock_daily['close'].rolling(window=20, min_periods=20).mean()
        stock_daily['ma40'] = stock_daily['close'].rolling(window=40, min_periods=40).mean()
        stock_daily['ma80'] = stock_daily['close'].rolling(window=80, min_periods=80).mean()
        stock_daily['ma160'] = stock_daily['close'].rolling(window=160, min_periods=160).mean()
        
        latest = stock_daily.iloc[-1]
        
        # 多头排列条件
        if (pd.notna(latest['ma20']) and pd.notna(latest['ma40']) and 
            pd.notna(latest['ma80']) and pd.notna(latest['ma160'])):
            if (latest['close'] > latest['ma20'] > latest['ma40'] > 
                latest['ma80'] > latest['ma160']):
                
                # 年内涨幅
                ytd_df = stock_daily[stock_daily['trade_date'] >= pd.to_datetime(FIRST_DAY)]
                if len(ytd_df) >= 2:
                    ytd_return = (latest['close'] / ytd_df.iloc[0]['close'] - 1) * 100
                else:
                    ytd_return = None
                
                exchange = 'SH' if code.startswith(('6', '5')) else 'SZ'
                name = stock_df[stock_df['code'] == code]['name'].values
                stock_name = name[0] if len(name) > 0 else code
                
                results.append({
                    '股票代码': code,
                    '交易所': exchange,
                    '股票简称': stock_name,
                    '当前价': round(latest['close'], 2),
                    'MA20': round(latest['ma20'], 2),
                    'MA40': round(latest['ma40'], 2),
                    'MA80': round(latest['ma80'], 2),
                    'MA160': round(latest['ma160'], 2),
                    '年内涨幅': round(ytd_return, 2) if ytd_return is not None else None
                })
    
    return results


def main():
    print("=" * 60)
    print("A股多头排列筛选器 - 批量高效版")
    print("=" * 60)
    print(f"筛选基准日: {TODAY}")
    
    # 1. 获取全市场A股列表
    print("\n获取全市场A股列表...")
    try:
        stock_df = ak.stock_zh_a_spot()
        print(f"获取到 {len(stock_df)} 只股票")
    except Exception as e:
        print(f"获取失败: {e}")
        return
    
    stock_df = stock_df[['代码', '名称']].copy()
    stock_df.columns = ['code', 'name']
    
    # 排除ST股
    stock_df = stock_df[~stock_df['name'].str.contains('ST|退', na=False)]
    
    # 提取纯数字代码
    stock_df['code_num'] = stock_df['code'].str.extract(r'(\d{6})')[0]
    stock_df = stock_df.dropna(subset=['code_num'])
    
    # 过滤
    def is_main_board(code):
        if code.startswith('688'):
            return False
        if code.startswith(('8', '4')):
            return False
        return len(code) == 6 and code.isdigit()
    
    stock_df = stock_df[stock_df['code_num'].apply(is_main_board)]
    stock_df['code'] = stock_df['code_num']
    
    codes = stock_df['code'].unique().tolist()
    print(f"过滤后剩余 {len(codes)} 只")
    
    # 2. 分批处理
    batch_size = 300
    all_results = []
    
    print(f"\n分批获取日线数据（每批{batch_size}只）...")
    
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i+batch_size]
        batch_num = i // batch_size + 1
        total_batches = (len(codes) + batch_size - 1) // batch_size
        
        print(f"\n处理批次 {batch_num}/{total_batches} ({len(batch)}只股票)")
        batch_results = process_batch(batch, stock_df)
        all_results.extend(batch_results)
        print(f"本批找到 {len(batch_results)} 只，累计 {len(all_results)} 只")
        
        if i + batch_size < len(codes):
            time.sleep(1)  # 批次间休息
    
    print(f"\n筛选完成！共 {len(all_results)} 只股票满足多头排列条件")
    
    if all_results:
        results_df = pd.DataFrame(all_results)
        results_df = results_df.sort_values('年内涨幅', ascending=False)
        top20 = results_df.head(20)
        
        print("\n📊 年内涨幅Top20：")
        for _, row in top20.iterrows():
            code_full = f"{row['交易所']}.{row['股票代码']}"
            ytd = f"{row['年内涨幅']:.2f}%" if pd.notna(row['年内涨幅']) else "N/A"
            print(f"  {code_full} {row['股票简称']} 价:{row['当前价']} 涨:{ytd}")
        
        # 生成Markdown
        md_content = f"# {TODAY} 全市场A股多头排列筛选Top20\n\n"
        md_content += f"**筛选基准日**: {TODAY}\n\n"
        md_content += "**筛选条件**: 股价 > MA20 > MA40 > MA80 > MA160\n\n"
        md_content += "**排序方式**: 按年内涨幅降序排列\n\n"
        md_content += f"**筛选总数**: {len(results_df)} 只股票满足条件\n\n"
        md_content += "| 股票代码 | 股票简称 | 当前价 | MA20 | MA40 | MA80 | MA160 | 年内涨幅(%) |\n"
        md_content += "|----------|----------|--------|------|------|-------|-------|-------------|\n"
        
        for _, row in top20.iterrows():
            code_full = f"{row['交易所']}.{row['股票代码']}"
            ytd_display = f"{row['年内涨幅']:.2f}" if pd.notna(row['年内涨幅']) else "N/A"
            md_content += f"| {code_full} | {row['股票简称']} | {row['当前价']} | {row['MA20']} | {row['MA40']} | {row['MA80']} | {row['MA160']} | {ytd_display} |\n"
        
        md_content += f"\n*数据来源: AkShare*\n"
        md_content += "*案例分析学习材料，不构成投资建议*\n"
        
        output_dir = "outputs/股票筛选"
        os.makedirs(output_dir, exist_ok=True)
        output_file = os.path.join(output_dir, f"{TODAY}_多头排列Top20.md")
        
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(md_content)
        
        print(f"\n📁 已保存: {output_file}")
        print("=" * 60)
        print("✅ 任务完成！")
    else:
        print("⚠️ 无满足条件的股票")


if __name__ == '__main__':
    main()
