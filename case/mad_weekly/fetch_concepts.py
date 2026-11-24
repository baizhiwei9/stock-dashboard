# -*- coding: utf-8 -*-
"""抓取同花顺概念指数(885xxx/886xxx)成分股并落盘。"""
import os
import sys
import csv
import time

WORK = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5e9bb55354ffe9Rg1AoYf1k3lwu")
from ts_http import fetch, fetch_many, fetch_df

idx = []
with open(os.path.join(WORK, "ths_index_all.csv"), encoding="utf-8-sig") as fh:
    for r in csv.DictReader(fh):
        if r["type"] == "N" and r["ts_code"][:3] in ("885", "886"):
            idx.append(r)
print("概念指数数:", len(idx))

f, items = fetch("ths_member", {"ts_code": idx[0]["ts_code"]})
print("ths_member fields:", f, "| sample:", items[0] if items else None)

codes = [{"ts_code": r["ts_code"]} for r in idx]
t0 = time.time()
res = fetch_many("ths_member", codes, workers=6, verbose=True, tag="member")
print("fetch done %.0fs" % (time.time() - t0))

rows, fails = [], []
for meta, df in zip(idx, res):
    if isinstance(df, Exception):
        fails.append((meta["ts_code"], str(df)))
        continue
    for _, x in df.iterrows():
        if "is_new" in df.columns and str(x.get("is_new")) not in ("Y", "y", "1", "True", "nan"):
            continue
        if "out_date" in df.columns and str(x.get("out_date")).strip() not in ("", "None", "nan"):
            continue
        rows.append({"概念代码": meta["ts_code"], "概念名称": meta["name"],
                     "股票代码": x["con_code"], "股票名称": x.get("con_name", "")})
out = os.path.join(WORK, "concept_members.csv")
with open(out, "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=["概念代码", "概念名称", "股票代码", "股票名称"])
    w.writeheader()
    w.writerows(rows)
print("members rows=%d concepts_ok=%d fails=%d" % (len(rows), len(idx) - len(fails), len(fails)))
if fails:
    print("fails sample:", fails[:10])

uni = set()
with open(os.path.join(WORK, "universe_all.csv"), encoding="utf-8-sig") as fh:
    rd = csv.DictReader(fh)
    for r in rd:
        uni.add(r["ts_code"])
cov = {}
for r in rows:
    cov.setdefault(r["股票代码"], set()).add(r["概念代码"])
print("universe=%d 有概念归属=%d 无概念归属=%d" % (len(uni), len(set(cov) & uni), len(uni - set(cov))))
print("概念内股票数分位:", sorted(len(v) for v in cov.values())[:5], "...")