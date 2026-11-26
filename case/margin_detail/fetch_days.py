# -*- coding: utf-8 -*-
"""补取逐日明细（两融明细 / 每日指标）

用法：``python fetch_days.py margin_detail`` 或 ``python fetch_days.py daily_basic``
数据经 DataHub/EDB 数据集（``CN.MARKET.MARGIN.DETAIL`` / ``CN.MARKET.BASIC.DAILY``）取得。
"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dh  # noqa: E402

D = os.path.join(HERE, "data")
mode = sys.argv[1] if len(sys.argv) > 1 else "margin_detail"   # margin_detail | daily_basic
KEEP = {"margin_detail": ["ts_code", "trade_date", "rzye", "rqye", "rzmre", "rzche", "rzrqye", "rqyl", "rqchl"],
        "daily_basic": ["ts_code", "trade_date", "close", "circ_mv", "total_mv", "float_share", "turnover_rate"]}[mode]
FETCH = {"margin_detail": dh.margin_detail_day, "daily_basic": dh.daily_basic_day}[mode]
SUB = {"margin_detail": "md", "daily_basic": "db"}[mode]

days = json.load(open(os.path.join(D, "trade_days.json")))
todo = [d for d in days if not os.path.exists(os.path.join(D, SUB, str(d) + ".csv"))]
print(f"{mode}: total={len(days)} todo={len(todo)}")


def one(d):
    try:
        df = FETCH(str(d))                       # 经 DataHub/EDB 取数
    except Exception as e:
        return d, 0, f"err:{str(e)[:60]}"
    if df is None or df.empty:
        return d, 0, "empty"
    df = df[[c for c in KEEP if c in df.columns]]
    df.to_csv(os.path.join(D, SUB, str(d) + ".csv"), index=False)
    return d, len(df), "ok"


t0 = time.time()
done = 0
fails = []
with ThreadPoolExecutor(max_workers=4) as ex:
    futs = {ex.submit(one, d): d for d in todo}
    for fu in as_completed(futs):
        d, n, st = fu.result()
        done += 1
        if st != "ok":
            fails.append((d, st))
        if done % 20 == 0:
            print(f"  {done}/{len(todo)} elapsed={time.time() - t0:.0f}s last={d} rows={n}")
print(f"{mode} DONE done={done} fails={len(fails)} elapsed={time.time() - t0:.0f}s")
if fails:
    print("FAILS:", fails[:20])