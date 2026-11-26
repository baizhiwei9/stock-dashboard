#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""完整测试20260717衍生品模块"""

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


import os
import sys

SCRIPT_DIR = _cp('/app/data/所有对话/主对话/市场情绪监控/scripts')
BASE_DIR = os.path.dirname(SCRIPT_DIR)
ROOT_DIR = os.path.dirname(BASE_DIR)
sys.path.insert(0, ROOT_DIR)
sys.path.insert(0, SCRIPT_DIR)

from risk_forecast_daily import *

TEST_DATE = '20260717'

print("=" * 70)
print(f"完整测试 {TEST_DATE} - 衍生品模块")
print("=" * 70)

# 加载期权数据
print("\n1. 加载期权数据...")
option_result = load_option_data(TEST_DATE, enable_fallback=False)
option_data = option_result[0] if option_result else None
option_date = option_result[1] if option_result else None
print(f"   option_data: {'有' if option_data else '无'}")
print(f"   option_date: {option_date}")

# 检查 data_hub 返回的 gex 数据结构
if option_data:
    gex = option_data.get('gex', {})
    for k, v in gex.items():
        if v:
            print(f"   gex[{k}] keys: {list(v.keys())}")
            print(f"     risk={v.get('risk')}, risk_label={v.get('risk_label')}")
            print(f"     total_gex_yi={v.get('total_gex_yi')}")

    basis = option_data.get('basis', [])
    for b in basis:
        print(f"   basis: product={b.get('product')}, type={b.get('type')}")
        print(f"          basis_rate_annual={b.get('basis_rate_annual')}, basis_rate={b.get('basis_rate')}")
        print(f"          rank_252={b.get('rank_252')}, z_score={b.get('z_score')}, signal={b.get('signal')}")

# 构建信号
print("\n2. build_index_signals (仅期权数据)...")
indices = build_index_signals(option_data, None, None, None, None, None)

print("\n--- 各指数衍生品状态 ---")
for idx_name in ALL_INDICES:
    idx = indices.get(idx_name, {})
    d_count = idx.get('derivatives_count', 0)
    factors = idx.get('factors', {})
    print(f"\n{idx_name} (derivatives_count={d_count}):")
    for fk in ['SKEW', 'GEX', '基差']:
        if fk in factors:
            fv = factors[fk]
            print(f"  {fk}: value={fv.get('value')}, label={fv.get('label')}, score={fv.get('score')}")
        else:
            print(f"  {fk}: ❌ 缺失")

# 检查 score_gex 函数
print("\n3. 测试 score_gex 函数...")
try:
    from risk_forecast_daily import score_gex
    # 测试不同的 risk_label
    test_cases = [
        (5.0, '🟢低'),
        (5.0, '低'),
        (5.0, '中'),
        (5.0, '高'),
        (-5.0, '高'),
        (5.0, None),
    ]
    for gex_val, risk in test_cases:
        score, label = score_gex(gex_val, risk_label=risk)
        print(f"   gex={gex_val}, risk={repr(risk)} → score={score}, label={label}")
except Exception as e:
    print(f"   错误: {e}")
    import traceback
    traceback.print_exc()

# 检查 score_basis 函数
print("\n4. 测试 score_basis 函数...")
try:
    from risk_forecast_daily import score_basis
    test_cases = [
        (-7.43, None),   # data_hub 返回的 basis (rank_252=None)
        (-8.14, 0.2661),  # 历史 JSON 的 basis
        (2.0, 0.9),
        (-10.0, 0.05),
    ]
    for br, rk in test_cases:
        score, label = score_basis(br, rk)
        print(f"   basis_rate={br}, rank_252={rk} → score={score}, label={label}")
except Exception as e:
    print(f"   错误: {e}")
    import traceback
    traceback.print_exc()

# 完整卡片生成
print("\n5. 完整卡片生成测试...")
from feishu_card_utils import build_risk_forecast_card

wanquan = indices.get('万得全A', {})
top_risks = collect_risk_signals(indices)
top_opps = collect_opportunity_signals(indices)
conclusion = generate_conclusion(wanquan, top_risks, top_opps)

card = build_risk_forecast_card(
    '2026-07-17',
    indices=indices,
    conclusion=conclusion,
    top_risks=top_risks[:5],
    top_opps=top_opps[:5],
    style_drift=None,
    is_midday=False,
)

# 提取衍生品表格
print("\n--- 衍生品模块表格内容 ---")
for i, elem in enumerate(card.get('elements', [])):
    if isinstance(elem, dict):
        text = elem.get('text', {})
        if isinstance(text, dict):
            content = text.get('content', '')
            if '模块一：衍生品' in content or ('SKEW' in content and 'GEX' in content and '基差' in content and '指数' in content):
                print(content)
                print("---")

print("\n测试完成")
