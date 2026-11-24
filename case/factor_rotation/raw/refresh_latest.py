# -*- coding: utf-8 -*-
"""把 daily_260d.csv / daily_basic_65d.csv 增量补齐到最新可得交易日（append-only，不改动 fetch_all.py）"""
import os, sys, time
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ts_session import api  # noqa

RAW = os.path.dirname(os.path.abspath(__file__))
TODAY = "20261029"
cal = pd.read_csv(os.path.join(RAW, "trade_cal.csv"), dtype=str)["trade_date"].str.replace("-", "")
cal = [d for d in cal if d <= TODAY]

def append(name, table, days, fname):
    fp = os.path.join(RAW, fname)
    old = pd.read_csv(fp, dtype={"ts_code": str, "trade_date": str})
    have = set(old["trade_date"].astype(str).unique())
    todo = [d for d in days if d not in have]
    print(f"[{name}] need {len(todo)} days: {todo}", flush=True)
    chunks = []
    for d in todo:
        try:
            df = api(table, trade_date=d)
        except Exception as e:
            print(f"  [{name}] {d} FAIL {str(e)[:60]}", flush=True); continue
        if df is None or not len(df):
            print(f"  [{name}] {d} EMPTY (未发布)", flush=True); continue
        chunks.append(df); print(f"  [{name}] {d} rows={len(df)}", flush=True)
        time.sleep(0.3)
    if chunks:
        out = pd.concat([old] + chunks, ignore_index=True)
        out = out.drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
        out.to_csv(fp, index=False)
        print(f"  saved {fname} rows={len(out)} new_max={out['trade_date'].max()}", flush=True)
    else:
        print(f"  no new data for {fname}", flush=True)

append("daily", "daily", cal[-6:], "daily_260d.csv")
append("basic", "daily_basic", cal[-6:], "daily_basic_65d.csv")
