# -*- coding: utf-8 -*-
"""为保留的细分题材标签计算两口径数值：概念指数口径（主）+ 成分股等权中位数（备用）。"""
import os
import sys

WORK = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5e9bb55354ffe9Rg1AoYf1k3lwu")
import numpy as np
import pandas as pd
from ts_http import fetch_many, fetch_df

S, E = "20251201", "20261030"
keep = pd.read_csv(os.path.join(WORK, "tag_keep_list.csv"))
idx = pd.read_csv(os.path.join(WORK, "ths_index_all.csv"), encoding="utf-8-sig")
idx = idx[idx["type"].isin(["N", "I"])] if "type" in idx.columns else idx
code_of = dict(zip(idx["name"], idx["ts_code"]))
keep["概念代码"] = keep["标签"].map(code_of)
print("68 个标签中能匹配同花顺指数代码的: %d / %d" % (keep.概念代码.notna().sum(), len(keep)))
print("未匹配:", keep[keep.概念代码.isna()].标签.tolist())

codes = keep.概念代码.dropna().tolist()
res = fetch_many("ths_daily", [{"ts_code": c, "start_date": S, "end_date": E} for c in codes],
                 workers=4, verbose=False)
raw = {}
for c, d in zip(codes, res):
    if isinstance(d, Exception) or d is None or len(d) == 0:
        print("取数失败/为空:", c, type(d).__name__ if isinstance(d, Exception) else "empty")
        continue
    d = d.sort_values("trade_date").reset_index(drop=True)
    base = d[d.trade_date <= "20251231"]
    if len(d) < 40:
        print("历史不足 40 根:", c, len(d))
        continue
    last = d.iloc[-1]
    ytd = (last.close / base.iloc[-1].close - 1) if len(base) else np.nan
    if not len(base):
        print("无年初基准（新指数）:", c, "首发", d.trade_date.min())
    raw[c] = {"指数今年涨幅": ytd, "指数MAD20": d.close.iloc[-20:].mean() / d.close.iloc[-40:].mean() - 1,
              "基准日": base.iloc[-1].trade_date if len(base) else "—", "末日": last.trade_date,
              "末日收盘": last.close, "样本根数": len(d)}

# 成分股口径（备用）：Top50 成员股的 mad20 中位数 / 今年以来涨幅中位数
top = pd.read_csv(os.path.join(WORK, "top50_mad20_full.csv"), dtype={"ts_code": str})
mem = pd.read_csv(os.path.join(WORK, "concept_members.csv"), dtype={"股票代码": str})
m20 = dict(zip(top.ts_code, top.mad20))
import glob
y0 = {}
d0 = np.load(os.path.join(WORK, "cache_ma", "D_20251231.npz"), allow_pickle=True) if os.path.exists(
    os.path.join(WORK, "cache_ma", "D_20251231.npz")) else None
print("年末基准缓存存在:", d0 is not None)

rows = []
for _, r in keep.iterrows():
    name, c = r.标签, r.概念代码
    rec = {"标签": name, "Top50成员数": int(r.Top50成员数), "概念代码": c if isinstance(c, str) else "—",
           "指数MAD20": np.nan, "指数今年涨幅": np.nan, "口径": "—", "末日": "—"}
    if isinstance(c, str) and c in raw:
        rec.update({"指数MAD20": raw[c]["指数MAD20"], "指数今年涨幅": raw[c]["指数今年涨幅"],
                    "口径": "概念指数" if raw[c]["指数今年涨幅"] == raw[c]["指数今年涨幅"] else "概念指数（无年初基准）",
                    "末日": raw[c]["末日"], "基准日": raw[c]["基准日"]})
    else:
        members = [x for x in mem[mem.概念名称 == name].股票代码.unique() if x in m20]
        if members:
            rec.update({"指数MAD20": float(np.median([m20[x] for x in members])),
                        "口径": "成分股中位数备用", "末日": "2026-10-30"})
    rows.append(rec)
t = pd.DataFrame(rows)
t.to_csv(os.path.join(WORK, "tag_metrics.csv"), index=False, encoding="utf-8-sig")
pd.set_option("display.width", 240)
print("\n口径分布:", t.口径.value_counts().to_dict())
print("\n按 MAD20 降序（前 20）:")
print(t.sort_values("指数MAD20", ascending=False).head(20)[["标签", "Top50成员数", "指数MAD20", "指数今年涨幅", "口径"]]
      .round(4).to_string(index=False))
print("\n按今年以来涨幅降序（前 20）:")
print(t.sort_values("指数今年涨幅", ascending=False).head(20)[["标签", "Top50成员数", "指数今年涨幅", "指数MAD20", "口径"]]
      .round(4).to_string(index=False))
print("\n两指标合并排序（末 8 名，按 MAD20）:")
print(t.sort_values("指数MAD20").head(8)[["标签", "指数MAD20", "指数今年涨幅"]].round(4).to_string(index=False))