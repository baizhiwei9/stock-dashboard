# -*- coding: utf-8 -*-
"""自建概念指数：黄酒 / 光模块（用户点名，同花顺无独立指数）
方法：成分股日线（代理通道，unadjusted close）→ 等权日收益率 → 基准1000 合成收盘指数
输出：追加/覆盖到 report_run/concepts/concepts.json，与既有概念同构
      [{trade_date: "YYYYMMDD", close: float}, ...]
"""
import json
import os
import sys
import time

import requests

BASE = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace"
sys.path.insert(0, os.path.join(BASE, "ses_gffe5f86bdd5bfffeXHo4SFOaOa2sWG", "pylibs"))
from ts_config import TS_TOKEN, TS_HTTP_URL

CONCEPTS_JSON = os.path.join(BASE, "report_run", "concepts", "concepts.json")
START = "20250101"
END = "20260928"

CUSTOM = {
    "黄酒": ["600059.SH", "601579.SH", "600616.SH"],           # 古越龙山/会稽山/金枫酒业
    "光模块": ["300308.SZ", "300502.SZ", "300394.SZ",           # 中际旭创/新易盛/天孚通信
               "002281.SZ", "300570.SZ", "603083.SH"],          # 光迅科技/太辰光/剑桥科技
}

S = requests.Session()


def fetch_close(code):
    r = S.post(TS_HTTP_URL, json={"api_name": "daily", "token": TS_TOKEN,
                                  "params": {"ts_code": code, "start_date": START,
                                             "end_date": END}}, timeout=25)
    j = r.json()
    items = (j.get("data") or {}).get("items")
    out = {}
    if items:
        for it in items:  # ts_code, trade_date, open, high, low, close, ...
            out[str(it[1])] = float(it[5])
    return out


def build_index(codes):
    """等权日收益率合成指数，基准1000。返回 {YYYYMMDD: close} 按日期升序。"""
    series = {}
    for c in codes:
        d = fetch_close(c)
        if not d:
            print(f"  [warn] {c} 无数据")
        for k, v in d.items():
            series.setdefault(k, []).append(v)
        time.sleep(0.8)
    dates = sorted(series)
    # 需要双侧收益：从第二个有效日开始
    close = {}
    prev = None
    for d in dates:
        vals = [v for v in series[d] if v and v > 0]
        if not vals:
            continue
        cur = sum(vals) / len(vals)  # 等权（当日有数据的成分股）
        if prev is not None and cur > 0 and prev > 0:
            # 累积收益率序列：先记收益率再转指数
            pass
        prev = cur
    # 用日收益率等权序列
    rets = []
    prev = None
    for d in dates:
        vals = [v for v in series[d] if v and v > 0]
        if not vals:
            continue
        cur = sum(vals) / len(vals)
        if prev is not None and prev > 0:
            rets.append((d, cur / prev - 1))
        prev = cur
    idx = 1000.0
    out = {}
    for d, r in rets:
        idx *= (1 + r)
        out[d] = round(idx, 4)
    return out


def main():
    os.makedirs(os.path.dirname(CONCEPTS_JSON), exist_ok=True)
    data = json.load(open(CONCEPTS_JSON, encoding="utf-8"))
    for name, codes in CUSTOM.items():
        idx = build_index(codes)
        rows = [{"trade_date": d, "close": c} for d, c in sorted(idx.items())]
        data[name] = rows
        if rows:
            print(f"[ok] {name}: {len(rows)} rows last={rows[-1]['trade_date']} "
                  f"close={rows[-1]['close']}")
    with open(CONCEPTS_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print("saved concepts:", list(data.keys()))


if __name__ == "__main__":
    main()