# -*- coding: utf-8 -*-
"""补 33 只标的前复权 OHLC 并逐只跑缠论买点判定。"""
import os
import sys
import json
import time

WORK = os.path.dirname(os.path.abspath(__file__))
SK = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/skills/user/chan-fenxing-ban"
sys.path.insert(0, SK)
sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5e9bb55354ffe9Rg1AoYf1k3lwu")
import numpy as np
import pandas as pd
import chan_fractal
from ts_http import fetch_many

S, E = "20240601", "20261030"
s = pd.read_csv(os.path.join(WORK, "top50_tag_series.csv"), dtype={"股票代码": str})
CODES = sorted(s.股票代码.unique())
NAME = s.drop_duplicates("股票代码").set_index("股票代码")["股票名称"].to_dict()
BARS = os.path.join(WORK, "chan_in")
os.makedirs(BARS, exist_ok=True)

res_d = fetch_many("daily", [{"ts_code": c, "start_date": S, "end_date": E} for c in CODES], workers=4, verbose=False)
res_a = fetch_many("adj_factor", [{"ts_code": c, "start_date": S, "end_date": E} for c in CODES], workers=4, verbose=False)
print("取数完成 daily=%d adj=%d" % (len(res_d), len(res_a)))

frames = {}
for c, d, a in zip(CODES, res_d, res_a):
    if isinstance(d, Exception) or isinstance(a, Exception) or len(d) == 0:
        print("FAIL", c, d if isinstance(d, Exception) else "", a if isinstance(a, Exception) else "")
        continue
    m = d.merge(a[["trade_date", "adj_factor"]], on="trade_date", how="left")
    m["adj_factor"] = m["adj_factor"].ffill().bfill()
    m = m.sort_values("trade_date").reset_index(drop=True)
    last_adj = m["adj_factor"].iloc[-1]
    for col in ("open", "high", "low", "close"):
        m[col] = m[col] * m["adj_factor"] / last_adj
    m["date"] = pd.to_datetime(m["trade_date"], format="%Y%m%d")
    out = m[["date", "open", "high", "low", "close", "vol", "amount"]].rename(columns={"vol": "volume"})
    out.to_csv(os.path.join(BARS, "%s.csv" % c), index=False)
    frames[c] = out
print("落盘 %d 只标的日线（前复权）" % len(frames))

rows, meta = [], []
for c, df in frames.items():
    try:
        r = chan_fractal.analyze(df, level="day", base="struct", name=NAME.get(c, c))
    except Exception as e:
        print("analyze FAIL", c, type(e).__name__, e)
        continue
    m = r.get("_meta", {})
    meta.append({"ts_code": c, "name": NAME.get(c, c), "bars": len(df),
                 "区间起": str(df.date.iloc[0].date()), "区间止": str(df.date.iloc[-1].date()),
                 "笔数": len(r.get("结构", {}).get("笔端点", [])),
                 "中枢数": len(r.get("结构", {}).get("中枢", [])),
                 "机会数": len(r.get("交易机会", []))})
    for op in r.get("交易机会", []):
        rows.append({"ts_code": c, "name": NAME.get(c, c), "行号": op.get("行号"),
                     "时间": op.get("时间"), "价格": op.get("价格"), "方向": op.get("方向"),
                     "类型": op.get("类型"), "说明": op.get("说明")})
sig = pd.DataFrame(rows)
sig.to_csv(os.path.join(WORK, "chan_signals_all.csv"), index=False, encoding="utf-8-sig")
pd.DataFrame(meta).to_csv(os.path.join(WORK, "chan_meta.csv"), index=False, encoding="utf-8-sig")
print("信号合计 %d 条（%d 只标的）" % (len(sig), sig.ts_code.nunique() if len(sig) else 0))
if len(sig):
    print(sig.类型.value_counts().to_string())

# 最近 30 根内出现的买点，且其后无对应卖点 → 仍有效
recent = []
for c, df in frames.items():
    n = len(df)
    sub = sig[(sig.ts_code == c) & (sig.方向.astype(str).str.contains("买") | sig.类型.isin(["B1", "B2", "B3"]))]
    for _, x in sub.iterrows():
        idx = int(x["行号"])
        after = sig[(sig.ts_code == c) & (sig.行号 > idx)]
        invalidated = len(after[after.类型.isin(["S1", "S2", "S3"])]) > 0
        recent.append({**x.to_dict(), "距末日K线数": n - 1 - idx, "选后是否出现卖点": invalidated,
                       "末日收盘": float(df.close.iloc[-1]), "买点价格": float(x["价格"])})
rc = pd.DataFrame(recent)
rc.to_csv(os.path.join(WORK, "chan_buypoints.csv"), index=False, encoding="utf-8-sig")
if len(rc):
    live = rc[(rc.距末日K线数 <= 30) & (~rc.选后是否出现卖点)]
    print("\n最近 30 根K线内且未被卖点破坏的买点：%d 条，涉及 %d 只" % (len(live), live.ts_code.nunique()))
    print(live.sort_values(["距末日K线数"]).to_string(index=False))
print("\n每只标的命中情况（含全部买点）：")
print(rc.groupby(["ts_code", "name"])["类型"].apply(lambda x: "、".join(x)).to_string() if len(rc) else "无")