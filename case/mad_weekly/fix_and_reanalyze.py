# -*- coding: utf-8 -*-
"""剔除停牌/零价行后重跑缠论判定（不重新取数）。"""
import os
import sys
import glob

WORK = os.path.dirname(os.path.abspath(__file__))
SK = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/skills/user/chan-fenxing-ban"
sys.path.insert(0, SK)
import pandas as pd
import numpy as np
import chan_fractal

s = pd.read_csv(os.path.join(WORK, "top50_tag_series.csv"), dtype={"股票代码": str})
NAME = s.drop_duplicates("股票代码").set_index("股票代码")["股票名称"].to_dict()

cleaned = []
for p in sorted(glob.glob(os.path.join(WORK, "chan_in", "*.csv"))):
    d = pd.read_csv(p, parse_dates=["date"])
    n0 = len(d)
    bad = (d["volume"] == 0) | (d[["open", "high", "low", "close"]] == 0).any(axis=1)
    if bad.any():
        d = d[~bad].reset_index(drop=True)
        d.to_csv(p, index=False)
        cleaned.append((os.path.basename(p)[:-4], n0, len(d)))
print("清洗标的:", cleaned)

frames = {os.path.basename(p)[:-4]: pd.read_csv(p, parse_dates=["date"]) for p in
          sorted(glob.glob(os.path.join(WORK, "chan_in", "*.csv")))}
rows, meta = [], []
for c, df in frames.items():
    r = chan_fractal.analyze(df, level="day", base="struct", name=NAME.get(c, c))
    meta.append({"ts_code": c, "name": NAME.get(c, c), "bars": len(df),
                 "区间起": str(df.date.iloc[0].date()), "区间止": str(df.date.iloc[-1].date()),
                 "笔数": len(r["结构"]["笔端点"]), "中枢数": len(r["结构"]["中枢"]),
                 "机会数": len(r["交易机会"])})
    for op in r["交易机会"]:
        rows.append({"ts_code": c, "name": NAME.get(c, c), "行号": op["行号"], "时间": op["时间"],
                     "价格": op["价格"], "方向": op["方向"], "类型": op["类型"], "说明": op["说明"]})
sig = pd.DataFrame(rows).drop_duplicates(["ts_code", "行号", "类型"])
sig.to_csv(os.path.join(WORK, "chan_signals_all.csv"), index=False, encoding="utf-8-sig")
pd.DataFrame(meta).to_csv(os.path.join(WORK, "chan_meta.csv"), index=False, encoding="utf-8-sig")
print("去重信号 %d 条，涉及 %d 只" % (len(sig), sig.ts_code.nunique()))
print(sig.类型.value_counts().to_dict())

latest = []
for c, df in frames.items():
    n = len(df)
    g = sig[sig.ts_code == c].sort_values("行号")
    buys = g[g.类型.isin(["B1", "B2", "B3"])]
    if len(buys) == 0:
        continue
    b = buys.iloc[-1]
    sells = g[g.类型.isin(["S1", "S2", "S3"])]
    latest.append({"ts_code": c, "name": NAME.get(c, c), "最新买点类型": b.类型, "买点日期": str(b.时间)[:10],
                   "买点价格": b.价格, "距末日K线": n - 1 - int(b.行号),
                   "买点后是否有卖点": bool(len(sells) and sells.行号.max() > int(b.行号)),
                   "末日收盘": float(df.close.iloc[-1]), "买点总数": len(buys),
                   "买点总数_全窗": len(buys)})
t = pd.DataFrame(latest)
t["当前有效"] = (t.距末日K线 <= 30) & (~t.买点后是否有卖点)
t.to_csv(os.path.join(WORK, "chan_latest_buypoint.csv"), index=False, encoding="utf-8-sig")
print("\n有买点标的 %d 只；当前有效 %d 只" % (len(t), int(t.当前有效.sum())))
print(t.sort_values("距末日K线")[["ts_code", "name", "最新买点类型", "买点日期", "买点价格",
                                 "距末日K线", "买点后是否有卖点", "当前有效"]].to_string(index=False))