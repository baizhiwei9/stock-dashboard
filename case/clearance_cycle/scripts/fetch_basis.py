#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""股指期货基差采集：IF/IC/IM 各合约收盘 + 现货指数 → 年化基差率

基差定义：基差 = 现货 - 期货（正=期货贴水）
年化基差率 = (现货 - 期货) / 现货 × 365 / 剩余日历天数 × 100
到期日 = 合约月第三个周五

历史口径：为算「基差分位数」，需要 250 个交易日的连续序列，
所以除了未来 6 个月的在挂合约，还要回看 15 个月的已到期合约。
"""
import json
import os
from datetime import date, datetime, timedelta

import akshare as ak
import pandas as pd
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "output", "data")
os.makedirs(DATA, exist_ok=True)

SPOT = {"IF": "000300", "IC": "000905", "IM": "000852"}
UA = "Mozilla/5.0"

HIST_DAYS = 280      # 保留的交易日数（约 13 个月，够算 250 日分位）
BACK_MONTHS = 15     # 回看的合约月数
FWD_MONTHS = 6       # 前瞻的合约月数


def third_friday(year, month):
    d = date(year, month, 1)
    fridays = [date(year, month, day) for day in range(1, 32) if _valid(year, month, day) and date(year, month, day).weekday() == 4]
    return fridays[2]


def _valid(y, m, d):
    try:
        date(y, m, d)
        return True
    except ValueError:
        return False


def contract_expiry(symbol):
    """IF2612 → 2026-12 第三个周五"""
    code = symbol[2:]
    yy, mm = int(code[:2]), int(code[2:4])
    return third_friday(2000 + yy, mm)


def fetch_spot(index_code, start, end):
    """中证指数官方行情（含成交额，亿元）"""
    h = {"User-Agent": UA, "Referer": "https://www.csindex.com.cn/"}
    url = "https://www.csindex.com.cn/csindex-home/perf/index-perf"
    r = requests.get(url, params={"indexCode": index_code, "startDate": start, "endDate": end}, headers=h, timeout=25)
    js = r.json()
    if js.get("code") != "200":
        raise RuntimeError(f"csindex {index_code} {js}")
    df = pd.DataFrame(js["data"])
    df["date"] = pd.to_datetime(df["tradeDate"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["amount_yi"] = pd.to_numeric(df["tradingValue"], errors="coerce")
    return df[["date", "close", "amount_yi"]].sort_values("date")


def _month_seq(back=BACK_MONTHS, fwd=FWD_MONTHS):
    """从 back 个月前到 fwd 个月后的合约月序列"""
    today = date.today()
    y, m = today.year, today.month
    out = []
    for k in range(-back, fwd + 1):
        mm = m + k
        yy = y + (mm - 1) // 12
        mm = (mm - 1) % 12 + 1
        out.append((yy, mm))
    return out


def fetch_futures(variety, days=400):
    """取该品种全部合约日线（含已到期，新浪源按合约代码可回溯）"""
    out = {}
    for yy, mm in _month_seq():
        sym = f"{variety}{str(yy)[2:]}{mm:02d}"
        try:
            df = ak.futures_zh_daily_sina(symbol=sym)
        except Exception:  # noqa: BLE001
            continue
        if df is None or not len(df):
            continue
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
        out[sym] = df[["date", "close", "volume"]].tail(days)
    return out


def build(days=HIST_DAYS):
    start = (date.today() - timedelta(days=int(days * 1.7) + 60)).strftime("%Y%m%d")
    end = date.today().strftime("%Y%m%d")
    result = {"as_of": None, "varieties": {}, "spot": {}, "history": {}}

    for v, idx in SPOT.items():
        spot = fetch_spot(idx, start, end)
        result["spot"][v] = spot.tail(days).to_dict("records")
        futs = fetch_futures(v)
        rows = []
        for sym, df in futs.items():
            exp = contract_expiry(sym)
            for _, r in df.iterrows():
                d = datetime.strptime(r["date"], "%Y-%m-%d").date()
                left = (exp - d).days
                if left <= 0:
                    continue
                sp = spot.loc[spot["date"] == r["date"], "close"]
                if not len(sp):
                    continue
                s = float(sp.iloc[0])
                basis = s - float(r["close"])
                rows.append({
                    "date": r["date"], "contract": sym, "spot": round(s, 2),
                    "fut": round(float(r["close"]), 2), "basis": round(basis, 2),
                    "days_left": left, "annual_pct": round(basis / s * 365 / left * 100, 3),
                    "volume": float(r["volume"]),
                })
        hist = pd.DataFrame(rows)
        if not len(hist):
            continue
        # 主口径：持仓量最大 → 用成交量代理；按日选当季合约（月份∈{3,6,9,12}且最近）
        hist["is_quarter"] = hist["contract"].str[4:6].isin(["03", "06", "09", "12"])
        result["history"][v] = hist.to_dict("records")
        # 当日快照：全部在挂合约
        last = hist["date"].max()
        snap = hist[hist["date"] == last].sort_values("contract")
        result["varieties"][v] = {
            "date": last,
            "contracts": snap.to_dict("records"),
            "main": snap.loc[snap["annual_pct"].abs().idxmax()].to_dict() if len(snap) else None,
        }
        result["as_of"] = last

    with open(os.path.join(DATA, "basis.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    return result


if __name__ == "__main__":
    r = build()
    print("as_of", r["as_of"])
    for v, s in r["varieties"].items():
        print(f"\n== {v} 现货 {r['spot'][v][-1]['close'] if r['spot'][v] else 'NA'}  历史 {len(r['history'][v])} 行")
        for c in s["contracts"]:
            print(f"  {c['contract']} 期{c['fut']:>9.1f} 基差{c['basis']:>8.1f} 剩余{c['days_left']:>4}天 年化{c['annual_pct']:>7.2f}%")
