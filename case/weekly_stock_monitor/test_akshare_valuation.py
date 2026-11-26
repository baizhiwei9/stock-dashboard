#!/usr/bin/env python3
"""
测试 akshare 估值接口获取创业板指 PE
"""
import akshare as ak
import pandas as pd
from datetime import datetime, timedelta

def test_valuation_interfaces():
    """测试多个估值接口"""
    
    print("=" * 60)
    print("测试 akshare 估值接口")
    print("=" * 60)
    
    # 1. stock_zh_valuation_baidu
    print("\n【1】stock_zh_valuation_baidu")
    try:
        # 尝试获取创业板估值
        df = ak.stock_zh_valuation_baidu(symbol="创业板", indicator="总市值", period="近一年")
        print(f"  ✓ 成功，获取 {len(df)} 条数据")
        print(f"  列名: {df.columns.tolist()}")
        print(f"  日期范围: {df['日期'].min()} ~ {df['日期'].max()}")
        print(df.tail(3))
    except Exception as e:
        print(f"  ✗ 失败: {e}")
    
    # 2. stock_zh_index_value_csindex
    print("\n【2】stock_zh_index_value_csindex")
    try:
        # 获取中证指数估值
        # symbol 格式可能是指数代码，如 000300(沪深300)
        df = ak.stock_zh_index_value_csindex(symbol="000300")
        print(f"  ✓ 成功，获取 {len(df)} 条数据")
        print(f"  列名: {df.columns.tolist()}")
        print(df.head(3))
        print(df.tail(3))
    except Exception as e:
        print(f"  ✗ 失败: {e}")
    
    # 尝试创业板指数代码
    print("\n【3】stock_zh_index_value_csindex - 创业板指")
    try:
        df = ak.stock_zh_index_value_csindex(symbol="399006")
        print(f"  ✓ 成功，获取 {len(df)} 条数据")
        print(f"  列名: {df.columns.tolist()}")
        print(df.head(3))
    except Exception as e:
        print(f"  ✗ 失败: {e}")
    
    # 尝试科创50
    print("\n【4】stock_zh_index_value_csindex - 科创50")
    try:
        df = ak.stock_zh_index_value_csindex(symbol="000688")
        print(f"  ✓ 成功，获取 {len(df)} 条数据")
        print(f"  列名: {df.columns.tolist()}")
        print(df.head(3))
    except Exception as e:
        print(f"  ✗ 失败: {e}")
    
    # 3. stock_market_pe_lg
    print("\n【5】stock_market_pe_lg")
    try:
        df = ak.stock_market_pe_lg(symbol="深证A股")
        print(f"  ✓ 成功，获取 {len(df)} 条数据")
        print(f"  列名: {df.columns.tolist()}")
        print(df.tail(3))
    except Exception as e:
        print(f"  ✗ 失败: {e}")

if __name__ == "__main__":
    test_valuation_interfaces()
