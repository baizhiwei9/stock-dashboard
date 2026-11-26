#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""期权 akshare 备用链路 端到端对照（模拟 Tushare 全挂）

用途：把 option_monitor 的 Tushare 通道打成失败，强制走 akshare 备用链路，
      跑完整的「合约获取 + Skew/GEX/RiskReversal 计算」，比对改造前后结果。
      为保证只比合约层，指标计算统一注入同一组基准现货价。

用法：python3 fallback_e2e.py <scripts_dir> <tag> <trade_date>
输出：fallback_<tag>.json
"""
import importlib
import json
import os
import socket
import sys

TAG = sys.argv[2] if len(sys.argv) > 2 else "tag"
TRADE_DATE = sys.argv[3] if len(sys.argv) > 3 else "20261022"
SCRIPTS_DIR = os.path.abspath(sys.argv[1])

socket.setdefaulttimeout(30)
sys.path.insert(0, SCRIPTS_DIR)

om = importlib.import_module("option_monitor")


class _DeadPro:
    """模拟 Tushare 全挂：任何调用都抛异常。"""

    def __getattr__(self, name):
        def _boom(*a, **k):
            raise RuntimeError(f"Tushare unavailable (simulated): {name}")
        return _boom


om.pro = _DeadPro()

# 基准现货价（取自 Tushare 主链路，已独立核验），用于隔离合约层差异
FIXED_SPOT = {
    "510050": 2.836,
    "510300": 4.107,
    "510500": 6.684,
    "588000": 1.377,
    "159915": 2.592,
    "MO": 6578.2722,
}

TARGETS = [
    ("510050", "SSE", "510050", None),
    ("510300", "SSE", "510300", None),
    ("510500", "SSE", "510500", None),
    ("588000", "SSE", "588000", None),
    ("159915", "SZSE", "159915", None),
    ("MO", "CFFEX", None, "000852"),
]

def _json_safe(obj, ndigits=6):
    """把 numpy 标量/嵌套结构转成可 JSON 序列化的结构，浮点统一保留 6 位。"""
    if obj is None or isinstance(obj, (str, bool)):
        return obj
    if isinstance(obj, (int,)):
        return obj
    if isinstance(obj, float):
        return round(obj, ndigits)
    if hasattr(obj, "item") and not isinstance(obj, (dict, list, tuple)):
        try:
            return _json_safe(obj.item(), ndigits)
        except Exception:
            return str(obj)
    if isinstance(obj, dict):
        return {str(k): _json_safe(v, ndigits) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v, ndigits) for v in obj]
    return str(obj)


out = {"tag": TAG, "scripts_dir": SCRIPTS_DIR, "trade_date": TRADE_DATE, "targets": {}}

for key, exchange, etf_code, underlying in TARGETS:
    rec = {}
    # 1) 现货链路（akshare 降级）
    try:
        rec["spot_chain"] = om.get_spot_price(etf_code, exchange, TRADE_DATE, underlying)
    except Exception as e:
        rec["spot_chain"] = f"ERR {type(e).__name__}: {e}"

    # 2) 合约链路（Tushare 已打死 -> akshare 备用）
    try:
        contracts = om.get_option_contracts(exchange, etf_code, TRADE_DATE, etf_code) or []
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {e}"
        out["targets"][key] = rec
        continue

    rec["count"] = len(contracts)
    rec["rows"] = [
        {
            "ts_code": str(c.get("ts_code")),
            "call_put": c.get("call_put"),
            "exercise_price": float(c.get("exercise_price") or 0),
            "maturity_date": str(c.get("maturity_date")),
            "close": round(float(c.get("close") or 0), 6),
            "oi": int(c.get("oi") or 0),
        }
        for c in contracts
    ]

    # 3) 指标计算（统一注入基准现货价）
    spot = FIXED_SPOT[key]
    for name, fn in (
        ("skew", lambda: om.calc_skew(contracts, spot, TRADE_DATE, 0.03)),
        ("gex", lambda: om.calc_gex(contracts, spot, TRADE_DATE)),
        ("rr", lambda: om.calc_risk_reversal(contracts, spot, TRADE_DATE)),
    ):
        try:
            rec[name] = _json_safe(fn())
        except Exception as e:
            rec[name] = f"ERR {type(e).__name__}: {e}"

    out["targets"][key] = rec

with open(f"fallback_{TAG}.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"fallback_{TAG}.json written")
for k, v in out["targets"].items():
    sk = v.get("skew") or {}
    gx = v.get("gex") or {}
    rr = v.get("rr") or {}
    print(k, "count", v.get("count"),
          "iv_skew", sk.get("iv_skew") if isinstance(sk, dict) else sk,
          "gex_net", gx.get("net_gex") if isinstance(gx, dict) else gx,
          "rr", rr.get("rr") if isinstance(rr, dict) else rr)
