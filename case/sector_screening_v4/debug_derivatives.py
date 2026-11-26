#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""衍生品模块NA问题排查脚本"""

import os
import sys
import json

# 设置路径
SCRIPT_DIR = '/app/data/所有对话/主对话/市场情绪监控/scripts'
BASE_DIR = os.path.dirname(SCRIPT_DIR)
ROOT_DIR = os.path.dirname(BASE_DIR)
sys.path.insert(0, ROOT_DIR)
sys.path.insert(0, SCRIPT_DIR)

# 导入脚本中的函数
from risk_forecast_daily import (
    load_option_data, build_index_signals,
    load_cfc_data, load_structural_factor_data,
    load_market_monitor_summary, load_volume_percentile_data,
    load_lppl_data,
    OPT_TO_INDEX, BASIS_TO_INDEX, CORE_INDICES, ALL_INDICES,
)

TEST_DATE = '20260715'  # 有完整数据的日期

print("=" * 70)
print("步骤1：期权数据获取验证")
print("=" * 70)

option_data, opt_date = load_option_data(TEST_DATE, enable_fallback=True)
print("\n=== 期权数据整体 ===")
print(f"type: {type(option_data)}")
print(f"keys: {option_data.keys() if option_data else 'None'}")
print(f"skew keys: {list(option_data['skew'].keys()) if option_data and 'skew' in option_data else 'None'}")
print(f"gex keys: {list(option_data['gex'].keys()) if option_data and 'gex' in option_data else 'None'}")

print("\n--- skew 详情 ---")
for k, v in (option_data.get('skew', {}) if option_data else {}).items():
    print(f"  skew[{k}]: type={type(v)}, value={'NOT None' if v else 'None'}")
    if v:
        print(f"    cboe_skew={v.get('cboe_skew')}, direction={v.get('direction')}")

print("\n--- gex 详情 ---")
for k, v in (option_data.get('gex', {}) if option_data else {}).items():
    print(f"  gex[{k}]: type={type(v)}, value={'NOT None' if v else 'None'}")
    if v:
        print(f"    total_gex_yi={v.get('total_gex_yi')}, risk={v.get('risk')}, risk_label={v.get('risk_label')}")

print(f"\nbasis count: {len(option_data['basis']) if option_data and 'basis' in option_data else 0}")
print("\n--- basis 详情 ---")
for b in (option_data.get('basis', []) if option_data else []):
    print(f"  basis: product={b.get('product')}, type={b.get('type')}, name={b.get('name')}")
    print(f"         basis_rate={b.get('basis_rate')}, basis_rate_annual={b.get('basis_rate_annual')}")
    print(f"         rank_252={b.get('rank_252')}, signal={b.get('signal')}")

print("\n" + "=" * 70)
print("步骤2：OPT_TO_INDEX 映射验证")
print("=" * 70)
print(f"OPT_TO_INDEX: {OPT_TO_INDEX}")
print(f"BASIS_TO_INDEX: {BASIS_TO_INDEX}")

print("\n" + "=" * 70)
print("步骤3：build_index_signals 验证（仅期权数据）")
print("=" * 70)

# 只传期权数据，其他传 None
indices = build_index_signals(option_data, None, None, None, None, None)

print(f"\nALL_INDICES: {ALL_INDICES}")
print(f"CORE_INDICES: {CORE_INDICES}")

print("\n--- 各指数 derivatives_count 和 factors ---")
for idx_name in ALL_INDICES:
    idx = indices.get(idx_name, {})
    d_count = idx.get('derivatives_count', 0)
    factors = idx.get('factors', {})
    factor_keys = list(factors.keys())
    print(f"\n{idx_name}:")
    print(f"  derivatives_count: {d_count}")
    print(f"  factors keys: {factor_keys}")
    for fk in factor_keys:
        fv = factors[fk]
        print(f"    {fk}: value={fv.get('value')}, label={fv.get('label')}, score={fv.get('score')}")

print("\n" + "=" * 70)
print("步骤4：完整数据 build_index_signals 验证")
print("=" * 70)

