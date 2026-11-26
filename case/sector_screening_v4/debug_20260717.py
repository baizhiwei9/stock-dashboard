#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试20260717日期的衍生品数据情况"""

import os
import sys

SCRIPT_DIR = '/app/data/所有对话/主对话/市场情绪监控/scripts'
BASE_DIR = os.path.dirname(SCRIPT_DIR)
ROOT_DIR = os.path.dirname(BASE_DIR)
sys.path.insert(0, ROOT_DIR)
sys.path.insert(0, SCRIPT_DIR)

from risk_forecast_daily import (
    load_option_data, build_index_signals,
    OPT_TO_INDEX, BASIS_TO_INDEX, CORE_INDICES, ALL_INDICES,
)

TEST_DATE = '20260717'

print("=" * 70)
print(f"测试日期: {TEST_DATE}")
print("=" * 70)

# 先看不启用回退的情况
print("\n--- 不启用回退 (默认模式) ---")
option_data, opt_date = load_option_data(TEST_DATE, enable_fallback=False)
print(f"option_data: {option_data}")
print(f"opt_date: {opt_date}")

if option_data is None:
    print("  → option_data 是 None！衍生品模块将完全不显示")

# 启用回退
print("\n--- 启用回退 ---")
option_data2, opt_date2 = load_option_data(TEST_DATE, enable_fallback=True)
print(f"option_data type: {type(option_data2) if option_data2 else 'None'}")
print(f"opt_date: {opt_date2}")

if option_data2:
    skew = option_data2.get('skew', {})
    gex = option_data2.get('gex', {})
    basis = option_data2.get('basis', [])
    print(f"\nskew keys: {list(skew.keys())}")
    for k, v in skew.items():
        print(f"  skew[{k}]: {'NOT None' if v else 'None'}")
    print(f"\nbasis count: {len(basis)}")

print("\n" + "=" * 70)
print("检查收盘模式下 load_option_data 的调用方式")
print("=" * 70)
# 看 run_closing_mode 中的调用是否带 enable_fallback
import inspect
from risk_forecast_daily import run_closing_mode
source = inspect.getsource(run_closing_mode)
# 查找 load_option_data 的调用
import re
matches = re.findall(r'load_option_data\([^)]+\)', source)
for m in matches:
    print(f"  {m}")

print("\n" + "=" * 70)
print("检查 detect_midday_mode 和 run_midday_mode")
print("=" * 70)

from risk_forecast_daily import detect_midday_mode
is_midday = detect_midday_mode(TEST_DATE)
print(f"detect_midday_mode({TEST_DATE}) = {is_midday}")
