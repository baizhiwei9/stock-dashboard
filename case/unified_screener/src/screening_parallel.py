#!/usr/bin/env python3
"""
全市场A股多头排列筛选 - 优化版
使用并行处理加速baostock数据获取
"""

import baostock as bs
import pandas as pd
import numpy as np
import os
import time
from datetime import datetime, timedelta
from multiprocessing import Pool, cpu_count
import warnings
warnings.filterwarnings('ignore')

# 全局变量（用于并行处理）
GLOBAL_TODAY = '2026-04-10'
GLOBAL_FIRST_DAY = '2026-01-05'
GLOBAL_START_DATE = (datetime.strptime(GLOBAL_TODAY, '%Y-%m-%d') - timedelta(days=365)).strftime('%Y-%m-%d')

def process_single_stock(args):
    """处理单只股票"""
    ts_code, stock_name, ipo_date = args
    
    try:
        # 每次创建新连接
        lg = bs.login()
        if lg.error_code != '0':
            bs.logout()
            return None
        
        rs = bs.query_history_k_data_plus(
            ts_code,
            "date,code,open,high,low,close,volume",
            start_date=GLOBAL_START_DATE,
            end_date=GLOBAL_TODAY,
            frequency="d",
            adjustflag="3"
        )
        
        if rs.error_code != '0':
            bs.logout()
            return None
        
        data_list = []
        while (rs.error_code == '0') & rs.next():
            data_list.append(rs.get_row_data())
        
        bs.logout()
        
        if not data_list:
            return None
        
        df = pd.DataFrame(data_list, columns=rs.fields)
        df['date'] = pd.to_datetime(df['date'])
        df['close'] = pd.to_numeric(df['close'], errors='coerce')
        df = df.sort_values('date').dropna(subset=['close'])
        
        if len(df) < 160:
            return None
        
        # 计算均线
        df['ma20'] = df['close'].rolling(window=20, min_periods=20).mean()
        df['ma40'] = df['close'].rolling(window=40, min_periods=40).mean()
        df['ma80'] = df['close'].rolling(window=80, min_periods=80).mean()
        df['ma160'] = df['close'].rolling(window=160, min_periods=160).mean()
        
        latest = df.iloc[-1]
        
        # 筛选条件：股价 > ma20 > ma40 > ma80 > ma160
        if (pd.notna(latest['ma20']) and pd.notna(latest['ma40']) and 
            pd.notna(latest['ma80']) and pd.notna(latest['ma160'])):
            if (latest['close'] > latest['ma20'] > latest['ma40'] > latest['ma80'] > latest['ma160']):
                # 计算年内涨幅
                effective_start = max(GLOBAL_FIRST_DAY, ipo_date)
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
    TODAY = GLOBAL_TODAY
    OUTPUT_DIR = "outputs/股票筛选"
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    print("=== 全市场A股多头排列筛选（并行优化版） ===\n")
    print(f"执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"筛选基准日: {TODAY}")
    
    # 获取所有A股股票代码
    lg = bs.login()
    if lg.error_code != '0':
        print(f"❌ 登录失败: {lg.error_msg}")
        exit(1)
    print("✅ baostock登录成功")
    
    rs = bs.query_stock_basic(code_name="")
    data_list = []
    while (rs.error_code == '0') & rs.next():
        data_list.append(rs.get_row_data())
    bs.logout()
    
    if not data_list:
        print("❌ 获取股票列表失败")
        exit(1)
    
    stocks_df = pd.DataFrame(data_list, columns=rs.fields)
    stocks_df = stocks_df[stocks_df['type'] == '1']
    # 只保留沪市和深市
    stocks_df = stocks_df[stocks_df['code'].str.startswith(('sh.', 'sz.'))]
    print(f"获取到 {len(stocks_df)} 只A股股票")
    
    print(f"2026年第一个交易日: {GLOBAL_FIRST_DAY}")
    print(f"历史数据起始日: {GLOBAL_START_DATE}")
    
    # 准备参数
    args_list = [
        (row['code'], row['code_name'], row['ipoDate'])
        for _, row in stocks_df.iterrows()
    ]
    
    print(f"\n开始并行处理（使用 {min(cpu_count(), 8)} 个进程）...")
    
    # 使用多进程处理
    num_processes = min(cpu_count(), 8)
    results = []
    
    with Pool(processes=num_processes) as pool:
        for i, result in enumerate(pool.imap(process_single_stock, args_list, chunksize=10)):
            if i % 200 == 0:
                print(f"处理进度: {i}/{len(args_list)}")
            if result is not None:
                results.append(result)
    
    print(f"\n✅ 筛选完成！共发现 {len(results)} 只股票满足多头排列条件")
    
    if results:
        results_df = pd.DataFrame(results)
        results_df = results_df.sort_values('年内涨幅', ascending=False)
        top20 = results_df.head(20)
        
        print(f"\n📊 年内涨幅Top20：")
        print(top20[['股票代码', '交易所', '股票简称', '当前价', '年内涨幅']].to_string(index=False))
        
        # 生成Markdown文件
        md_content = f"# {TODAY} 全市场A股多头排列筛选Top20\n\n"
        md_content += f"**筛选基准日**: {TODAY}\n\n"
        md_content += "**筛选条件**: 股价 > MA20 > MA40 > MA80 > MA160\n\n"
        md_content += "**排序方式**: 按年内涨幅（从2026年第一个交易日到筛选基准日）降序排列\n\n"
        md_content += f"**筛选总数**: {len(results_df)} 只股票满足条件\n\n"
        md_content += f"**数据源**: baostock\n\n"
        md_content += "| 股票代码 | 股票简称 | 当前价 | MA20 | MA40 | MA80 | MA160 | 年内涨幅(%) |\n"
        md_content += "|----------|----------|--------|------|------|-------|-------|-------------|\n"
        
        for _, row in top20.iterrows():
            code_full = f"{row['交易所']}.{row['股票代码']}"
            ytd_display = f"{row['年内涨幅']:.2f}" if pd.notna(row['年内涨幅']) else "N/A"
            md_content += f"| {code_full} | {row['股票简称']} | {row['当前价']} | {row['MA20']} | {row['MA40']} | {row['MA80']} | {row['MA160']} | {ytd_display} |\n"
        
        md_content += f"\n*数据来源: baostock*\n"
        md_content += "*仅供理论学习和理论练习使用，不构成投资建议，不能作为投资依据，注意投资风险，股市有风险，投资需谨慎*\n"
        
        output_file = os.path.join(OUTPUT_DIR, f"{TODAY}_多头排列Top20.md")
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(md_content)
        
        print(f"\n📁 结果文件已保存: {output_file}")
        print(f"✅ 任务完成！")
    else:
        print("⚠️ 没有找到任何满足条件的股票")
        exit(1)

if __name__ == '__main__':
    main()
