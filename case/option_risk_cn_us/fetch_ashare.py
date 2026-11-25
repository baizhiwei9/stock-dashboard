# -*- coding: utf-8 -*-
"""A股期权风险监控 - 原始数据抓取 (tushare + akshare + CBOE)
输出统一落盘到 ./data/
"""
import os, json
import pandas as pd
from ts_config import init_tushare

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(OUT, exist_ok=True)
TRADE_DATE = "20261021"      # 数据日 (2026-10-21 收盘)
START = "20251001"           # 覆盖 252 交易日

pro = init_tushare()

def save_df(name, df):
    p = os.path.join(OUT, name)
    df.to_json(p, orient="records", force_ascii=False)
    print("saved", name, df.shape)

# ---------- 1. 期权合约基础信息 ----------
for ex, fn in [("SSE","basic_sse"), ("SZSE","basic_szse"), ("CFFEX","basic_cffex")]:
    try:
        b = pro.opt_basic(exchange=ex)
        save_df(fn + ".json", b)
    except Exception as e:
        print("opt_basic", ex, "ERR", e)

# ---------- 2. 当日全市场期权行情 ----------
for ex, fn in [("SSE","chain_sse"), ("SZSE","chain_szse"), ("CFFEX","chain_cffex")]:
    try:
        d = pro.opt_daily(trade_date=TRADE_DATE, exchange=ex)
        save_df(fn + ".json", d)
    except Exception as e:
        print("opt_daily", ex, "ERR", e)

# ---------- 3. 标的行情 (ETF 收盘 + 历史, 用于 HV20) ----------
etfs = ["510300.SH","510500.SH","588000.SH","159915.SZ","159919.SZ","159922.SZ"]
idx  = ["000300.SH","000905.SH","000852.SH","000688.SH","399006.SZ"]
rows = []
for code in etfs:
    try:
        d = pro.fund_daily(ts_code=code, start_date=START, end_date=TRADE_DATE)
        d["kind"] = "etf"; d["code"] = code
        rows.append(d[["code","kind","trade_date","close","vol"]])
    except Exception as e:
        print("fund_daily", code, "ERR", e)
for code in idx:
    try:
        d = pro.index_daily(ts_code=code, start_date=START, end_date=TRADE_DATE)
        d["kind"] = "index"; d["code"] = code
        rows.append(d[["code","kind","trade_date","close","vol"]])
    except Exception as e:
        print("index_daily", code, "ERR", e)
if rows:
    save_df("underlying_hist.json", pd.concat(rows, ignore_index=True))

# 当日标的收盘快照
spot = []
for code in etfs + idx:
    try:
        if code in etfs:
            d = pro.fund_daily(ts_code=code, trade_date=TRADE_DATE)
        else:
            d = pro.index_daily(ts_code=code, trade_date=TRADE_DATE)
        if not d.empty:
            spot.append({"code": code, "close": float(d.iloc[0]["close"])})
    except Exception as e:
        print("spot", code, "ERR", e)
save_df("spot.json", pd.DataFrame(spot))

# ---------- 4. 期指 IM 基差 ----------
try:
    fb = pro.fut_basic(exchange="CFFEX", fut_type="2")
    im = fb[fb["symbol"].astype(str).str.startswith("IM")]
    save_df("fut_basic_im.json", im[["ts_code","symbol","name","list_date","delist_date"]])
except Exception as e:
    print("fut_basic ERR", e)
    im = None
if im is not None:
    fr = []
    for code in im["ts_code"].tolist():
        try:
            d = pro.fut_daily(ts_code=code, start_date=START, end_date=TRADE_DATE)
            d["code"] = code
            fr.append(d[["code","trade_date","close","settle","vol","oi"]])
        except Exception as e:
            pass
    if fr:
        save_df("fut_im_hist.json", pd.concat(fr, ignore_index=True))

print("DONE fetch_ashare")