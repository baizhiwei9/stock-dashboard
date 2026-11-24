# -*- coding: utf-8 -*-
"""独立源（新浪 前复权）复算 Top20 的 MAD 与 MA40 条件，逐股比对。"""
import os, sys
sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ea8aaca93ffeHyamlhKpZfCuW2/py_deps")
import pandas as pd, numpy as np, akshare as ak
WORK = os.path.dirname(os.path.abspath(__file__))
top = pd.read_csv(os.path.join(WORK, "top20.csv"))
out = []
for _, row in top.iterrows():
    code = row.ts_code
    sym = ("sh" if code.endswith(".SH") else ("bj" if code.endswith(".BJ") else "sz")) + code[:6]
    try:
        d = ak.stock_zh_a_daily(symbol=sym, start_date="20240101", end_date="20261101", adjust="qfq")
        d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y%m%d")
        d = d.sort_values("date").reset_index(drop=True).dropna(subset=["close"])
        c = d["close"]
        ma20, ma40, ma200 = c.iloc[-20:].mean(), c.iloc[-40:].mean(), c.iloc[-200:].mean()
        ma40_lag = c.iloc[-45:-5].mean()
        out.append({"rank": row["rank"], "ts_code": code, "name": row["name"],
                    "mad_sina": round(ma20/ma200-1, 4), "mad_ours": round(row["mad"], 4),
                    "d_mad": round(ma20/ma200-1-row["mad"], 4),
                    "close_sina": float(c.iloc[-1]), "close_ours": row["close"],
                    "above_ma40_sina": bool(c.iloc[-1] > ma40),
                    "ma40up_sina": bool(ma40 > ma40_lag),
                    "last_date": d["date"].iloc[-1], "bars": len(d)})
    except Exception as e:
        out.append({"rank": row["rank"], "ts_code": code, "name": row["name"], "mad_sina": None,
                    "err": "%s:%s" % (type(e).__name__, e)})
v = pd.DataFrame(out)
pd.set_option("display.width", 250)
print(v.to_string(index=False))
v.to_csv(os.path.join(WORK, "verify_top20.csv"), index=False, encoding="utf-8-sig")
