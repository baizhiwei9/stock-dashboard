# -*- coding: utf-8 -*-
"""对齐核验：tushare vs 新浪 同一股票同一日期序列。"""
import os
import sys
sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ea8aaca93ffeHyamlhKpZfCuW2/py_deps")
sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5e9bb55354ffe9Rg1AoYf1k3lwu")
import numpy as np
import pandas as pd
import akshare as ak
from ts_http import fetch_df

C = "600825.SH"
S, E = "20240601", "20261030"
ts = fetch_df("daily", {"ts_code": C, "start_date": S, "end_date": E})[["trade_date", "close"]]
ts = ts.sort_values("trade_date").reset_index(drop=True)
sn = ak.stock_zh_a_daily(symbol="sh600825", start_date=S, end_date="20261101")
sn = sn[["date", "close"]].rename(columns={"date": "trade_date", "close": "sina_close"})
sn["trade_date"] = pd.to_datetime(sn["trade_date"]).dt.strftime("%Y%m%d")
sn = sn.sort_values("trade_date").reset_index(drop=True)
m = ts.merge(sn, on="trade_date", how="outer").sort_values("trade_date").reset_index(drop=True)
print("ts rows=%d sina rows=%d merged=%d" % (len(ts), len(sn), len(m)))
print("date range ts:", ts.trade_date.iloc[0], ts.trade_date.iloc[-1], " sina:", sn.trade_date.iloc[0], sn.trade_date.iloc[-1])
m["diff"] = m["close"] - m["sina_close"]
print("mismatch rows (|diff|>0.011):", int((m["diff"].abs() > 0.011).sum()))
print(m[m["diff"].abs() > 0.011].head(15).to_string(index=False))
print("tail 6:")
print(m.tail(6).to_string(index=False))
mm = m.dropna()
print("aligned n=%d" % len(mm))
print("aligned MA20 ts=%.4f sina=%.4f ; MA200 ts=%.4f sina=%.4f" % (
    mm["close"].iloc[-20:].mean(), mm["sina_close"].iloc[-20:].mean(),
    mm["close"].iloc[-200:].mean(), mm["sina_close"].iloc[-200:].mean()))
print("aligned MAD ts=%.4f sina=%.4f" % (
    mm["close"].iloc[-20:].mean() / mm["close"].iloc[-200:].mean() - 1,
    mm["sina_close"].iloc[-20:].mean() / mm["sina_close"].iloc[-200:].mean() - 1))
print("aligned MA200 date window:", mm["trade_date"].iloc[-200], "->", mm["trade_date"].iloc[-1])