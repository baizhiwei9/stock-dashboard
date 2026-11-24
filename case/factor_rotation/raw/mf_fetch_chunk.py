# -*- coding: utf-8 -*-
"""按日期序号块补取个股资金流。
用法: python3 raw/mf_fetch_chunk.py <i0> <i1>
从 raw/_mf_dates.txt 取日期序列的 [i0, i1) 区间，逐日调用 moneyflow（单日返回全市场）。
输出: raw/mf_chunk_<i0:03d>.csv（各块独立落盘，便于中断后按块续跑）
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

i0 = int(sys.argv[1])
i1 = int(sys.argv[2])
dates = open(os.path.join(RAW, "_mf_dates.txt")).read().split()[i0:i1]
print(f"chunk {i0}:{i1} -> {len(dates)} dates {dates[0]}..{dates[-1]}", flush=True)

frames = []
fails = []
t0 = time.time()
for i, dt in enumerate(dates, 1):
    try:
        x = t.api("moneyflow", trade_date=dt)
    except Exception as e:
        fails.append(dt)
        print(f"  {dt} ERR {str(e)[:80]}", flush=True)
        continue
    frames.append(x)
    if i % 10 == 0 or i == len(dates):
        print(f"[{i}/{len(dates)}] {dt} rows={len(x)} {time.time()-t0:.0f}s", flush=True)

if frames:
    mf = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["ts_code", "trade_date"])
    out = os.path.join(RAW, f"mf_chunk_{i0:03d}.csv")
    mf.to_csv(out, index=False)
    print(f"[saved] {out} {mf.shape} dates={mf['trade_date'].nunique()} fails={fails}", flush=True)
else:
    print(f"[none] fails={fails}", flush=True)