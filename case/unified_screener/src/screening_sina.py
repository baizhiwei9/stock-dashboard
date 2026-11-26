#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股多头排列筛选器 - 新浪财经API版
案例分析学习材料，不构成投资建议
筛选条件：股价 > MA20 > MA40 > MA80 > MA160
"""

import requests
import pandas as pd
import numpy as np
import os
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import warnings
warnings.filterwarnings('ignore')

# 禁用代理
session = requests.Session()
session.trust_env = False


def get_stock_list_sina():
    """获取全市场A股列表（新浪财经API）"""
    url = 'https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData'
    params = {
        'page': 1,
        'num': 5000,
        'sort': 'symbol',
        'asc': 1,
        'node': 'hs_a',
        'symbol': '',
        '_s_r_a': 'page'
    }
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://finance.sina.com.cn/'
        }
        resp = session.get(url, params=params, headers=headers, timeout=30)
        data = resp.json()
        
        stocks = []
        for item in data:
            code = item['symbol']
            # 排除沪市科创板(688)和北交所(8/4开头)
            if code.startswith(('6', '5')) or code.startswith(('0', '3')):
                if not code.startswith('688'):
                    stocks.append({
                        'code': code,
                        'name': item.get('name', code)
                    })
        return stocks
    except Exception as e:
        print(f"获取股票列表失败: {e}")
        return []


def get_stock_data_sina(code):
    """获取单只股票日线数据（新浪财经K线API）"""
    try:
        url = 'https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData'
        
        # 新浪代码格式：sh或sz前缀
        if code.startswith('6'):
            symbol = f'sh{code}'
        else:
            symbol = f'sz{code}'
        
        params = {
            'symbol': symbol,
            'scale': 240,  # 日线
            'datalen': 250,  # 获取足够的数据计算均线
            'ma': 'no'  # 不需要自带均线
        }
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://finance.sina.com.cn/'
        }
        
        resp = session.get(url, params=params, headers=headers, timeout=15)
        data = resp.json()
        
        if data and len(data) > 0:
            df = pd.DataFrame(data)
            df['code'] = code
            return df
        return None
    except Exception as e:
        return None


def main():
    print("=" * 60)
    print("A股多头排列筛选器 (新浪财经API版)")
    print("=" * 60)
    
    global TODAY, FIRST_DAY
    
    TODAY = datetime.now().strftime('%Y-%m-%d')
    FIRST_DAY = f"{datetime.now().year}-01-05"  # 年初第一个交易日附近
    
    print(f"筛选基准日: {TODAY}")
    
    # 1. 获取全市场A股列表
    print("\n获取全市场A股列表...")
    stock_list = get_stock_list_sina()
    
    if not stock_list:
        print("获取股票列表失败，尝试备用方法...")
        # 备用：使用代码列表
        stock_list = []
        # 沪市主板
        for i in range(600000, 602000):
            stock_list.append({'code': str(i), 'name': ''})
        # 深市主板
        for i in range(1, 1000):
            stock_list.append({'code': f'000{i:03d}', 'name': ''})
        for i in range(1, 1000):
            stock_list.append({'code': f'002{i:03d}', 'name': ''})
        for i in range(1, 500):
            stock_list.append({'code': f'300{i:03d}', 'name': ''})
    
    stock_df = pd.DataFrame(stock_list)
    print(f"获取到 {len(stock_df)} 只股票")
    
    # 过滤
    codes = [c for c in stock_df['code'].unique() 
             if not c.startswith('688') and len(c) == 6]
    print(f"排除科创板后剩余 {len(codes)} 只")
    
    if not codes:
        print("没有符合条件的股票")
        return
    
    # 2. 并行获取日线数据
    print(f"\n并行获取日线数据（使用15线程）...")
    all_data = []
    
    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = {executor.submit(get_stock_data_sina, code): code for code in codes}
        
        completed = 0
        for future in as_completed(futures):
            completed += 1
            if completed % 200 == 0:
                print(f"进度: {completed}/{len(codes)}")
            
            result = future.result()
            if result is not None and len(result) > 160:
                all_data.append(result)
            time.sleep(0.05)  # 避免频率限制
    
    print(f"进度: {len(codes)}/{len(codes)}")
    
    if not all_data:
        print("无日线数据")
        return
    
    daily_df = pd.concat(all_data, ignore_index=True)
    print(f"获取到 {len(daily_df)} 条数据，{len(all_data)} 只股票有足够数据")
    
    # 3. 计算均线并筛选
    print("\n计算均线并筛选...")
    results = []
    qualified = 0
    
    for code in codes:
        stock_daily = daily_df[daily_df['code'] == code].copy()
        
        if len(stock_daily) < 160:
            continue
        
        stock_daily = stock_daily.sort_values('day')
        
        # 转换数值列
        stock_daily['close'] = pd.to_numeric(stock_daily['close'], errors='coerce')
        stock_daily['day'] = pd.to_datetime(stock_daily['day'], errors='coerce')
        stock_daily = stock_daily.dropna(subset=['close', 'day'])
        
        if len(stock_daily) < 160:
            continue
        
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
                ytd_df = stock_daily[stock_daily['day'] >= pd.to_datetime(FIRST_DAY)]
                if len(ytd_df) >= 2:
                    ytd_return = (latest['close'] / ytd_df.iloc[0]['close'] - 1) * 100
                else:
                    ytd_return = None
                
                # 获取股票名称
                name = stock_df[stock_df['code'] == code]['name'].values
                name = name[0] if len(name) > 0 and name[0] else code
                
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
        
        if qualified > 0 and qualified % 10 == 0:
            print(f"  已发现{qualified}只满足条件的股票...")
    
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
        
        md_content += f"\n*数据来源: 新浪财经API*\n"
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
        
        # 打印最终Top20供确认
        print("\n最终Top20结果预览：")
        print(top20[['股票代码', '股票简称', '当前价', '年内涨幅']].to_string(index=False))
    else:
        print("⚠️ 无满足条件的股票")


if __name__ == '__main__':
    main()
