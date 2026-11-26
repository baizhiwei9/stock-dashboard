#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""调试脚本：完整测试卡片构建，检查衍生品模块数据"""

# ── 去云盘化：/Coze/Drive、/opt/coze/.fuse_drive、/app/data 三个旧根可重定向 ──
# 换机器时设 CASE_DRIVE_ROOT / CASE_FUSE_ROOT / CASE_APP_DATA，未设则原样返回。
def _cloud_translate(_f=__file__):
    import os as _o
    import sys as _s
    _d = _o.path.dirname(_o.path.abspath(_f))
    for _ in range(6):
        _c = _o.path.join(_d, "_lib")
        if _o.path.isfile(_o.path.join(_c, "cloud_paths.py")):
            if _c not in _s.path:
                _s.path.insert(0, _c)
            from cloud_paths import translate as _t
            return _t
        _d = _o.path.dirname(_d)
    return lambda p: p


_cp = _cloud_translate()


import sys
import os
import json

SCRIPT_DIR = _cp('/app/data/所有对话/主对话/市场情绪监控/scripts')
sys.path.insert(0, SCRIPT_DIR)
ROOT_DIR = _cp('/app/data/所有对话/主对话')
sys.path.insert(0, ROOT_DIR)

from risk_forecast_daily import (
    load_option_data,
    load_cfc_data,
    load_structural_factor_data,
    load_market_monitor_summary,
    load_volume_percentile_data,
    load_lppl_data,
    load_style_drift_data,
    build_index_signals,
    collect_risk_signals,
    collect_opportunity_signals,
    generate_conclusion,
)

from feishu_card_utils import build_risk_forecast_card

trade_date = '20260717'
date_display = '2026-07-17'

print("=" * 60)
print(f"完整卡片构建测试 ({trade_date})")
print("=" * 60)

# 加载所有数据
print("\n📥 加载数据...")
opt_result = load_option_data(trade_date)
option_data = opt_result[0] if opt_result else None

cfc_result = load_cfc_data(trade_date)
cfc_data = cfc_result[0] if cfc_result else None

sf_result = load_structural_factor_data(trade_date)
sf_data = sf_result[0] if sf_result else None

mm_result = load_market_monitor_summary(trade_date)
market_data = mm_result[0] if mm_result else None

vp_result = load_volume_percentile_data(trade_date)
vol_pct_data = vp_result[0] if vp_result else None

lppl_result = load_lppl_data(trade_date, '000001.SH')
lppl_data = lppl_result[0] if lppl_result else None

sd_result = load_style_drift_data(trade_date)
style_drift = sd_result[0] if sd_result else None

# 构建指数信号
print("\n🧮 构建指数信号...")
indices = build_index_signals(option_data, cfc_data, sf_data, market_data, vol_pct_data, lppl_data)

if style_drift:
    indices['风格漂移'] = style_drift

top_risks = collect_risk_signals(indices)
top_opps = collect_opportunity_signals(indices)
wanquan = indices.get('万得全A', {})
conclusion = generate_conclusion(wanquan, top_risks, top_opps)

print(f"\n📊 衍生品有数据的指数:")
for name in ['上证50', '沪深300', '中证500', '中证1000', '创业板指', '科创50', '万得全A']:
    idx = indices.get(name, {})
    dc = idx.get('derivatives_count', 0)
    factors = idx.get('factors', {})
    print(f"  {name}: derivatives_count={dc}, factors keys={list(factors.keys())[:5]}...")

# 构建卡片
print("\n🎴 构建飞书卡片...")
card = build_risk_forecast_card(
    date_display,
    indices=indices,
    conclusion=conclusion,
    top_risks=top_risks[:5],
    top_opps=top_opps[:5],
    style_drift=style_drift,
    is_midday=False,
)

# 查找衍生品模块
print("\n" + "=" * 60)
print("卡片中衍生品模块内容")
print("=" * 60)

elements = card.get('elements', [])
found_deriv = False
for i, elem in enumerate(elements):
    text = elem.get('text', {})
    content = text.get('content', '') if isinstance(text, dict) else ''
    # 查找衍生品相关内容
    if '衍生品' in content or 'SKEW' in content or 'GEX' in content or '基差' in content:
        found_deriv = True
        print(f"\n--- 元素 {i} ---")
        print(f"tag: {elem.get('tag')}")
        if isinstance(text, dict) and content:
            print(f"content:\n{content}")

if not found_deriv:
    print("\n❌ 未找到衍生品模块！")
    # 打印所有元素的前50字符
    print("\n所有元素概览:")
    for i, elem in enumerate(elements):
        tag = elem.get('tag')
        text = elem.get('text', {})
        if isinstance(text, dict):
            content = text.get('content', '')[:80]
        else:
            content = str(text)[:80]
        print(f"  [{i}] {tag}: {content}")

# 特别检查"模块一：衍生品定价风险"
print("\n" + "=" * 60)
print("检查 '模块一：衍生品定价风险' 内容")
print("=" * 60)
for i, elem in enumerate(elements):
    text = elem.get('text', {})
    content = text.get('content', '') if isinstance(text, dict) else ''
    if '模块一' in content and '衍生品' in content:
        print(f"\n✅ 找到衍生品模块（元素 {i}）:")
        print(f"--- 完整内容 ---")
        print(content)
        print("--- 结束 ---")
        
        # 分析表格数据
        lines = content.split('\n')
        print(f"\n行数: {len(lines)}")
        for j, line in enumerate(lines):
            if '|' in line and ('指数' in line or '---' in line or '上证' in line or '沪深' in line or '中证' in line or '创业' in line or '科创' in line):
                print(f"  行{j}: {line}")

print("\n" + "=" * 60)
print("调试完毕")
print("=" * 60)
