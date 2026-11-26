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
    print("=" * 60)
    print("A股多头排列筛选器 (efinance版)")
    print("=" * 60)
    
    TODAY = datetime.now().strftime('%Y-%m-%d')
    FIRST_DAY = f"{datetime.now().year}-01-01"
    
    print(f"筛选基准日: {TODAY}")
    
    # 1. 获取全市场A股列表
    print("\n获取全市场A股列表...")
    try:
        stock_df = ef.stock.get_realtime_quotes()
        print(f"获取到 {len(stock_df)} 只股票")
        print(f"列名: {stock_df.columns.tolist()[:10]}...")
    except Exception as e:
        print(f"获取股票列表失败: {e}")
        return
    
    # 过滤股票
    stock_df = stock_df[stock_df['股票代码'].str.match(r'^\d{6}$')]
    stock_df = stock_df[~stock_df['股票名称'].str.contains('ST|退|N', na=False)]
    stock_df = stock_df[~stock_df['股票代码'].str.startswith('688')]
    stock_df = stock_df[~stock_df['股票代码'].str.startswith('8')]
    stock_df = stock_df[~stock_df['股票代码'].str.startswith('4')]
    
    codes = stock_df['股票代码'].tolist()
    print(f"过滤后剩余 {len(codes)} 只")
    
    if not codes:
        print("没有符合条件的股票")
        return
    
    # 2. 获取日线数据
    print(f"\n获取日线数据...")
    try:
        daily_df = ef.stock.get_daily_history(codes[:100], fstart_date='2024-10-01', fend_date=TODAY)
        if isinstance(daily_df, dict):
            # 单只股票返回Series，转为DataFrame
            daily_df = pd.DataFrame(daily_df)
        print(f"获取到 {len(daily_df)} 条数据")
    except Exception as e:
        print(f"获取日线数据失败: {e}")
        return
    
    print(f"\n数据形状: {daily_df.shape}")
    print(f"列名: {daily_df.columns.tolist()}")
    print(daily_df.head())


if __name__ == '__main__':
    main()
