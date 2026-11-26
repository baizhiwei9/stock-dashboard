#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股多头排列筛选器 - 综合版
案例分析学习材料，不构成投资建议
筛选条件：股价 > MA20 > MA40 > MA80 > MA160
"""

import akshare as ak
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
    print("A股多头排列筛选器 (综合版)")
    print("=" * 60)
    print(f"筛选基准日: {TODAY}")
    
    # 1. 获取全市场A股列表
    print("\n获取全市场A股列表...")
    all_codes = []
    all_names = {}
    
    # 沪市
    try:
        df_sh = ak.stock_info_sh_name_code(symbol="主板A股")
        for _, row in df_sh.iterrows():
            all_codes.append(row['证券代码'])
            all_names[row['证券代码']] = row['证券简称']
        print(f"沪市主板: {len(df_sh)} 只")
    except Exception as e:
        print(f"沪市获取失败: {e}")
    
    # 深市
    try:
        df_sz = ak.stock_info_sz_name_code(symbol="A股列表")
        if df_sz is not None:
            for _, row in df_sz.iterrows():
                code = str(row['A股代码'])
                if code not in all_names:
                    all_codes.append(code)
                    all_names[code] = row['A股简称']
            print(f"深市A股: {len(df_sz)} 只")
    except Exception as e:
        print(f"深市获取失败: {e}")
    
    # 过滤
    codes = [str(c) for c in all_codes if str(c).isdigit() and len(str(c)) == 6 
             and not str(c).startswith(('688', '8', '4'))]
    codes = list(set(codes))
    print(f"共 {len(codes)} 只股票（排除科创板、北交所）")
    
    if not codes:
        print("无股票数据")
        return
    
    # 2. 获取日线数据（使用2年前的数据确保有160+条）
    print(f"\n获取日线数据（采样200只，需2年历史数据）...")
    # 均匀采样
    step = max(1, len(codes) // 200)
    sampled_codes = codes[::step][:200]
    print(f"采样数量: {len(sampled_codes)}")
    
    # 使用2024年1月开始，确保有足够数据
    START_DATE = f'{YEAR-2}0101'
    
    all_data = []
    success = 0
    
    for i, code in enumerate(sampled_codes):
        if i % 50 == 0:
            print(f"进度: {i}/{len(sampled_codes)}")
        try:
            if code.startswith('6'):
                ts_code = f"sh{code}"
            else:
                ts_code = f"sz{code}"
            
            df = ak.stock_zh_a_daily(
                symbol=ts_code,
                start_date=START_DATE,
                end_date=TODAY_STR,
                adjust="qfq"
            )
            if df is not None and len(df) >= 160:
                df['code'] = code
                all_data.append(df)
                success += 1
        except:
            pass
    
    print(f"成功获取 {success} 只股票的数据")
    
    if not all_data:
        print("⚠️ 无足够数据")
        return
    
    daily_df = pd.concat(all_data, ignore_index=True)
    
    # 转换日期列为统一格式
    daily_df['date'] = pd.to_datetime(daily_df['date']).dt.strftime('%Y-%m-%d')
    
    # 3. 计算均线并筛选
    print("\n计算均线并筛选...")
    results = []
    
    for code in daily_df['code'].unique():
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
                ytd_df = stock_daily[stock_daily['date'] >= f'{YEAR}-01-05']
                if len(ytd_df) >= 2:
                    ytd_return = (latest['close'] / ytd_df.iloc[0]['close'] - 1) * 100
                else:
                    ytd_return = None
                
                exchange = 'SH' if code.startswith('6') else 'SZ'
                name = all_names.get(code, code)
                
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
    
    print(f"筛选完成！共 {len(results)} 只股票满足多头排列条件")
    
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
        md_content += f"**采样说明**: 采样约200只代表性股票\n\n"
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
