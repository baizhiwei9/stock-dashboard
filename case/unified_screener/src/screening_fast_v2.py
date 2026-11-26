#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股多头排列筛选器 - 高效版 (新浪财经API)
筛选条件：股价 > MA20 > MA40 > MA80 > MA160
"""

import requests
import pandas as pd
import numpy as np
import os
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import warnings
warnings.filterwarnings('ignore')

session = requests.Session()
session.trust_env = False
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://finance.sina.com.cn/'
}

TODAY = datetime.now().strftime('%Y-%m-%d')
FIRST_DAY = f"{datetime.now().year}-01-05"


def get_stock_list():
    """获取全市场A股列表（分页获取）"""
    all_stocks = []
    for page in range(1, 80):
        url = 'https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData'
        params = {'page': page, 'num': 80, 'sort': 'symbol', 'asc': 1, 'node': 'hs_a', 'symbol': '', '_s_r_a': 'page'}
        try:
            resp = session.get(url, params=params, headers=HEADERS, timeout=15)
            data = resp.json()
            if not data:
                break
            all_stocks.extend(data)
        except:
            break
    
    # Filter: only SH/SZ main board, exclude ST/退市/科创板/北交所
    stocks = []
    for s in all_stocks:
        code = s['code']
        name = s.get('name', '')
        symbol = s['symbol']
        if not symbol.startswith(('sh6', 'sz0', 'sz3')):
            continue
        if code.startswith('688') or code.startswith('8') or code.startswith('4'):
            continue
        if 'ST' in name or '退' in name:
            continue
        stocks.append({'code': code, 'name': name, 'symbol': symbol})
    return stocks


def get_kline(code, symbol):
    """获取单只股票K线数据"""
    try:
        url = 'https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData'
        params = {'symbol': symbol, 'scale': 240, 'datalen': 250, 'ma': 'no'}
        resp = session.get(url, params=params, headers=HEADERS, timeout=10)
        data = resp.json()
        if data and len(data) >= 160:
            return code, data
    except:
        pass
    return code, None


def main():
    print("=" * 60)
    print("A股多头排列筛选器 (新浪财经API - 高效版)")
    print(f"筛选基准日: {TODAY}")
    print("=" * 60)
    
    # 1. 获取股票列表
    print("\n获取全市场A股列表...")
    stocks = get_stock_list()
    print(f"共 {len(stocks)} 只股票（排除ST/科创板/北交所）")
    
    # 2. 并行获取K线
    print(f"\n并行获取K线数据（20线程）...")
    code_map = {s['code']: s['name'] for s in stocks}
    all_kline = {}
    
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(get_kline, s['code'], s['symbol']): s['code'] for s in stocks}
        done = 0
        for future in as_completed(futures):
            done += 1
            if done % 500 == 0:
                print(f"  K线获取进度: {done}/{len(stocks)}")
            code, data = future.result()
            if data is not None:
                all_kline[code] = data
    
    print(f"  获取成功: {len(all_kline)}/{len(stocks)} 只")
    
    # 3. 计算均线并筛选
    print("\n计算均线并筛选...")
    results = []
    
    for code, kline_data in all_kline.items():
        df = pd.DataFrame(kline_data)
        df['close'] = pd.to_numeric(df['close'], errors='coerce')
        df['day'] = pd.to_datetime(df['day'], errors='coerce')
        df = df.dropna(subset=['close', 'day']).sort_values('day')
        
        if len(df) < 160:
            continue
        
        df['ma20'] = df['close'].rolling(20, min_periods=20).mean()
        df['ma40'] = df['close'].rolling(40, min_periods=40).mean()
        df['ma80'] = df['close'].rolling(80, min_periods=80).mean()
        df['ma160'] = df['close'].rolling(160, min_periods=160).mean()
        
        latest = df.iloc[-1]
        
        if (pd.notna(latest['ma20']) and pd.notna(latest['ma40']) and 
            pd.notna(latest['ma80']) and pd.notna(latest['ma160'])):
            if (latest['close'] > latest['ma20'] > latest['ma40'] > 
                latest['ma80'] > latest['ma160']):
                
                # 年内涨幅
                ytd_df = df[df['day'] >= pd.to_datetime(FIRST_DAY)]
                ytd_return = (latest['close'] / ytd_df.iloc[0]['close'] - 1) * 100 if len(ytd_df) >= 2 else None
                
                exchange = 'SH' if code.startswith(('6', '5')) else 'SZ'
                name = code_map.get(code, code)
                
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
    
    if not results:
        print("⚠️ 无满足条件的股票")
        return
    
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
    
    md_content += f"\n*数据来源: 新浪财经*\n"
    md_content += "*案例分析学习材料，不构成投资建议*\n"
    md_content += "*股市有风险，投资需谨慎*\n"
    
    # 保存
    output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "outputs", "股票筛选")
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, f"{TODAY}_多头排列Top20.md")
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(md_content)
    
    print(f"\n📁 已保存: {output_file}")
    print("✅ 任务完成！")


if __name__ == '__main__':
    main()
