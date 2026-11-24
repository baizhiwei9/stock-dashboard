# -*- coding: utf-8 -*-
"""全A 日线+复权因子 按交易日窗口抓取（带断点续传）。

用法: python3 fetch_all.py <start_YYYYMMDD> <end_YYYYMMDD> [--max-batch N]
"""
import os
import sys
import time
import datetime as dt

WORK = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5e9bb55354ffe9Rg1AoYf1k3lwu")
import numpy as np
from ts_http import fetch

CACHE = os.path.join(WORK, "cache_ma")
os.makedirs(CACHE, exist_ok=True)


def weekdays(start, end):
    out = []
    d = dt.datetime.strptime(start, "%Y%m%d").date()
    e = dt.datetime.strptime(end, "%Y%m%d").date()
    while d <= e:
        if d.weekday() < 5:
            out.append(d.strftime("%Y%m%d"))
        d += dt.timedelta(days=1)
    return out


def save_date(date):
    p = os.path.join(CACHE, "D_%s.npz" % date)
    if os.path.exists(p):
        return "cached"
    f, items = fetch("daily", {"trade_date": date})
    if not items:
        np.savez_compressed(p, empty=np.array([1]))
        return "nontrading"
    codes = np.array([r[0] for r in items], dtype=object)
    close = np.array([r[5] if r[5] is not None else np.nan for r in items], dtype=np.float64)
    amount = np.array([r[10] if r[10] is not None else np.nan for r in items], dtype=np.float64)
    vol = np.array([r[9] if r[9] is not None else np.nan for r in items], dtype=np.float64)
    pct = np.array([r[8] if r[8] is not None else np.nan for r in items], dtype=np.float64)
    fa, ia = fetch("adj_factor", {"trade_date": date})
    amap = {r[0]: r[2] for r in ia} if ia else {}
    adj = np.array([amap.get(c, np.nan) for c in codes], dtype=np.float64)
    np.savez_compressed(p, codes=codes, close=close, amount=amount, vol=vol, pct=pct, adj=adj)
    return "ok"


def main():
    start, end = sys.argv[1], sys.argv[2]
    maxb = None
    if "--max-batch" in sys.argv:
        maxb = int(sys.argv[sys.argv.index("--max-batch") + 1])
    dates = weekdays(start, end)
    todo = [d for d in dates if not os.path.exists(os.path.join(CACHE, "D_%s.npz" % d))]
    print("candidates=%d todo=%d" % (len(dates), len(todo)), flush=True)
    if maxb:
        todo = todo[:maxb]
    t0 = time.time()
    ok = nt = 0
    from concurrent.futures import ThreadPoolExecutor
    lock = __import__("threading").Lock()
    done = [0]

    def work(d):
        try:
            r = save_date(d)
        except Exception as e:  # noqa: BLE001
            r = "fail:%s" % e
        with lock:
            done[0] += 1
            if done[0] % 20 == 0:
                el = time.time() - t0
                print("  %d/%d %.0fs %.2f/s %s" % (done[0], len(todo), el, done[0] / max(el, 1e-9), r), flush=True)
        return r

    with ThreadPoolExecutor(max_workers=6) as ex:
        res = list(ex.map(work, todo))
    for r in res:
        if r == "ok":
            ok += 1
        elif r == "nontrading":
            nt += 1
        elif r == "cached":
            ok += 1
        else:
            print("FAIL", r)
    print("done ok=%d nontrading=%d total=%.0fs" % (ok, nt, time.time() - t0))


if __name__ == "__main__":
    main()