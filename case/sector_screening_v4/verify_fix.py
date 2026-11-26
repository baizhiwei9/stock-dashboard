#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证四大维度概览的衍生品模块"""
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

# 加载所有数据（简化版）
option_result = load_option_data(TEST_DATE, enable_fallback=False)
option_data = option_result[0] if option_result else None

cfc_result = load_cfc_data(TEST_DATE, enable_fallback=False)
cfc_data = cfc_result[0] if cfc_result else None

sf_result = load_structural_factor_data(TEST_DATE, enable_fallback=False)
sf_data = sf_result[0] if sf_result else None

# 构建信号
indices = build_index_signals(option_data, cfc_data, sf_data, None, None, None)

wq = indices.get('万得全A', {})
ds = wq.get('dim_scores', {})

print("=" * 60)
print("万得全A 四大维度得分")
print("=" * 60)
dim_info = [
    ('derivatives', '衍生品'),
    ('structural', '结构'),
    ('sentiment', '量能情绪'),
    ('extreme', '极端'),
]
for dim_key, dim_name in dim_info:
    s = ds.get(dim_key)
    if s is not None:
        print(f"  {dim_name}: {s:+.3f} ✅")
    else:
        print(f"  {dim_name}: N/A ❌ 缺失")

print(f"\n万得全A derivatives_count: {wq.get('derivatives_count', 0)}")
print(f"万得全A derivatives_score: {wq.get('derivatives_score', 0)}")

# 检查万得全A的factors
print(f"\n万得全A factors中衍生品相关:")
for fk in ['SKEW', 'GEX', '基差']:
    f = wq.get('factors', {}).get(fk)
    if f:
        print(f"  {fk}: value={f.get('value')}, label={f.get('label')}, score={f.get('score'):.3f}")
    else:
        print(f"  {fk}: 缺失")

# 生成卡片，检查四大维度
print("\n" + "=" * 60)
print("飞书卡片 - 四大维度概览")
print("=" * 60)
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

# 查找四大维度区域
for i, elem in enumerate(card.get('elements', [])):
    if isinstance(elem, dict):
        tag = elem.get('tag', '')
        if tag == 'column_set':
            # 检查是否为四大维度
            columns = elem.get('columns', [])
            if len(columns) >= 3:
                texts = []
                for col in columns:
                    elems = col.get('elements', [])
                    for e in elems:
                        if e.get('tag') == 'markdown':
                            texts.append(e.get('content', ''))
                combined = ' '.join(texts)
                if '衍生品' in combined or '结构' in combined:
                    print(f"\n[元素{i}] 维度概览:")
                    for t in texts:
                        print(f"  {t.replace(chr(10), ' | ')}")

print("\n验证完成")
