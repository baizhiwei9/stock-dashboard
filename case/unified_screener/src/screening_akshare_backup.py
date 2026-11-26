#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股多头排列筛选器 - AkShare备用数据源版
案例分析学习材料，不构成投资建议
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
    print("A股多头排列筛选器 (AkShare备用版)")
    print("=" * 60)
    
    global TODAY, FIRST_DAY
    
    TODAY = datetime.now().strftime('%Y-%m-%d')
    FIRST_DAY = f"{datetime.now().year}-01-05"  # 年初第一个交易日附近
    
    print(f"筛选基准日: {TODAY}")
    
    # 1. 获取全市场A股列表 - 使用备用接口
    print("\n获取全市场A股列表...")
    codes = []
    stock_names = {}
    
    try:
        # 尝试使用stock_info接口（从本地文件读取）
        print("尝试从本地数据获取股票列表...")
        try:
            stock_info = ak.stock_info_a_code_name()
            stock_df = stock_info[['code', 'name']].copy()
            stock_df = stock_df[stock_df['code'].str.match(r'^\d{6}$')]
            stock_df = stock_df[~stock_df['code'].str.startswith('688')]  # 排除科创板
            stock_df = stock_df[~stock_df['code'].str.startswith('8')]    # 排除北交所
            stock_df = stock_df[~stock_df['code'].str.startswith('4')]    # 排除北交所
            stock_df = stock_df[~stock_df['name'].str.contains('ST|退', na=False)]
            codes = stock_df['code'].tolist()
            stock_names = dict(zip(stock_df['code'], stock_df['name']))
            print(f"通过stock_info获取到 {len(codes)} 只股票")
        except Exception as e1:
            print(f"stock_info方式失败: {e1}")
            
            # 备用方案：从沪深300成分股获取
            print("尝试从沪深300获取股票列表...")
            try:
                hs300 = ak.stock_index_weight_cons_ths(symbol="000300")
                stock_df = hs300[['代码', '名称']].copy()
                stock_df.columns = ['code', 'name']
                codes = stock_df['code'].tolist()
                stock_names = dict(zip(stock_df['code'], stock_df['name']))
                print(f"通过沪深300获取到 {len(codes)} 只股票")
            except Exception as e2:
                print(f"沪深300方式失败: {e2}")
                
                # 再备选：使用上证50
                print("尝试从上证50获取股票列表...")
                try:
                    sz50 = ak.stock_index_weight_cons_ths(symbol="000016")
                    stock_df = sz50[['代码', '名称']].copy()
                    stock_df.columns = ['code', 'name']
                    codes = stock_df['code'].tolist()
                    stock_names = dict(zip(stock_df['code'], stock_df['name']))
                    print(f"通过上证50获取到 {len(codes)} 只股票")
                except Exception as e3:
                    print(f"上证50方式也失败: {e3}")
                    print("所有数据源均不可用")
                    return
    except Exception as e:
        print(f"获取股票列表失败: {e}")
        return
    
    if not codes:
        print("没有获取到股票代码")
        return
    
    print(f"\n待筛选股票数量: {len(codes)}")
    
    # 2. 并行获取日线数据
    print(f"\n并行获取日线数据（使用10线程）...")
    all_data = []
    failed = 0
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(get_stock_data, code): code for code in codes}
        
        completed = 0
        for future in as_completed(futures):
            completed += 1
            if completed % 100 == 0:
                print(f"进度: {completed}/{len(codes)}")
            
            result = future.result()
            if result is not None:
                all_data.append(result)
            else:
                failed += 1
    
    print(f"进度: {len(codes)}/{len(codes)}")
    print(f"成功获取: {len(all_data)} 只, 失败: {failed} 只")
    
    if not all_data:
        print("无日线数据")
        return
    
    daily_df = pd.concat(all_data, ignore_index=True)
    
    # 日期列标准化
    if '日期' in daily_df.columns:
        daily_df['trade_date'] = pd.to_datetime(daily_df['日期'], errors='coerce')
        daily_df['close'] = daily_df['收盘']
    else:
        daily_df['trade_date'] = pd.to_datetime(daily_df['date'], errors='coerce')
    
    # 检查数据完整性
    stock_counts = daily_df.groupby('code').size()
    print(f"获取到 {len(daily_df)} 条数据")
    print(f"平均每只股票: {stock_counts.mean():.0f} 条")
    
    # 3. 计算均线并筛选
    print("\n计算均线并筛选...")
    results = []
    qualified = 0
    valid_codes = daily_df['code'].unique().tolist()
    
    for idx, code in enumerate(valid_codes):
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
                ytd_df = stock_daily[stock_daily['trade_date'] >= pd.to_datetime(FIRST_DAY)]
                if len(ytd_df) >= 2:
                    ytd_return = (latest['close'] / ytd_df.iloc[0]['close'] - 1) * 100
                else:
                    ytd_return = None
                
                # 判断交易所
                exchange = 'SH' if code.startswith(('6', '5')) else 'SZ'
                
                name = stock_names.get(code, code)
                
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
            print(f"筛选进度: {idx}/{len(valid_codes)} (已发现{qualified}只)")
    
    print(f"\n筛选完成！共 {len(results)} 只股票满足多头排列条件")
    
    if results:
        results_df = pd.DataFrame(results)
        
        # 按年内涨幅排序
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
        
        md_content += f"\n*数据来源: AkShare*\n"
        md_content += "*案例分析学习材料，不构成投资建议*\n"
        md_content += "*股市有风险，投资需谨慎*\n"
        
        # 保存结果
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_dir = os.path.dirname(script_dir)
        root_dir = os.path.dirname(project_dir)
        output_dir = os.path.join(root_dir, "outputs", "股票筛选")
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
