# -*- coding: utf-8 -*-
"""核验：后复权口径 vs 原始口径、是否存在复权跳变异常。"""
import sys
sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5e9bb55354ffe9Rg1AoYf1k3lwu")
import numpy as np
import pandas as pd
from ts_http import fetch_df

CODES = ["688137.SH", "301234.SZ", "600825.SH", "301190.SZ", "002242.SZ",
         "601988.SH", "000530.SZ", "300006.SZ"]
S, E = "20250601", "20261030"

rows = []
for c in CODES:
    d = fetch_df("daily", {"ts_code": c, "start_date": S, "end_date": E})
    a = fetch_df("adj_factor", {"ts_code": c, "start_date": S, "end_date": E})
    d = d.sort_values("trade_date").reset_index(drop=True)
    a = a.sort_values("trade_date").reset_index(drop=True)
    mrg = d.merge(a[["trade_date", "adj_factor"]], on="trade_date", how="left")
    n_missing_adj = int(mrg["adj_factor"].isna().sum())
    mrg["adj_factor"] = mrg["adj_factor"].ffill().bfill()
    cadj = (mrg["close"] * mrg["adj_factor"]).values
    raw = d["close"].values
    print(c, "daily_rows=%d adj_rows=%d missing_adj=%d" % (len(d), len(a), n_missing_adj))
    a = mrg
    mad_adj = pd.Series(cadj).iloc[-20:].mean() / pd.Series(cadj).iloc[-200:].mean() - 1
    mad_raw = pd.Series(raw).iloc[-20:].mean() / pd.Series(raw).iloc[-200:].mean() - 1
    pct = pd.Series(cadj).pct_change().dropna()
    big = pct[pct.abs() > 0.21]
    n_adj_change = int((a["adj_factor"].diff().fillna(0) != 0).sum())
    rows.append({
        "ts_code": c, "n": len(d), "mad_adj": round(float(mad_adj), 4), "mad_raw": round(float(mad_raw), 4),
        "last_close_raw": float(raw[-1]), "last_close_hfq": float(cadj[-1]),
        "adj_factor_last": float(a["adj_factor"].values[-1]),
        "adj_factor_changes": n_adj_change,
        "jumps_gt21pct": len(big), "max_jump_pct": round(float(pct.abs().max()), 4),
        "max_jump_date": str(pct.abs().idxmax()),
    })
r = pd.DataFrame(rows)
pd.set_option("display.width", 250)
print(r.to_string(index=False))

# 具体查看 688137 的最近 10 个交易日
c = "688137.SH"
d = fetch_df("daily", {"ts_code": c, "start_date": "20260901", "end_date": E}).sort_values("trade_date")
a = fetch_df("adj_factor", {"ts_code": c, "start_date": "20260901", "end_date": E}).sort_values("trade_date")
m = d.merge(a, on=["ts_code", "trade_date"])
m["hfq"] = m["close"] * m["adj_factor"]
print(m[["trade_date", "close", "adj_factor", "hfq", "pct_chg"]].tail(12).to_string(index=False))