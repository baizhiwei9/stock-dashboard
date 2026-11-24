# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ea8aaca93ffeHyamlhKpZfCuW2/py_deps")
sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5e9bb55354ffe9Rg1AoYf1k3lwu")
import pandas as pd, numpy as np, akshare as ak
from ts_http import fetch_df
C, S, E = "600825.SH", "20240601", "20261030"
ts = fetch_df("daily", {"ts_code": C, "start_date": S, "end_date": E}).sort_values("trade_date").reset_index(drop=True)
sn = ak.stock_zh_a_daily(symbol="sh600825", start_date=S, end_date="20261101")
sn["trade_date"] = pd.to_datetime(sn["date"]).dt.strftime("%Y%m%d")
sn = sn.sort_values("trade_date").reset_index(drop=True)
only = ts[~ts.trade_date.isin(set(sn.trade_date))]
print("tushare-only dates (%d):" % len(only))
print(only[["trade_date","close","pre_close","pct_chg","vol","amount"]].to_string(index=False))
m = ts.merge(sn[["trade_date","close"]].rename(columns={"close":"sn"}), on="trade_date", how="left")
print("\ntushare full-series MA20/MA200/MAD:",
      round(m.close.iloc[-20:].mean(),4), round(m.close.iloc[-200:].mean(),4),
      round(m.close.iloc[-20:].mean()/m.close.iloc[-200:].mean()-1,4))
mm = m.dropna(subset=["sn"]).reset_index(drop=True)
print("sina-aligned  MA20/MA200/MAD:",
      round(mm.sn.iloc[-20:].mean(),4), round(mm.sn.iloc[-200:].mean(),4),
      round(mm.sn.iloc[-20:].mean()/mm.sn.iloc[-200:].mean()-1,4))
print("dates of last 200 tushare rows:", m.trade_date.iloc[-200], "->", m.trade_date.iloc[-1])
print("dates of last 200 sina rows   :", mm.trade_date.iloc[-200], "->", mm.trade_date.iloc[-1])
