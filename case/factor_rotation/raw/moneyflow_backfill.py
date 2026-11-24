# -*- coding: utf-8 -*-
"""补取个股资金流全窗口：2025-09-08 .. 2026-10-29（274 个交易日）。

设计为「可续跑 + 分片落盘」：
  - 每日一个分片文件 raw/mf_parts/{trade_date}.csv，已存在则跳过（断点续跑）。
  - --max N 限制本次最多取 N 个缺失日，便于在 sandbox 单次调用时限内分批推进。
  - 全部取完后（或 --merge-only）合并为 raw/moneyflow_daily.csv（原子替换）。

接口 moneyflow 单日返回全市场大中小单与净流入额（万元）。
板块级资金流由成分股汇总，不再依赖 moneyflow_ind_ths。
"""
import os
import sys
import time

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "raw")
sys.path.insert(0, RAW)
sys.path.insert(0, ROOT)
import ts_session as t  # noqa: E402

PARTS = os.path.join(RAW, "mf_parts")
os.makedirs(PARTS, exist_ok=True)
OUT = os.path.join(RAW, "moneyflow_daily.csv")
TMP = OUT + ".tmp"

ARGS = [a for a in sys.argv[1:]]
MAXN = None
MERGE_ONLY = "--merge-only" in ARGS
for a in ARGS:
    if a.startswith("--max="):
        MAXN = int(a.split("=", 1)[1])

a = pd.read_csv(os.path.join(RAW, "daily_260d.csv"), usecols=["trade_date"], dtype=str)
b = pd.read_csv(os.path.join(RAW, "daily_20261029.csv"), usecols=["trade_date"], dtype=str)
dates = sorted(set(a["trade_date"]) | set(b["trade_date"]))
print("target dates", dates[0], "..", dates[-1], "n=", len(dates), flush=True)

# 1) 已有 25 日数据先落成日分片，避免重复取数
if os.path.exists(OUT):
    old = pd.read_csv(OUT, dtype={"trade_date": str, "ts_code": str})
    n_new = 0
    for dt, g in old.groupby("trade_date"):
        p = os.path.join(PARTS, f"{dt}.csv")
        if not os.path.exists(p):
            g.to_csv(p, index=False)
            n_new += 1
    print(f"[seed] existing dates -> parts: {old['trade_date'].nunique()} days, wrote {n_new}", flush=True)

# 2) 找出缺失日
have = {f[:-4] for f in os.listdir(PARTS) if f.endswith(".csv")}
todo = [d for d in dates if d not in have]
print(f"[todo] missing {len(todo)} days", flush=True)

fails = []
t0 = time.time()
done = 0
if not MERGE_ONLY:
    for i, dt in enumerate(todo, 1):
        if MAXN is not None and done >= MAXN:
            print(f"[stop] reached --max={MAXN}", flush=True)
            break
        try:
            x = t.api("moneyflow", trade_date=dt)
        except Exception as e:
            fails.append(dt)
            print(f"[{i}/{len(todo)}] {dt} ERR {str(e)[:90]}", flush=True)
            continue
        if x is None or len(x) == 0:
            fails.append(dt)
            print(f"[{i}/{len(todo)}] {dt} EMPTY", flush=True)
            continue
        if "trade_date" not in x.columns:
            x.insert(0, "trade_date", dt)
        x["trade_date"] = x["trade_date"].astype(str)
        p = os.path.join(PARTS, f"{dt}.csv")
        x.to_csv(p + ".tmp", index=False)
        os.replace(p + ".tmp", p)
        done += 1
        if done % 10 == 0:
            print(f"[{i}/{len(todo)}] {dt} rows={len(x)} done={done} elapsed={time.time()-t0:.0f}s", flush=True)

# 3) 合并
parts = sorted(f for f in os.listdir(PARTS) if f.endswith(".csv"))
frames = [pd.read_csv(os.path.join(PARTS, f), dtype={"trade_date": str, "ts_code": str}) for f in parts]
mf = pd.concat(frames, ignore_index=True)
mf = mf.drop_duplicates(subset=["ts_code", "trade_date"])
mf = mf.sort_values(["trade_date", "ts_code"], kind="mergesort")
mf.to_csv(TMP, index=False)
os.replace(TMP, OUT)
print("[saved]", OUT, mf.shape, "dates", mf["trade_date"].nunique(), "codes", mf["ts_code"].nunique(), flush=True)
print("[fails]", fails, flush=True)