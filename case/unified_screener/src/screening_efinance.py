#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股多头排列筛选器 - efinance版
案例分析学习材料，不构成投资建议
筛选条件：股价 > MA20 > MA40 > MA80 > MA160
"""

import efinance as ef
import pandas as pd
import numpy as np
import os
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')


def main():
    TODAY = datetime.now().strftime('%Y-%m-%d')
    TODAY_STR = datetime.now().strftime('%Y%m%d')
    YEAR = datetime.now().year
    
    print("=" * 60)
    print("A股多头排列筛选器 (efinance版)")
    print("=" * 60)
    print(f"筛选基准日: {TODAY}")
    
    # 1. 获取全市场A股列表
    print("\n获取全市场A股列表...")
    try:
        # 尝试获取股票列表
        stocks = ef.stock.get_stock_code_list()
        if stocks is None:
            print("股票列表获取失败")
            return
        print(f"获取到 {len(stocks)} 只股票")
    except Exception as e:
        print(f"获取股票列表失败: {e}")
        return
    
    # 过滤
    codes = [s for s in stocks if s.startswith(('0', '3', '6')) and len(s) == 6]
    print(f"主板股票: {len(codes)} 只")
    
    # 2. 获取所有股票日线数据
    print(f"\n获取日线数据（批量获取）...")
    try:
        # 获取所有股票的日线数据
        daily_df = ef.stock.get_daily_brief(count=100)
        if daily_df is None or daily_df.empty:
            print("日线数据获取失败，尝试其他方式...")
            # 尝试获取单只股票测试
            test = ef.stock.get_daily('000001')
            if test is not None:
                print(f"单只股票数据获取正常: {len(test)} 条")
            else:
                print("无法获取任何数据")
                return
    except Exception as e:
        print(f"获取日线数据失败: {e}")
        return
    
    # 3. 获取所有股票完整数据
    print(f"\n获取完整日线数据...")
    all_data = []
    batch_size = 50
    
    for i in range(0, min(len(codes), 1000), batch_size):
        batch_codes = codes[i:i+batch_size]
        print(f"进度: {i}/{min(len(codes), 1000)}")
        try:
            for code in batch_codes:
                df = ef.stock.get_daily(code, beg=f'{YEAR}0101', end=TODAY_STR)
                if df is not None and len(df) >= 160:
                    df['code'] = code
                    all_data.append(df)
        except:
            pass
    
    if not all_data:
        print("无足够数据")
        return
    
    daily_df = pd.concat(all_data, ignore_index=True)
    print(f"获取到 {len(all_data)} 只股票的数据")
    
    # 4. 计算均线并筛选
    print("\n计算均线并筛选...")
    results = []
    codes_in_data = daily_df['code'].unique()
    
    for code in codes_in_data:
        stock_daily = daily_df[daily_df['code'] == code].copy()
        
        if len(stock_daily) < 160:
            continue
        
        stock_daily = stock_daily.sort_values('date')
        
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
                
                # 计算年内涨幅
                ytd_df = stock_daily[stock_daily['date'] >= f'{YEAR}0105']
                if len(ytd_df) >= 2:
                    ytd_return = (latest['close'] / ytd_df.iloc[0]['close'] - 1) * 100
                else:
                    ytd_return = None
                
                # 判断交易所
                exchange = 'SH' if code.startswith('6') else 'SZ'
                
                results.append({
                    '股票代码': code,
                    '交易所': exchange,
                    '股票简称': code,
                    '当前价': round(latest['close'], 2),
                    'MA20': round(latest['ma20'], 2),
                    'MA40': round(latest['ma40'], 2),
                    'MA80': round(latest['ma80'], 2),
                    'MA160': round(latest['ma160'], 2),
                    '年内涨幅': round(ytd_return, 2) if ytd_return is not None else None
                })
    
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
        
        md_content += f"\n*数据来源: efinance*\n"
        md_content += "*案例分析学习材料，不构成投资建议*\n"
        md_content += "*股市有风险，投资需谨慎*\n"
        
        # 保存结果
        output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "outputs", "股票筛选")
        os.makedirs(output_dir, exist_ok=True)
        
        output_file = os.path.join(output_dir, f"{TODAY}_多头排列Top20.md")
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
