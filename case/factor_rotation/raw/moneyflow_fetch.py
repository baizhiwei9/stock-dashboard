# -*- coding: utf-8 -*-
"""补取个股/行业资金流（用于新增资金流因子）。
moneyflow        : 个股每日大中小单与净流入（万元）
moneyflow_ind_ths: 同花顺行业每日净额
输出：raw/moneyflow_daily.csv, raw/moneyflow_ind_ths.csv
"""
import os
import sys
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "raw")
sys.path.insert(0, RAW)
sys.path.insert(0, ROOT)
import ts_session as t  # noqa: E402

d = pd.read_csv(os.path.join(RAW, "daily_260d.csv"), usecols=["trade_date"], dtype=str)
d29 = pd.read_csv(os.path.join(RAW, "daily_20261029.csv"), usecols=["trade_date"], dtype=str)
dates = sorted(set(d["trade_date"]) | set(d29["trade_date"]))[-25:]
print("fetch dates", dates[0], "..", dates[-1], len(dates))

frames = []
for i, dt in enumerate(dates, 1):
    try:
        x = t.api("moneyflow", trade_date=dt)
        frames.append(x)
        print(f"[{i}/{len(dates)}] {dt} rows={len(x)}", flush=True)
    except Exception as e:
        print(f"[{i}/{len(dates)}] {dt} ERR {e}", flush=True)
mf = pd.concat(frames, ignore_index=True)
mf.to_csv(os.path.join(RAW, "moneyflow_daily.csv"), index=False)
print("[saved] moneyflow_daily.csv", mf.shape)

frames = []
for i, dt in enumerate(dates, 1):
    try:
        x = t.api("moneyflow_ind_ths", trade_date=dt)
        frames.append(x)
        print(f"[ind {i}/{len(dates)}] {dt} rows={len(x)}", flush=True)
    except Exception as e:
        print(f"[ind {i}/{len(dates)}] {dt} ERR {e}", flush=True)
if frames:
    ind = pd.concat(frames, ignore_index=True)
    ind.to_csv(os.path.join(RAW, "moneyflow_ind_ths.csv"), index=False)
    print("[saved] moneyflow_ind_ths.csv", ind.shape)
print("[done]")