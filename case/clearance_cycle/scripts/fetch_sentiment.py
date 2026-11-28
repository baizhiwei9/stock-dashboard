#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""情绪/价格/资金面数据采集：沪深300长历史、成交额、融资余额、涨跌停、QVIX"""
import json
import os
from datetime import date, timedelta

import akshare as ak
import pandas as pd
import requests

import realtime_fill

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "output", "data")
os.makedirs(DATA, exist_ok=True)
UA = "Mozilla/5.0"

# 指数集合：key → (中证代码, 回看交易日, DataHub 兜底回看自然日)
INDEX_SPECS = {
    "hs300": ("000300", 320, 560),
    "zz500": ("000905", 120, 300),
    "zz1000": ("000852", 120, 300),
}


def csindex_hist(index_code, days=320):
    today = date.fromisoformat(realtime_fill.today_cn())
    start = (today - timedelta(days=days * 1.6)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    h = {"User-Agent": UA, "Referer": "https://www.csindex.com.cn/"}
    r = requests.get(
        "https://www.csindex.com.cn/csindex-home/perf/index-perf",
        params={"indexCode": index_code, "startDate": start, "endDate": end},
        headers=h,
        timeout=30,
    )
    js = r.json()
    df = pd.DataFrame(js["data"])
    df["date"] = pd.to_datetime(df["tradeDate"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
    for c in ["close", "tradingValue", "changePct", "consNumber"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df[["date", "close", "tradingValue", "changePct"]].rename(
        columns={"tradingValue": "amount_yi", "changePct": "chg_pct"}
    ).sort_values("date")


def margin_hist():
    df = ak.stock_margin_account_info()
    df["date"] = pd.to_datetime(df["日期"]).dt.strftime("%Y-%m-%d")
    df["fin_bal_yi"] = pd.to_numeric(df["融资余额"], errors="coerce")
    df["ratio"] = pd.to_numeric(df["平均维持担保比例"], errors="coerce")
    return df[["date", "fin_bal_yi", "ratio"]].sort_values("date")


def qvix():
    df = ak.index_option_300etf_qvix()
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    return df[["date", "close"]].dropna().sort_values("date").tail(300)


def breadth_today():
    df = ak.stock_zh_a_spot()
    df["涨跌幅"] = pd.to_numeric(df["涨跌幅"], errors="coerce")
    up = int((df["涨跌幅"] > 0).sum())
    down = int((df["涨跌幅"] < 0).sum())
    limit_up = int((df["涨跌幅"] >= 9.8).sum())
    limit_down = int((df["涨跌幅"] <= -9.8).sum())
    return {
        "total": int(len(df)),
        "up": up,
        "down": down,
        "limit_up": limit_up,
        "limit_down": limit_down,
        "median_chg": round(float(df["涨跌幅"].median()), 2),
        "amount_yi_all": round(float(pd.to_numeric(df["成交额"], errors="coerce").sum()) / 1e8, 1),
    }


def build():
    out = {}
    today = realtime_fill.today_cn()

    # 1) 历史序列：中证指数官网为主；整条不可用时退 DataHub 日线（含当日实时）。
    frames = {}
    for key, (code, days, hub_lookback) in INDEX_SPECS.items():
        df = None
        try:
            df = csindex_hist(code, days=days)
        except Exception as e:  # noqa: BLE001
            print(f"[sentiment] csindex {code} 失败，改用 DataHub 日线: {e}", flush=True)
        if df is None or not len(df):
            df = realtime_fill.fetch_index_history(f"{code}.SH", lookback=hub_lookback)
        if df is None or not len(df):
            raise RuntimeError(f"指数 {code} 两条数据源都取不到")
        frames[key] = df

    # 2) 日线缺当日（14:50 盘中常态）→ 用实时快照补一根；只补确认是今天的。
    missing = {
        key: f"{INDEX_SPECS[key][0]}.SH"
        for key, df in frames.items()
        if str(df["date"].iloc[-1])[:10] < today
    }
    if missing:
        bars = realtime_fill.fetch_index_today(missing)
        for key, bar in bars.items():
            frames[key] = realtime_fill.append_bar(
                frames[key], bar, ["close", "amount_yi", "chg_pct"])
        out["realtime_fill"] = bars
    for key, df in frames.items():
        out[key] = df.to_dict("records")

    try:
        out["margin"] = margin_hist().to_dict("records")
    except Exception as e:  # noqa: BLE001
        out["margin"] = []
        out["margin_err"] = str(e)[:120]
    try:
        out["qvix"] = qvix().to_dict("records")
    except Exception as e:  # noqa: BLE001
        out["qvix"] = []
        out["qvix_err"] = str(e)[:120]
    try:
        out["breadth"] = breadth_today()
    except Exception as e:  # noqa: BLE001
        out["breadth"] = {}
        out["breadth_err"] = str(e)[:120]
    with open(os.path.join(DATA, "sentiment.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    return out


if __name__ == "__main__":
    r = build()
    print("hs300", len(r["hs300"]), r["hs300"][-1] if r["hs300"] else None)
    print("margin", len(r["margin"]), r["margin"][-1] if r["margin"] else r.get("margin_err"))
    print("qvix", len(r["qvix"]), r["qvix"][-1] if r["qvix"] else r.get("qvix_err"))
    print("breadth", r.get("breadth") or r.get("breadth_err"))
