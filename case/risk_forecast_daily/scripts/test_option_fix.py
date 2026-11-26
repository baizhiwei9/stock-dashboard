#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试：验证修复后的期权数据获取逻辑
"""
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

# 导入风险日报中的期权相关函数
from risk_forecast_daily import (
    _fetch_option_from_datahub,
    _get_option_spot_price,
    _normalize_option_data,
    _calc_skew_from_normalized,
    _calc_gex_from_normalized,
    _datahub_ready,
    _tushare_ready,
    _OPTION_DATAHUB_MAP,
)

print("=" * 70)
print("期权数据获取修复验证")
print("=" * 70)
print(f"data_hub就绪: {_datahub_ready}")
print(f"tushare就绪: {_tushare_ready}")

TRADE_DATE = "20260717"

# 1. 测试现货价格获取
print(f"\n{'='*70}")
print("测试1: 标的现货价格获取")
print(f"{'='*70}")
for opt_name, params in _OPTION_DATAHUB_MAP.items():
    price = _get_option_spot_price(params['underlying'], TRADE_DATE)
    print(f"  {opt_name} ({params['underlying']}): {price}")

# 2. 测试完整的期权数据获取
print(f"\n{'='*70}")
print("测试2: 完整期权数据获取 (_fetch_option_from_datahub)")
print(f"{'='*70}")
result, date = _fetch_option_from_datahub(TRADE_DATE)

if result:
    print(f"\n✅ 成功获取期权数据 (日期: {date})")
    print(f"\nSKEW 数据:")
    for name, skew in result.get('skew', {}).items():
        print(f"  {name}: SKEW={skew.get('cboe_skew')}, ATM_IV={skew.get('atm_iv')}%, 合约数={skew.get('contract_count')}")
    
    print(f"\nGEX 数据:")
    for name, gex in result.get('gex', {}).items():
        print(f"  {name}: GEX={gex.get('total_gex_yi')}亿, 风险={gex.get('risk')}, 合约数={gex.get('details_count')}")
    
    print(f"\n基差数据: {len(result.get('basis', []))} 个品种")
else:
    print("❌ 获取失败")

print("\n" + "=" * 70)
print("验证完成")
