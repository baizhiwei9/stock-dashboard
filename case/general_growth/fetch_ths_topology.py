# -*- coding: utf-8 -*-
"""同花顺概念指数拓扑：按交易日批量取全概念收盘价 → 60 日收益相关矩阵 → 邻居数（相关>0.8 的个数）

与申万三级口径一致（W=60，阈值 0.8，≥4 判聚集）。
输出：data/ths_topology.json（每个概念 code -> neighbors/判聚集或扩散/60日收益）
用法: python3 fetch_ths_topology.py [--n 80]
"""
import argparse
import json
import os
import sys
import time

import numpy as np
import requests

sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/pylibs")
sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f86bdd5bfffeXHo4SFOaOa2sWG/pylibs")
import ts_config  # noqa

S = requests.Session()
D = "data"


def call(api, params, retry=4):
    last = ""
    for i in range(retry + 1):
        try:
            time.sleep(0.7)
            r = S.post(ts_config.TS_HTTP_URL, json={"api_name": api, "token": ts_config.TS_TOKEN,
                                                    "params": params, "fields": ""}, timeout=60)
            j = r.json()
            if j.get("code") == 0:
                return j["data"]
            last = "code=%s msg=%s" % (j.get("code"), (j.get("msg") or "")[:50])
            if "过快" in (j.get("msg") or "") or "超限" in (j.get("msg") or ""):
                time.sleep(12 + 8 * i)
                continue
            break
        except Exception as e:  # noqa
            last = repr(e)[:100]
            time.sleep(3 + 3 * i)
    print("[warn]", api, params, last)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=80)
    a = ap.parse_args()

    ths = json.load(open(os.path.join(D, "fm_ths_index.json"), encoding="utf-8"))
    want = {x["ts_code"] for x in ths}

    d0 = call("index_daily", {"ts_code": "000001.SH", "start_date": "20260501",
                              "end_date": "20261231"})
    it = d0.get("items", []) if d0 else []
    f = d0.get("fields", []) if d0 else []
    rows = [dict(zip(f, r)) for r in it]
    rows.sort(key=lambda x: x["trade_date"])
    dates = [r["trade_date"] for r in rows][-a.n:]
    print("交易日窗口:", dates[0], "→", dates[-1], "共", len(dates))

    cache_p = os.path.join(D, "ths_daily_cache.json")
    cache = json.load(open(cache_p, encoding="utf-8")) if os.path.exists(cache_p) else {}
    for i, dt in enumerate(dates, 1):
        if dt in cache and cache[dt]:
            continue
        d = call("ths_daily", {"trade_date": dt})
        if not d:
            continue
        rec = {}
        for x in d["items"]:
            r = dict(zip(d["fields"], x))
            if r["ts_code"] in want:
                rec[r["ts_code"]] = r["close"]
        cache[dt] = rec
        if i % 10 == 0:
            json.dump(cache, open(cache_p, "w", encoding="utf-8"), ensure_ascii=False)
            print("  概念日线进度 %d/%d" % (i, len(dates)))
    json.dump(cache, open(cache_p, "w", encoding="utf-8"), ensure_ascii=False)

    dates = [d for d in dates if cache.get(d)]
    codes = sorted(want)
    px = {}
    for c in codes:
        arr = [cache[d].get(c) for d in dates]
        if sum(1 for v in arr if v) >= 40:
            px[c] = arr

    # 收益矩阵（按各自可用日对齐：用全窗口，缺失前值填充）
    def rets(arr):
        out = []
        for i in range(1, len(arr)):
            a, b = arr[i - 1], arr[i]
            out.append((b / a - 1) if (a and b) else 0.0)
        return out

    cds = sorted(px)
    M = np.array([rets(px[c]) for c in cds])  # 每行一只
    if M.size == 0:
        print("无足够数据"); return
    M = M[:, -60:]
    # 标准化后求相关
    Z = (M - M.mean(axis=1, keepdims=True))
    sd = Z.std(axis=1, keepdims=True)
    sd[sd == 0] = 1
    Z = Z / sd
    C = np.corrcoef(Z)
    out = {}
    for i, c in enumerate(cds):
        nb = int(((C[i] > 0.8).sum()) - 1)
        out[c] = {"neighbors": nb, "judge": "聚集" if nb >= 4 else "扩散"}
    json.dump({"asof": dates[-1], "window": len(dates), "rule": "corr>0.8 邻居数≥4聚集/≤3扩散",
               "n": len(cds), "topology": out},
              open(os.path.join(D, "ths_topology.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    arr = sorted(out.items(), key=lambda kv: -kv[1]["neighbors"])
    print("概念拓扑：%d 个；邻居数多者前 10：" % len(out))
    n2c = {x["ts_code"]: x["name"] for x in ths}
    for c, v in arr[:10]:
        print("   %-14s %3d %s" % (n2c.get(c, c), v["neighbors"], v["judge"]))
    for nm in ["减速器", "人形机器人", "机器人概念", "机器视觉", "传感器"]:
        for x in ths:
            if x["name"] == nm and x["ts_code"] in out:
                v = out[x["ts_code"]]
                print("  [%s] %s 邻居数 %d %s" % (nm, x["ts_code"], v["neighbors"], v["judge"]))


if __name__ == "__main__":
    main()