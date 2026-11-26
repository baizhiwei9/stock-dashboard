#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""liqeq —— 五宽基日线（流动性周报的股市那一半）

原实现（``liq_credit_weekly.load_eq``）用 ``tushare_init.get_pro()`` 直连
``pro.index_daily`` 抓 420 天，缓存到 ``.cache/cn_eq_index.json``。

迁移后：走 DataHub EDB 数据集 ``CN.MARKET.INDEX.DAILY``（Tushare 通道，
代理/直连降级与限流都在 DataHub 内部）。返回结构与原 json 完全一致，
调用方 ``eq_stat() / eq_from_high()`` 一行都不用改：

    {code: {"name":..., "dates":[YYYY-MM-DD...], "close":[...],
            "pct":[涨跌幅%...], "amount":[千元...]}}
"""
from __future__ import annotations

import datetime as dt
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_SHARED = os.path.join(os.path.dirname(HERE), "wide_ens_lib")
for _p in (HERE, _SHARED):
    if _p not in sys.path:
        sys.path.insert(0, _p)

NAME = {"000300.SH": "沪深300", "000905.SH": "中证500", "000852.SH": "中证1000",
        "399006.SZ": "创业板指", "000688.SH": "科创50"}

# DataHub EDB 的交易日日期是 YYYYMMDD 字符串，原 json 用 YYYY-MM-DD（strptime 过）
def _d10(s: str) -> str:
    s = str(s).strip()
    if len(s) == 8 and s.isdigit():
        return "%s-%s-%s" % (s[:4], s[4:6], s[6:])
    return s[:10]


def load_index_daily(codes=None, days: int = 420) -> dict:
    """→ {code: {...}}；任何一只失败只跳过它，不整体失败。"""
    codes = codes or list(NAME)
    from datahub_fetch import hub
    dh = hub()
    end = dt.date.today().strftime("%Y%m%d")
    start = (dt.date.today() - dt.timedelta(days=days)).strftime("%Y%m%d")
    try:
        res = dh.edb.get_dataset_result("CN.MARKET.INDEX.DAILY", codes=codes,
                                        start_date=start, end_date=end)
    except Exception as e:                                   # noqa: BLE001
        raise RuntimeError("指数日线取数失败（DataHub）：%s" % str(e)[:200])
    if not res.get("ok"):
        raise RuntimeError("指数日线取数失败：%s" % (res.get("error") or "未知"))
    df = res.get("data")
    if df is None or len(df) == 0:
        return {}

    out = {}
    df = df.copy()
    df["trade_date"] = df["trade_date"].astype(str)
    for code in codes:
        sub = df[df["ts_code"].astype(str) == code].sort_values("trade_date")
        if not len(sub):
            continue
        close = [float(x) for x in sub["close"]]
        pct = []
        for i, c in enumerate(close):
            pct.append(0.0 if i == 0 else (c / close[i - 1] - 1) * 100)
        out[code] = {"name": NAME.get(code, code),
                     "dates": [_d10(x) for x in sub["trade_date"]],
                     "close": close,
                     "pct": [round(x, 4) for x in pct],
                     "amount": [float(x) for x in sub.get("amount", [0] * len(sub))]}
    return out


if __name__ == "__main__":
    d = load_index_daily()
    for k, v in d.items():
        print("%-11s %s  %d 点  %s ~ %s  末收 %.2f"
              % (k, v["name"], len(v["close"]), v["dates"][0], v["dates"][-1],
                 v["close"][-1]))
