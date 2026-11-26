#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股多头排列筛选器 - AkShare改进版
使用 pro_bar 接口逐股获取数据，避免代理问题
筛选条件：股价 > MA20 > MA40 > MA80 > MA160
"""

import akshare as ak
import pandas as pd
import numpy as np
import os
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import warnings
warnings.filterwarnings('ignore')

TODAY = datetime.now().strftime('%Y-%m-%d')
FIRST_DAY = f"{datetime.now().year}-01-05"

# 输出目录
OUTPUT_DIR = "outputs/股票筛选"
os.makedirs(OUTPUT_DIR, exist_ok=True)


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
    except Exception as e:
        return None


def main():
    print("=" * 60)
    print("A股多头排列筛选器 (AkShare改进版)")
    print("=" * 60)
    print(f"筛选基准日: {TODAY}")
    
    # 生成候选股票代码列表（沪市6开头，深市0开头）
    print("\n生成候选股票代码列表...")
    
    candidate_codes = []
    # 沪市主板
    for i in range(600000, 602000):
        candidate_codes.append(str(i))
    # 科创板（排除）
    # for i in range(688000, 689000):
    #     candidate_codes.append(str(i))
    # 深市主板
    for i in range(1, 100):
        candidate_codes.append(f"000{str(i).zfill(3)}")
    for i in range(100, 1000):
        candidate_codes.append(f"00{str(i).zfill(3)}")
    
    print(f"候选代码数量: {len(candidate_codes)}")
    
    # 并行获取日线数据
    print(f"\n并行获取日线数据（使用15线程）...")
    all_data = []
    
    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = {executor.submit(get_stock_data, code): code for code in candidate_codes}
        
        completed = 0
        for future in as_completed(futures):
            completed += 1
            if completed % 500 == 0:
                print(f"进度: {completed}/{len(candidate_codes)}")
            
            result = future.result()
            if result is not None and len(result) >= 160:
                all_data.append(result)
    
    print(f"获取完成! 有效数据: {len(all_data)} 只")
    
    if not all_data:
        print("无有效日线数据")
        return
    
    daily_df = pd.concat(all_data, ignore_index=True)
    
    # 日期列标准化
    if '日期' in daily_df.columns:
        daily_df['trade_date'] = pd.to_datetime(daily_df['日期'], errors='coerce')
        daily_df['close'] = daily_df['收盘']
    
    # 3. 计算均线并筛选
    print("\n计算均线并筛选...")
    results = []
    
    codes = daily_df['code'].unique().tolist()
    
    for idx, code in enumerate(codes):
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
        
        # 检查多头排列条件
        if (pd.notna(latest['ma20']) and pd.notna(latest['ma40']) and 
            pd.notna(latest['ma80']) and pd.notna(latest['ma160'])):
            if (latest['close'] > latest['ma20'] > latest['ma40'] > 
                latest['ma80'] > latest['ma160']):
                
                # 获取股票名称
                name = latest.get('股票名称', code)
                
                # 计算年内涨幅
                ytd_df = stock_daily[stock_daily['trade_date'] >= pd.to_datetime(FIRST_DAY)]
                if len(ytd_df) >= 2:
                    ytd_return = (latest['close'] / ytd_df.iloc[0]['close'] - 1) * 100
                else:
                    ytd_return = None
                
                # 判断交易所
                exchange = 'SH' if code.startswith(('6', '5')) else 'SZ'
                
                results.append({
                    '股票代码': code,
                    '交易所': exchange,
                    '股票简称': name,
                    '当前价': round(latest['close'], 2),
                    'MA20': round(latest['ma20'], 2),
                    'MA40': round(latest['ma40'], 2),
                    'MA80': round(latest['ma80'], 2),
                    'MA160': round(latest['ma160'], 2),
                    '年内涨幅': round(ytd_return, 2) if ytd_return is not None else None
                })
        
        if idx % 500 == 0 and idx > 0:
            print(f"筛选进度: {idx}/{len(codes)}")
    
    print(f"\n筛选完成！共 {len(results)} 只股票满足多头排列条件")
    
    if results:
        results_df = pd.DataFrame(results)
        
        # 按年内涨幅排序
        results_df = results_df.sort_values('年内涨幅', ascending=False)
        top20 = results_df.head(20)
        
        print("\n📊 年内涨幅Top20：")
        for i, row in top20.iterrows():
            code_full = f"{row['交易所']}.{row['股票代码']}"
            ytd = f"{row['年内涨幅']:.2f}%" if pd.notna(row['年内涨幅']) else "N/A"
            print(f"  {code_full} {row['股票简称']} 价:{row['当前价']} 涨:{ytd}")
        
        # 生成Markdown报告
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
        md_content += "*股市有风险，投资需谨慎*\n"
        
        # 保存结果
        output_file = os.path.join(OUTPUT_DIR, f"{TODAY}_多头排列Top20.md")
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(md_content)
        
        print(f"\n📁 已保存: {output_file}")
        print("=" * 60)
        print("✅ 任务完成！")
        print("=" * 60)
    else:
        print("⚠️ 无满足条件的股票")


if __name__ == '__main__':
    main()
