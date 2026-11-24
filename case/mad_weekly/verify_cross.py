# -*- coding: utf-8 -*-
"""独立源交叉核验：新浪行情 vs tushare 口径。"""
import os
import sys
sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ea8aaca93ffeHyamlhKpZfCuW2/py_deps")
import numpy as np
import pandas as pd

WORK = os.path.dirname(os.path.abspath(__file__))
top = pd.read_csv(os.path.join(WORK, "top20.csv"))
print(top[["rank", "ts_code", "name", "close_raw", "mad"]].head(8).to_string(index=False))

import akshare as ak
try:
    spot = ak.stock_zh_a_spot()
    print("spot rows=%d cols=%s" % (len(spot), list(spot.columns)[:8]))
    code_col = [c for c in spot.columns if "代码" in c][0]
    price_col = [c for c in spot.columns if c in ("最新价", "最新", "现价")][0]
    spot["code6"] = spot[code_col].astype(str).str[-6:]
    m = top.merge(spot[["code6", "名称", price_col]].rename(columns={price_col: "sina_close"}),
                  left_on=top.ts_code.str[:6], right_on="code6", how="left")
    print(m[["rank", "ts_code", "name", "close_raw", "sina_close", "mad"]].head(20).to_string(index=False))
except Exception as e:
    print("sina spot FAIL:", type(e).__name__, e)

for c in ["sz301234", "sz301190", "sh600825"]:
    try:
        d = ak.stock_zh_a_daily(symbol=c, start_date="20250101", end_date="20261101")
        d = d.dropna(subset=["close"]).reset_index(drop=True)
        ma20 = d["close"].iloc[-20:].mean()
        ma200 = d["close"].iloc[-200:].mean()
        print(c, "rows=%d last=%s close=%.2f mad_raw_sina=%.4f" % (
            len(d), str(d["date"].iloc[-1]), float(d["close"].iloc[-1]), ma20 / ma200 - 1))
    except Exception as e:
        print(c, "sina daily FAIL:", type(e).__name__, e)