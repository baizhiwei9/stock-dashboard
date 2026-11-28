#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重建被动化率历史：上交所按日回溯宽基ETF份额（深市用当前值固定，净值用最新）

输出 output/data/passive_history.csv：date, wide_yi, hs300_close
"""
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pandas as pd
import requests

from fetch_etf_scale import DATA, classify, fetch_nav, fetch_sse, fetch_szse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"


def sse_page(d, p):
    h = {"User-Agent": UA, "Referer": "http://www.sse.com.cn/"}
    for _ in range(3):
        try:
            r = requests.get(
                "http://query.sse.com.cn/commonQuery.do",
                params={
                    "sqlId": "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L",
                    "isPagination": "true",
                    "pageHelp.pageSize": "100",
                    "pageHelp.pageNo": str(p),
                    "pageHelp.beginPage": str(p),
                    "pageHelp.cacheSize": "1",
                    "pageHelp.endPage": str(p),
                    "STAT_DATE": d,
                },
                headers=h,
                timeout=25,
            )
            return r.json()["pageHelp"]["data"]
        except Exception:  # noqa: BLE001
            continue
    return []


def sse_day(d, pages=10):
    with ThreadPoolExecutor(max_workers=5) as ex:
        res = list(ex.map(lambda p: sse_page(d, p), range(1, pages + 1)))
    rows = [x for page in res for x in page]
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["code"] = df["SEC_CODE"].astype(str).str.zfill(6)
    df["name"] = df["SEC_NAME"]
    df["vol"] = pd.to_numeric(df["TOT_VOL"], errors="coerce")
    return df[["code", "name", "vol"]]


def main(trade_dates):
    navmap, _ = fetch_nav()
    szse = fetch_szse()
    szse["nav"] = szse["code"].map(lambda c: navmap.get(str(c).zfill(6)))
    szse["scale"] = szse["vol"] * szse["nav"] / 1e4
    szse["cat"] = szse["name"].map(classify)
    sz_wide = float(szse.dropna(subset=["cat", "scale"])["scale"].sum())
    print("深市宽基(亿, 固定)", round(sz_wide, 1))

    hs = pd.DataFrame(json.load(open(os.path.join(DATA, "sentiment.json"), encoding="utf-8"))["hs300"])
    hs_close = dict(zip(hs["date"].astype(str), pd.to_numeric(hs["close"], errors="coerce")))

    out = []
    for d in trade_dates:
        df = sse_day(d.replace("-", ""))
        if df is None:
            print("miss", d)
            continue
        df["nav"] = df["code"].map(lambda c: navmap.get(str(c).zfill(6)))
        df["scale"] = df["vol"] * df["nav"] / 1e4
        df["cat"] = df["name"].map(classify)
        sh_wide = float(df.dropna(subset=["cat", "scale"])["scale"].sum())
        out.append({"date": d, "wide_yi": round(sh_wide + sz_wide, 1), "hs300_close": hs_close.get(d)})
        print(d, round(sh_wide + sz_wide, 1), hs_close.get(d))
    pd.DataFrame(out).to_csv(os.path.join(DATA, "passive_history.csv"), index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        dates = sys.argv[1].split(",")
    else:
        hs = pd.DataFrame(json.load(open(os.path.join(DATA, "sentiment.json"), encoding="utf-8"))["hs300"])
        dates = list(hs["date"].astype(str).tail(25))
    main(dates)