# 加载所有数据
print("\n加载CFC数据...")
cfc_result = load_cfc_data(TEST_DATE, enable_fallback=True)
cfc_data = cfc_result[0] if cfc_result else None
print(f"  CFC数据: {'有' if cfc_data else '无'}")

print("\n加载结构因子数据...")
sf_result = load_structural_factor_data(TEST_DATE, enable_fallback=True)
sf_data = sf_result[0] if sf_result else None
print(f"  结构因子数据: {'有' if sf_data else '无'}")

print("\n加载市场监控数据...")
mm_result = load_market_monitor_summary(TEST_DATE)
market_data = mm_result[0] if mm_result else None
print(f"  市场监控数据: {'有' if market_data else '无'}")

print("\n加载成交量分位数据...")
try:
    vp_result = load_volume_percentile_data(TEST_DATE)
    vol_pct_data = vp_result[0] if vp_result else None
except Exception as e:
    print(f"  加载失败: {e}")
    vol_pct_data = None
print(f"  成交量分位数据: {'有' if vol_pct_data else '无'}")

print("\n加载LPPL数据...")
try:
    lppl_result = load_lppl_data(TEST_DATE, '000001.SH')
    lppl_data = lppl_result[0] if lppl_result else None
except Exception as e:
    print(f"  加载失败: {e}")
    lppl_data = None
print(f"  LPPL数据: {'有' if lppl_data else '无'}")

# 完整构建
indices_full = build_index_signals(option_data, cfc_data, sf_data, market_data, vol_pct_data, lppl_data)

print("\n--- 各指数完整状态 ---")
for idx_name in ALL_INDICES:
    idx = indices_full.get(idx_name, {})
    d_count = idx.get('derivatives_count', 0)
    s_count = idx.get('structural_count', 0)
    sent_count = idx.get('sentiment_count', 0)
    e_count = idx.get('extreme_count', 0)
    factors = idx.get('factors', {})
    factor_keys = list(factors.keys())
    print(f"\n{idx_name}:")
    print(f"  derivatives_count: {d_count}, structural_count: {s_count}, sentiment_count: {sent_count}, extreme_count: {e_count}")
    print(f"  factors keys: {factor_keys}")
    # 特别检查衍生品相关因子
    for fk in ['SKEW', 'GEX', '基差']:
        if fk in factors:
            fv = factors[fk]
            print(f"    {fk}: value={fv.get('value')}, label={fv.get('label')}")
        else:
            print(f"    {fk}: ❌ 缺失！")

print("\n" + "=" * 70)
print("步骤5：验证卡片生成 - 衍生品模块")
print("=" * 70)

from feishu_card_utils import build_risk_forecast_card
from risk_forecast_daily import generate_conclusion, collect_risk_signals, collect_opportunity_signals

wanquan = indices_full.get('万得全A', {})
top_risks = collect_risk_signals(indices_full)
top_opps = collect_opportunity_signals(indices_full)
conclusion = generate_conclusion(wanquan, top_risks, top_opps)

card = build_risk_forecast_card(
    '2026-07-15',
    indices=indices_full,
    conclusion=conclusion,
    top_risks=top_risks[:5],
    top_opps=top_opps[:5],
    style_drift=None,
    is_midday=False,
)

# 查找衍生品模块的内容
print("\n--- 搜索卡片中的衍生品内容 ---")
def find_derivatives_content(elements, depth=0):
    for i, elem in enumerate(elements):
        if isinstance(elem, dict):
            text = elem.get('text', {})
            if isinstance(text, dict):
                content = text.get('content', '')
                if '衍生品' in content or 'SKEW' in content or 'GEX' in content or '基差' in content:
                    print(f"\n[元素{i}, 深度{depth}] 找到衍生品相关内容:")
                    # 打印前2000字符
                    print(content[:2000])
                    if len(content) > 2000:
                        print(f"... (共{len(content)}字符)")
            # 递归搜索子元素
            for key in ['elements', 'columns', 'body']:
                if key in elem and isinstance(elem[key], list):
                    find_derivatives_content(elem[key], depth+1)

find_derivatives_content(card.get('elements', []))

print("\n" + "=" * 70)
print("排查完成")
print("=" * 70)
