#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全链路排查：期权数据 → 风险信号 → 飞书卡片
逐步检查每一步的数据流动
"""

import os
import sys
import json

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
ROOT_DIR = os.path.dirname(BASE_DIR)
sys.path.insert(0, ROOT_DIR)
sys.path.insert(0, SCRIPT_DIR)

# 导入风险日报模块
import risk_forecast_daily as rfd
from feishu_card_utils import build_risk_forecast_card

TRADE_DATE = '20260717'

print("=" * 70)
print("【步骤1】_fetch_option_from_datahub 返回结果")
print("=" * 70)

result, date = rfd._fetch_option_from_datahub(TRADE_DATE)
print(f"\n返回日期: {date}")
print(f"返回是否为None: {result is None}")

if result:
    print(f"\nresult.keys(): {list(result.keys())}")
    
    skew = result.get('skew', {})
    gex = result.get('gex', {})
    basis = result.get('basis', [])
    
    print(f"\nskew key数量: {len(skew)}")
    for k, v in skew.items():
        print(f"  - {k}: cboe_skew={v.get('cboe_skew')}, atm_iv={v.get('atm_iv')}")
    
    print(f"\ngex key数量: {len(gex)}")
    for k, v in gex.items():
        print(f"  - {k}: total_gex_yi={v.get('total_gex_yi')}, risk={v.get('risk')}")
    
    print(f"\nbasis 元素数量: {len(basis)}")
    for b in basis:
        print(f"  - {b.get('product')}: type={b.get('type')}, basis_rate={b.get('basis_rate')}")

print("\n" + "=" * 70)
print("【步骤2】load_option_data 返回结果")
print("=" * 70)

opt_result = rfd.load_option_data(TRADE_DATE, enable_fallback=False)
print(f"\n返回是否为None: {opt_result is None}")
if opt_result:
    data, actual_date = opt_result
    print(f"实际日期: {actual_date}")
    print(f"data.keys(): {list(data.keys())}")
    print(f"skew数量: {len(data.get('skew', {}))}")
    print(f"gex数量: {len(data.get('gex', {}))}")
    print(f"basis数量: {len(data.get('basis', []))}")
    
    # 检查数据来源
    for b in data.get('basis', []):
        print(f"  basis来源: {b.get('data_source', 'unknown')}")

print("\n" + "=" * 70)
print("【步骤3】build_index_signals 中期权数据消费")
print("=" * 70)

if opt_result:
    option_data = opt_result[0]
    indices = rfd.build_index_signals(option_data, None, None, None, None, None)
    
    print(f"\n所有指数: {list(indices.keys())}")
    print("\n各指数 derivatives_count:")
    for idx_name, idx_data in indices.items():
        dc = idx_data.get('derivatives_count', 0)
        ds = idx_data.get('dim_scores', {}).get('derivatives')
        factors = list(idx_data.get('factors', {}).keys())
        print(f"  {idx_name}: count={dc}, dim_score={ds}, factors={factors}")

print("\n" + "=" * 70)
print("【步骤4】build_risk_forecast_card 衍生品模块")
print("=" * 70)

if opt_result:
    option_data = opt_result[0]
    indices = rfd.build_index_signals(option_data, None, None, None, None, None)
    
    # 模拟卡片构建中的衍生品模块判断
    core_indices = ['上证50', '沪深300', '中证500', '中证1000', '创业板指', '科创50', '万得全A']
    
    deriv_indices = [i for i in core_indices if indices.get(i, {}).get('derivatives_count', 0) > 0]
    print(f"\nderiv_indices (有衍生品数据的指数): {deriv_indices}")
    print(f"数量: {len(deriv_indices)}")
    
    # 检查万得全A的dim_scores
    wq = indices.get('万得全A', {})
    print(f"\n万得全A dim_scores: {wq.get('dim_scores', {})}")
    print(f"万得全A derivatives_count: {wq.get('derivatives_count', 0)}")

print("\n" + "=" * 70)
print("【排查完成】")
print("=" * 70)
