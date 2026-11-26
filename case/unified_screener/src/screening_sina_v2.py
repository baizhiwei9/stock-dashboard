#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股多头排列筛选器 - 新浪财经API版
案例分析学习材料，不构成投资建议
筛选条件：股价 > MA20 > MA40 > MA80 > MA160
"""

import requests
import json
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import warnings
warnings.filterwarnings('ignore')


def get_stock_list_sina():
    """从新浪获取全市场A股列表"""
    stocks = []
    for page in range(1, 20):  # 最多20页，每页1000只
        try:
            url = f'http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeDataSimple?page={page}&num=1000&sort=symbol&asc=1&node=hs_a&symbol=&_s_r_a=page'
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = json.loads(resp.text)
                if not data:
                    break
                stocks.extend(data)
            else:
                break
        except Exception as e:
            print(f"获取股票列表第{page}页失败: {e}")
            break
    return stocks


def get_kline_data(code, prefix='sh'):
    """获取单只股票K线数据"""
    try:
        # 转换代码格式
        if not code.startswith(('sh', 'sz')):
            if code.startswith('6'):
                code = f'sh{code}'
            else:
                code = f'sz{code}'
        
        url = 'http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData'
        params = {
            'symbol': code,
            'scale': 240,  # 日K
            'ma': 'no',
            'datalen': 200  # 约一年的交易日数据
        }
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            data = json.loads(resp.text)
            if data:
                df = pd.DataFrame(data)
                df['code'] = code.replace('sh', '').replace('sz', '')
                return df
        return None
    except Exception as e:
        return None


def calculate_ma_and_filter(df, code, name, year_start_price):
    """计算均线并检查多头排列"""
    try:
        if len(df) < 160:
            return None
        
        df['close'] = df['close'].astype(float)
        df['ma20'] = df['close'].rolling(window=20, min_periods=20).mean()
        df['ma40'] = df['close'].rolling(window=40, min_periods=40).mean()
        df['ma80'] = df['close'].rolling(window=80, min_periods=80).mean()
        df['ma160'] = df['close'].rolling(window=160, min_periods=160).mean()
        
        latest = df.iloc[-1]
        
        # 检查多头排列条件
        if (pd.notna(latest['ma20']) and pd.notna(latest['ma40']) and 
            pd.notna(latest['ma80']) and pd.notna(latest['ma160'])):
            if (latest['close'] > latest['ma20'] > latest['ma40'] > 
                latest['ma80'] > latest['ma160']):
                # 计算年内涨幅
                if year_start_price and year_start_price > 0:
                    ytd_return = (latest['close'] / year_start_price - 1) * 100
                else:
                    ytd_return = None
                
                return {
                    '股票代码': code.replace('sh', '').replace('sz', ''),
                    '交易所': 'SH' if code.startswith('sh') else 'SZ',
                    '股票简称': name,
                    '当前价': round(float(latest['close']), 2),
                    'MA20': round(float(latest['ma20']), 2),
                    'MA40': round(float(latest['ma40']), 2),
                    'MA80': round(float(latest['ma80']), 2),
                    'MA160': round(float(latest['ma160']), 2),
                    '年内涨幅': round(ytd_return, 2) if ytd_return is not None else None
                }
        return None
    except Exception as e:
        return None


def main():
    print("=" * 60)
    print("A股多头排列筛选器 (新浪财经API版)")
    print("=" * 60)
    
    TODAY = datetime.now().strftime('%Y-%m-%d')
    FIRST_DAY = f"{datetime.now().year}-01-05"
    
    print(f"筛选基准日: {TODAY}")
    
    # 1. 获取全市场A股列表
    print("\n获取全市场A股列表...")
    stock_list = get_stock_list_sina()
    print(f"获取到 {len(stock_list)} 只股票")
    
    if not stock_list:
        print("获取股票列表失败")
        return
    
    # 过滤和整理
    stocks_info = {}
    codes = []
    for stock in stock_list:
        code = stock.get('symbol', '')
        name = stock.get('name', '')
        # 排除ST、北交所、科创板
        if any(x in name for x in ['ST', '退']):
            continue
        if code.startswith(('bj', 'bj8', 'bj4', 'sh688')):
            continue
        if code.startswith(('sh', 'sz')):
            codes.append(code)
            stocks_info[code] = name
    
    print(f"过滤后剩余 {len(codes)} 只股票")
    
    if not codes:
        print("没有符合条件的股票")
        return
    
    # 2. 批量获取K线数据
    print(f"\n批量获取K线数据（使用10线程）...")
    all_klines = {}
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(get_kline_data, code): code for code in codes}
        
        completed = 0
        for future in as_completed(futures):
            completed += 1
            if completed % 500 == 0:
                print(f"进度: {completed}/{len(codes)}")
            
            result = future.result()
            if result is not None and len(result) >= 160:
                code = result['code'].iloc[0] if hasattr(result['code'], 'iloc') else result['code']
                all_klines[code] = result
    
    print(f"获取完成: {len(all_klines)} 只股票有足够数据")
    
    if not all_klines:
        print("无K线数据")
        return
    
    # 3. 获取年内开盘价用于计算涨幅
    print("\n获取年内开盘价...")
    year_codes = list(all_klines.keys())
    year_start_prices = {}
    
    # 获取2026年开盘价
    for code in year_codes:
        df = all_klines[code]
        if 'day' in df.columns:
            df_2026 = df[df['day'] >= f'{datetime.now().year}-01-01']
            if len(df_2026) > 0:
                year_start_prices[code] = float(df_2026.iloc[0]['close'])
    
    # 4. 计算均线并筛选
    print("\n计算均线并筛选...")
    results = []
    
    for idx, (code, df) in enumerate(all_klines.items()):
        name = stocks_info.get(f'sh{code}') or stocks_info.get(f'sz{code}') or code
        year_start = year_start_prices.get(code)
        
        result = calculate_ma_and_filter(df, code, name, year_start)
        if result:
            results.append(result)
        
        if (idx + 1) % 1000 == 0:
            print(f"筛选进度: {idx + 1}/{len(all_klines)} (已发现{len(results)}只)")
    
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
        
        md_content += f"\n*数据来源: 新浪财经*\n"
        md_content += "*案例分析学习材料，不构成投资建议*\n"
        
        # 保存报告
        output_dir = f"outputs/股票筛选"
        os.makedirs(output_dir, exist_ok=True)
        output_file = f"{output_dir}/{TODAY}_多头排列Top20.md"
        
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(md_content)
        
        print(f"\n✅ 报告已保存到: {output_file}")
        
        # 飞书推送（交互式卡片）
        try:
            import sys as _sys
            _sys.path.insert(0, '/Coze/Drive/蓁宝/所有对话/主对话')
            from feishu_card_utils import build_chanlun_card
            from feishu_push import push_card_to_feishu
            
            # 构建股票数据
            stocks_data = []
            for _, row in top20.iterrows():
                stocks_data.append({
                    'name': row.get('股票简称', ''),
                    'code': row.get('股票代码', ''),
                    'ytd_return': row.get('年内涨幅'),
                    'industry': '-',
                })
            
            card = build_chanlun_card(
                TODAY,
                stocks_data,
                total_count=len(results_df),
                details={
                    '筛选条件': '股价>MA20>MA40>MA80>MA160（多头排列）',
                    '数据源': '新浪财经API',
                }
            )
            push_card_to_feishu(card_dict=card)
        except Exception as e:
            print(f"⚠️ 飞书卡片推送失败: {e}")
            import traceback; traceback.print_exc()
        
        return output_file
    else:
        print("⚠️ 无满足条件的股票")
        return None


if __name__ == "__main__":
    import os
    main()
