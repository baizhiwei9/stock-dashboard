#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
验证卡片修复：衍生品模块是否正常显示
"""

import os
import sys
import json

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
ROOT_DIR = os.path.dirname(BASE_DIR)
sys.path.insert(0, ROOT_DIR)
sys.path.insert(0, SCRIPT_DIR)

import risk_forecast_daily as rfd
from feishu_card_utils import build_risk_forecast_card

TRADE_DATE = '20260717'
date_display = f"{TRADE_DATE[:4]}-{TRADE_DATE[4:6]}-{TRADE_DATE[6:]}"

print("=" * 70)
print("验证：构建风险日报卡片，检查衍生品模块")
print("=" * 70)

# 加载期权数据
print("\n📥 加载期权数据...")
opt_result = rfd.load_option_data(TRADE_DATE, enable_fallback=False)
option_data = opt_result[0] if opt_result else None

# 加载CFC数据（用于结构模块验证）
cfc_result = rfd.load_cfc_data(TRADE_DATE, enable_fallback=True)
cfc_data = cfc_result[0] if cfc_result else None

# 加载结构因子数据
sf_result = rfd.load_structural_factor_data(TRADE_DATE, enable_fallback=True)
sf_data = sf_result[0] if sf_result else None

# 构建信号
print("\n🧮 构建风险信号...")
indices = rfd.build_index_signals(option_data, cfc_data, sf_data, None, None, None)

top_risks = rfd.collect_risk_signals(indices)
top_opps = rfd.collect_opportunity_signals(indices)
wanquan = indices.get('万得全A', {})
conclusion = rfd.generate_conclusion(wanquan, top_risks, top_opps)

# 构建卡片
print("\n🎨 构建飞书卡片...")
card = build_risk_forecast_card(
    date_display,
    indices=indices,
    conclusion=conclusion,
    top_risks=top_risks[:5],
    top_opps=top_opps[:5],
    style_drift=None,
    is_midday=False,
)

# 分析卡片元素
elements = card.get('elements', [])
print(f"\n📊 卡片元素总数: {len(elements)}")

# 查找衍生品模块
deriv_found = False
struct_found = False
sent_found = False
extreme_found = False

for i, elem in enumerate(elements):
    if elem.get('tag') == 'div' and elem.get('text', {}).get('tag') == 'lark_md':
        content = elem['text'].get('content', '')
        if '模块一：衍生品' in content:
            deriv_found = True
            print(f"\n✅ 找到衍生品模块 (元素#{i})")
            # 检查是否有表格
            if '| 指数 | SKEW | GEX | 基差 |' in content:
                print(f"   ✅ 包含SKEW/GEX/基差表格")
            else:
                print(f"   ⚠️  表格内容可能有问题")
            # 打印前500字符预览
            preview = content[:500].replace('\n', '\\n')
            print(f"   预览: {preview}")
        
        if '模块二：结构' in content:
            struct_found = True
            print(f"\n✅ 找到结构风险模块 (元素#{i})")
        
        if '模块三：量能情绪' in content:
            sent_found = True
            print(f"\n✅ 找到量能情绪模块 (元素#{i})")
        
        if '模块四：极端风险' in content:
            extreme_found = True
            print(f"\n✅ 找到极端风险模块 (元素#{i})")

print(f"\n{'='*70}")
print("【验证结果】")
print(f"{'='*70}")
print(f"  衍生品模块: {'✅ 正常' if deriv_found else '❌ 缺失'}")
print(f"  结构风险模块: {'✅ 正常' if struct_found else '❌ 缺失'}")
print(f"  量能情绪模块: {'✅ 正常' if sent_found else '⚪ 无数据（正常）'}")
print(f"  极端风险模块: {'✅ 正常' if extreme_found else '⚪ 无数据（正常）'}")

# 检查衍生品数据详情
if option_data:
    skew = option_data.get('skew', {})
    gex = option_data.get('gex', {})
    basis = option_data.get('basis', [])
    print(f"\n📈 期权数据详情:")
    print(f"  SKEW 标的数: {len(skew)}")
    for k, v in skew.items():
        print(f"    - {k}: SKEW={v.get('cboe_skew')}")
    print(f"  GEX 标的数: {len(gex)}")
    for k, v in gex.items():
        print(f"    - {k}: GEX={v.get('total_gex_yi')}亿")
    print(f"  基差品种数: {len(basis)}")
    for b in basis:
        print(f"    - {b.get('product')}: 基差率={b.get('basis_rate')}%")

# 检查各指数衍生品计数
print(f"\n📊 各指数衍生品因子计数:")
for idx_name in ['上证50', '沪深300', '中证500', '中证1000', '创业板指', '科创50', '万得全A']:
    idx = indices.get(idx_name, {})
    dc = idx.get('derivatives_count', 0)
    factors = [k for k in idx.get('factors', {}).keys() if k in ['SKEW', 'GEX', '基差']]
    if dc > 0:
        print(f"  ✅ {idx_name}: count={dc}, factors={factors}")
    else:
        print(f"  ⚪ {idx_name}: count={dc}")

print(f"\n{'='*70}")
if deriv_found and len(option_data.get('skew', {})) > 0:
    print("🎉 修复成功！衍生品模块正常显示")
else:
    print("❌ 仍有问题，需进一步排查")
