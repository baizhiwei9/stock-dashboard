#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""调试脚本：检查 build_index_signals 中期权数据是否正确填充"""

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

SCRIPT_DIR = _cp('/app/data/所有对话/主对话/市场情绪监控/scripts')
sys.path.insert(0, SCRIPT_DIR)

from risk_forecast_daily import (
    load_option_data,
    load_cfc_data,
    load_structural_factor_data,
    load_market_monitor_summary,
    load_volume_percentile_data,
    load_lppl_data,
    build_index_signals,
    OPT_TO_INDEX,
    BASIS_TO_INDEX,
    ALL_INDICES,
)

trade_date = '20260717'

print("=" * 60)
print(f"调试 build_index_signals 期权数据填充 ({trade_date})")
print("=" * 60)

# 1. 加载所有数据
print("\n📥 加载期权数据...")
opt_result = load_option_data(trade_date)
option_data = opt_result[0] if opt_result else None

print("\n📥 加载CFC数据...")
cfc_result = load_cfc_data(trade_date)
cfc_data = cfc_result[0] if cfc_result else None

print("\n📥 加载结构因子数据...")
sf_result = load_structural_factor_data(trade_date)
sf_data = sf_result[0] if sf_result else None

print("\n📥 加载市场监控数据...")
mm_result = load_market_monitor_summary(trade_date)
market_data = mm_result[0] if mm_result else None

print("\n📥 加载成交量分位数据...")
vp_result = load_volume_percentile_data(trade_date)
vol_pct_data = vp_result[0] if vp_result else None

print("\n📥 加载LPPL数据...")
lppl_result = load_lppl_data(trade_date, '000001.SH')
lppl_data = lppl_result[0] if lppl_result else None

# 2. 构建指数信号
print("\n\n🧮 调用 build_index_signals...")
indices = build_index_signals(option_data, cfc_data, sf_data, market_data, vol_pct_data, lppl_data)

# 3. 检查每个指数的衍生品数据
print("\n" + "=" * 60)
print("各指数衍生品因子检查")
print("=" * 60)

for idx_name in ALL_INDICES:
    idx = indices.get(idx_name, {})
    d_count = idx.get('derivatives_count', 0)
    factors = idx.get('factors', {})
    
    print(f"\n📊 {idx_name}:")
    print(f"  derivatives_count: {d_count}")
    print(f"  factors keys: {list(factors.keys())}")
    
    # 检查三个衍生品因子
    for fkey in ['SKEW', 'GEX', '基差']:
        if fkey in factors:
            f = factors[fkey]
            print(f"  ✅ {fkey}: value={f.get('value')}, label={f.get('label')}, score={f.get('score')}")
        else:
            print(f"  ❌ {fkey}: 缺失")

# 4. 特别检查映射关系
print("\n\n" + "=" * 60)
print("映射关系验证")
print("=" * 60)
print(f"OPT_TO_INDEX: {OPT_TO_INDEX}")
print(f"BASIS_TO_INDEX: {BASIS_TO_INDEX}")

if option_data:
    skew_keys = list(option_data.get('skew', {}).keys())
    gex_keys = list(option_data.get('gex', {}).keys())
    basis_products = [b.get('product') for b in option_data.get('basis', [])]
    basis_types = [b.get('type') for b in option_data.get('basis', [])]
    
    print(f"\nskew keys: {skew_keys}")
    print(f"gex keys: {gex_keys}")
    print(f"basis products: {basis_products}")
    print(f"basis types: {basis_types}")
    
    # 检查哪些期权标的能映射到指数
    print("\n期权标的→指数映射匹配:")
    for opt_name in skew_keys:
        idx_name = OPT_TO_INDEX.get(opt_name, '无映射')
        exists = idx_name in ALL_INDICES
        print(f"  {opt_name:10s} → {idx_name:10s} {'✅' if exists else '❌(不在ALL_INDICES)'}")
    
    # 检查基差映射
    print("\n基差产品→指数映射匹配:")
    for b in option_data.get('basis', []):
        product = b.get('product', '')
        btype = b.get('type', '')
        idx_name = BASIS_TO_INDEX.get(product, '无映射')
        exists = idx_name in ALL_INDICES
        type_match = (btype == '当季')
        print(f"  {product:5s} ({btype}) → {idx_name:10s} 指数存在:{'✅' if exists else '❌'} 类型匹配:{'✅' if type_match else '❌'}")

print("\n" + "=" * 60)
print("调试完毕")
print("=" * 60)
