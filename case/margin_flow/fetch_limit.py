# -*- coding: utf-8 -*-
"""补取涨跌停家数（一次性/补历史脚本）

数据经 DataHub/EDB 数据集 ``CN.MARKET.LIMIT.COUNTS`` 取得，本脚本不再直连数据源。
"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dh  # noqa: E402

import pandas as pd  # noqa: E402

D = os.path.join(HERE, "data")
days = json.load(open(os.path.join(D, "trade_days.json")))
out = os.path.join(D, "limit_counts.csv")
have = set()
if os.path.exists(out):
    have = set(pd.read_csv(out).trade_date.astype(str))
todo = [d for d in days if d not in have]
print("todo", len(todo))


def one(d):
    try:
        r = dh.limit_counts_day(str(d))
    except Exception as e:
        print("  ERR", d, str(e)[:100])
        return {"trade_date": int(d), "up_limit": 0, "down_limit": 0, "broken": 0}
    return {"trade_date": int(d), "up_limit": int(r["up_limit"]),
            "down_limit": int(r["down_limit"]), "broken": int(r["broken"])}


res = []
t0 = time.time()
with ThreadPoolExecutor(max_workers=4) as ex:
    for k, r in enumerate(ex.map(one, todo)):
        res.append(r)
        if (k + 1) % 30 == 0:
            print(f"  {k + 1}/{len(todo)} t={time.time() - t0:.0f}s")
nd = pd.DataFrame(res)
if os.path.exists(out) and not nd.empty:
    nd = pd.concat([pd.read_csv(out), nd], ignore_index=True)
if not nd.empty:
    nd = nd.drop_duplicates(subset=["trade_date"], keep="last").sort_values("trade_date")
    nd.to_csv(out, index=False)
print("limit_counts rows", len(nd))
print(nd.tail(4).to_string())