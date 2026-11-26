#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股多头排列筛选器 - Baostock版
案例分析学习材料，不构成投资建议
筛选条件：股价 > MA20 > MA40 > MA80 > MA160
"""

import baostock as bs
import pandas as pd
import numpy as np
import os
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import warnings
warnings.filterwarnings('ignore')

# 全局变量
TODAY = None
FIRST_DAY = None


def get_stock_data_baostock(code):
    """获取单只股票日线数据"""
    try:
        # 转换代码格式
        if code.startswith('6'):
            bs_code = f"sh.{code}"
        else:
            bs_code = f"sz.{code}"
        
        # 获取日线数据 - 使用 YYYY-MM-DD 格式
        rs = bs.query_history_k_data_plus(
            bs_code,
            "date,code,open,high,low,close,volume,amount",
            start_date=FIRST_DAY,
            end_date=TODAY,
            frequency="d",
            adjustflag="2"  # 前复权
        )
        
        data_list = []
        while (rs.error_code == '0') & rs.next():
            data_list.append(rs.get_row_data())
        
        if data_list and len(data_list) > 160:
            df = pd.DataFrame(data_list, columns=rs.fields)
            df['code'] = code
            return df
        return None
    except Exception as e:
        return None


def main():
    global TODAY, FIRST_DAY
    
    # baostock 需要 YYYY-MM-DD 格式
    TODAY = datetime.now().strftime('%Y-%m-%d')
    FIRST_DAY = f"{datetime.now().year}-01-05"
    TODAY_STR = datetime.now().strftime('%Y%m%d')  # 用于文件名
    
    print("=" * 60)
    print("A股多头排列筛选器 (Baostock版)")
    print("=" * 60)
    print(f"筛选基准日: {TODAY}")
    
    # 1. 登录 baostock
    lg = bs.login()
    if lg.error_code != '0':
        print(f"登录失败: {lg.error_msg}")
        return
    
    print("\n获取全市场A股列表...")
    
    # 获取沪深A股列表
    rs = bs.query_stock_basic(code_name="")
    stocks = []
    while rs.error_code == '0' and rs.next():
        stocks.append(rs.get_row_data())
    
    bs.logout()
    
    if not stocks:
        print("获取股票列表失败")
        return
    
    stock_df = pd.DataFrame(stocks, columns=['code', 'code_name', 'ipoDate', 'outDate', 'type', 'status'])
    
    # 过滤
    stock_df = stock_df[stock_df['status'] == '1']  # 上市状态
    stock_df = stock_df[stock_df['type'] == '1']     # A股
    stock_df = stock_df[~stock_df['code_name'].str.contains('ST|退', na=False)]
    stock_df = stock_df[~stock_df['code'].str.startswith('sh.688')]  # 排除科创板
    stock_df = stock_df[~stock_df['code'].str.startswith('sh.8')]    # 排除北交所
    stock_df = stock_df[~stock_df['code'].str.startswith('sh.4')]    # 排除北交所
    
    stock_df['code'] = stock_df['code'].str.replace('sh.', '').str.replace('sz.', '')
    codes = stock_df['code'].tolist()
    
    print(f"获取到 {len(stock_df)} 只股票")
    print(f"排除ST股、科创板和北交所后: {len(codes)} 只")
    
    # 2. 并行获取日线数据
    print(f"\n并行获取日线数据（使用10线程）...")
    all_data = []
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(get_stock_data_baostock, code): code for code in codes}
        
        completed = 0
        for future in as_completed(futures):
            completed += 1
            if completed % 500 == 0:
                print(f"进度: {completed}/{len(codes)}")
            
            result = future.result()
            if result is not None:
                all_data.append(result)
    
    print(f"成功获取 {len(all_data)} 只股票的数据")
    
    if not all_data:
        print("⚠️ 无足够数据，尝试使用原始脚本的 akshare 方式...")
        return
    
    daily_df = pd.concat(all_data, ignore_index=True)
    
    # 数据类型转换
    for col in ['open', 'high', 'low', 'close', 'volume', 'amount']:
        if col in daily_df.columns:
            daily_df[col] = pd.to_numeric(daily_df[col], errors='coerce')
    daily_df['trade_date'] = pd.to_datetime(daily_df['date'], errors='coerce')
    
    # 3. 计算均线并筛选
    print("\n计算均线并筛选...")
    results = []
    qualified = 0
    FIRST_DAY_PD = pd.to_datetime(FIRST_DAY)
    
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
                qualified += 1
                
                # 计算年内涨幅
                ytd_df = stock_daily[stock_daily['trade_date'] >= FIRST_DAY_PD]
                if len(ytd_df) >= 2:
                    ytd_return = (latest['close'] / ytd_df.iloc[0]['close'] - 1) * 100
                else:
                    ytd_return = None
                
                # 判断交易所
                exchange = 'SH' if code.startswith('6') else 'SZ'
                
                results.append({
                    '股票代码': code,
                    '交易所': exchange,
                    '股票简称': stock_df[stock_df['code'] == code]['code_name'].values[0] if code in stock_df['code'].values else code,
                    '当前价': round(latest['close'], 2),
                    'MA20': round(latest['ma20'], 2),
                    'MA40': round(latest['ma40'], 2),
                    'MA80': round(latest['ma80'], 2),
                    'MA160': round(latest['ma160'], 2),
                    '年内涨幅': round(ytd_return, 2) if ytd_return is not None else None
                })
        
        if idx % 1000 == 0 and idx > 0:
            print(f"筛选进度: {idx}/{len(codes)} (已发现{qualified}只)")
    
    print(f"\n筛选完成！共 {len(results)} 只股票满足多头排列条件")
    
    if results:
        results_df = pd.DataFrame(results)
        results_df = results_df.sort_values('年内涨幅', ascending=False)
        top20 = results_df.head(20)
        
        print("\n📊 年内涨幅Top20：")
        for _, row in top20.iterrows():
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
        
        md_content += f"\n*数据来源: Baostock*\n"
        md_content += "*案例分析学习材料，不构成投资建议*\n"
        md_content += "*股市有风险，投资需谨慎*\n"
        
        # 保存结果
        output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "outputs", "股票筛选")
        os.makedirs(output_dir, exist_ok=True)
        
        output_file = os.path.join(output_dir, f"{TODAY_STR}_多头排列Top20.md")
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
