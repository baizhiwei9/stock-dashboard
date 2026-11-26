#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""期权 akshare 备用链路 改造前后对照探针

用途：直接调用 option_monitor.py 内部的 akshare 备用链路函数，记录基准/改造后输出，
      用于比对「只换数据获取层」后字段、条数、数值是否一致。

用法：python3 akshare_path_probe.py <scripts_dir> <tag> <trade_date>
输出：probe_<tag>.json
"""
import importlib
import json
import os
import socket
import sys

TRADE_DATE = sys.argv[3] if len(sys.argv) > 3 else "20261022"
TAG = sys.argv[2] if len(sys.argv) > 2 else "tag"
SCRIPTS_DIR = os.path.abspath(sys.argv[1])

socket.setdefaulttimeout(30)
sys.path.insert(0, SCRIPTS_DIR)

om = importlib.import_module("option_monitor")

TARGETS = [
    ("510050", "SSE", None),
    ("510300", "SSE", None),
    ("510500", "SSE", None),
    ("588000", "SSE", None),
    ("159915", "SZSE", None),
    ("MO", "CFFEX", "000852"),
]

out = {"tag": TAG, "scripts_dir": SCRIPTS_DIR, "trade_date": TRADE_DATE}

# 1) akshare 降级现货价
out["spot_akshare_index_000852"] = om._spot_akshare_index("000852")
out["spot_akshare_etf"] = {}
for code, ex, _ in TARGETS:
    if ex == "CFFEX":
        continue
    out["spot_akshare_etf"][code] = om._spot_akshare_etf(code, ex)

# 2) akshare 备用合约链路
out["targets"] = {}
for code, ex, uc in TARGETS:
    rec = {}
    try:
        contracts = om.get_option_contracts_akshare(code, ex, TRADE_DATE)
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {e}"
        out["targets"][code] = rec
        continue

    contracts = contracts or []
    rec["count"] = len(contracts)
    rec["calls"] = sum(1 for c in contracts if c.get("call_put") == "C")
    rec["puts"] = sum(1 for c in contracts if c.get("call_put") == "P")
    rec["fields"] = sorted(contracts[0].keys()) if contracts else []
    rec["has_price"] = sum(1 for c in contracts if float(c.get("close") or 0) > 0)
    rec["has_oi"] = sum(1 for c in contracts if int(c.get("oi") or 0) > 0)
    rec["sum_oi"] = sum(int(c.get("oi") or 0) for c in contracts)
    rec["sum_close"] = round(sum(float(c.get("close") or 0) for c in contracts), 6)
    # 全量明细（含合约代码/行权价/到期日/收盘/持仓），用于逐条比对
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
    out["targets"][code] = rec

with open(f"probe_{TAG}.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"probe_{TAG}.json written")
for k, v in out["targets"].items():
    print(k, "count", v.get("count"), "has_price", v.get("has_price"), "has_oi", v.get("has_oi"))
