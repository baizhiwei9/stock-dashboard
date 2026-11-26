#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""调试脚本：检查 load_option_data 返回的数据结构"""

import sys
import os
import json

SCRIPT_DIR = '/app/data/所有对话/主对话/市场情绪监控/scripts'
sys.path.insert(0, SCRIPT_DIR)

from risk_forecast_daily import load_option_data

trade_date = '20260717'

print("=" * 60)
print(f"调用 load_option_data('{trade_date}')")
print("=" * 60)

result = load_option_data(trade_date)

print(f"\n返回值类型: {type(result)}")
print(f"返回值长度: {len(result) if result else 'None'}")

if result:
    option_data = result[0]
    actual_date = result[1] if len(result) > 1 else None
    print(f"实际数据日期: {actual_date}")
    print(f"\noption_data 类型: {type(option_data)}")
    
    if option_data and isinstance(option_data, dict):
        print(f"option_data keys: {list(option_data.keys())}")
        
        # --- skew ---
        print("\n" + "=" * 60)
        print("SKEW 数据")
        print("=" * 60)
        skew = option_data.get('skew', {})
        print(f"skew 类型: {type(skew)}")
        print(f"skew keys: {list(skew.keys()) if isinstance(skew, dict) else 'N/A'}")
        for k, v in skew.items():
            print(f"  {k}: {type(v)}")
            if v and isinstance(v, dict):
                print(f"    keys: {list(v.keys())}")
                for kk, vv in v.items():
                    print(f"    {kk}: {vv}")
        
        # --- gex ---
        print("\n" + "=" * 60)
        print("GEX 数据")
        print("=" * 60)
        gex = option_data.get('gex', {})
        print(f"gex 类型: {type(gex)}")
        print(f"gex keys: {list(gex.keys()) if isinstance(gex, dict) else 'N/A'}")
        for k, v in gex.items():
            print(f"  {k}: {type(v)}")
            if v and isinstance(v, dict):
                print(f"    keys: {list(v.keys())}")
                for kk, vv in v.items():
                    print(f"    {kk}: {vv}")
        
        # --- basis ---
        print("\n" + "=" * 60)
        print("基差数据")
        print("=" * 60)
        basis = option_data.get('basis', [])
        print(f"basis 类型: {type(basis)}")
        print(f"basis 长度: {len(basis) if isinstance(basis, list) else 'N/A'}")
        for i, b in enumerate(basis):
            print(f"\n  [{i}] type={type(b)}")
            if isinstance(b, dict):
                for kk, vv in b.items():
                    print(f"    {kk}: {vv}")
    else:
        print(f"option_data 内容: {option_data}")
else:
    print("返回值为 None 或空")

print("\n" + "=" * 60)
print("调试完毕")
print("=" * 60)
