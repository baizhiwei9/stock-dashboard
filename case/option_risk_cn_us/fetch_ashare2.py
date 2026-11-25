# -*- coding: utf-8 -*-
"""A股期权风险监控数据抓取 v2 —— 钉IP + 断点续传
用法: python fetch_ashare2.py <cal|underlying|im|chains> [参数]
"""
import os, sys, json, time
import pandas as pd
import ts_pin
import lib_adapter as LIB  # 取数走 DataHub（用户口径 2026-11-13）

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
CH = os.path.join(DATA, "chains")
os.makedirs(CH, exist_ok=True)
TRADE_DATE = os.environ.get("TRADE_DATE", "20261021")  # 由 run_daily.py 动态注入
HIST_START = "20241001"

pro = ts_pin.init()


def save_json(name, obj):
    p = os.path.join(DATA, name)
    if isinstance(obj, pd.DataFrame):
        obj.to_json(p, orient="records", force_ascii=False)
    else:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
    print("saved", name)


def read_json(name, **kw):
    p = os.path.join(DATA, name)
    return pd.read_json(p, orient="records", dtype=kw.get("dtype"))


# ---------------- cal ----------------
def mode_cal():
    dates = LIB.trade_dates("20250101", TRADE_DATE)          # DataHub 交易日历
    if not dates:                                            # 单源故障才回落 TuShare
        cal = ts_pin.call(pro.trade_cal, exchange="SSE", start_date="20250101",
                          end_date=TRADE_DATE, is_open="1")
        cal = cal.sort_values("cal_date")
        dates = [str(x) for x in cal["cal_date"].tolist()]
    save_json("trade_cal.json", {"all": dates, "last252": dates[-252:]})
    print("open days", len(dates), "last252 head", dates[-252])


# ---------------- underlying ----------------
def mode_underlying():
    etfs = ["510300.SH", "510500.SH", "588000.SH", "159915.SZ",
            "159919.SZ", "159922.SZ"]
    idx = ["000300.SH", "000905.SH", "000852.SH", "000688.SH", "399006.SZ"]
    # DataHub 优先（ETF 走 etf_daily、指数走 index_daily，均带日期参数）
    uh = LIB.underlying_hist(etfs + idx, HIST_START, TRADE_DATE)
    if uh is not None and len(uh):
        uh = uh.copy()
        uh["trade_date"] = uh["trade_date"].astype(str)
        uh = uh.sort_values(["code", "trade_date"])
        spot = [{"code": c, "close": float(g.iloc[-1]["close"]),
                 "trade_date": str(g.iloc[-1]["trade_date"])}
                for c, g in uh.groupby("code")]
        save_json("underlying_hist.json", uh[["code", "kind", "trade_date", "close"]])
        save_json("spot.json", pd.DataFrame(spot))
        for c, g in uh.groupby("code"):
            print(c, g.shape)
        print("underlying via DataHub:", uh.shape)
        return
    rows, spot = [], []
    for code in etfs:
        d = ts_pin.call(pro.fund_daily, ts_code=code, start_date=HIST_START,
                        end_date=TRADE_DATE)
        d = d.sort_values("trade_date")
        d["kind"], d["code"] = "etf", code
        rows.append(d[["code", "kind", "trade_date", "close"]])
        if len(d):
            spot.append({"code": code, "close": float(d.iloc[-1]["close"]),
                         "trade_date": str(d.iloc[-1]["trade_date"])})
        print("etf", code, d.shape)
    for code in idx:
        d = ts_pin.call(pro.index_daily, ts_code=code, start_date=HIST_START,
                        end_date=TRADE_DATE)
        d = d.sort_values("trade_date")
        d["kind"], d["code"] = "index", code
        rows.append(d[["code", "kind", "trade_date", "close"]])
        if len(d):
            spot.append({"code": code, "close": float(d.iloc[-1]["close"]),
                         "trade_date": str(d.iloc[-1]["trade_date"])})
        print("index", code, d.shape)
    save_json("underlying_hist.json", pd.concat(rows, ignore_index=True))
    save_json("spot.json", pd.DataFrame(spot))


# ---------------- IM 期货 ----------------
def mode_im():
    fb = ts_pin.call(pro.fut_basic, exchange="CFFEX", fut_type="2")
    im = fb[fb["symbol"].astype(str).str.startswith("IM")].copy()
    save_json("fut_basic_im.json", im[["ts_code", "symbol", "name", "list_date",
                                       "delist_date"]])
    fr = []
    for code in im["ts_code"].tolist():
        try:
            d = ts_pin.call(pro.fut_daily, ts_code=code, start_date=HIST_START,
                            end_date=TRADE_DATE)
        except Exception as e:
            print("fut_daily", code, "ERR", str(e)[:50]); continue
        if not len(d):
            continue
        d = d.sort_values("trade_date")
        d["code"] = code
        fr.append(d[["code", "trade_date", "close", "settle", "vol", "oi"]])
        print("fut", code, d.shape)
    if fr:
        save_json("fut_im_hist.json", pd.concat(fr, ignore_index=True))


def mode_im_monthly():
    fb = ts_pin.call(pro.fut_basic, exchange="CFFEX", fut_type="1")
    im = fb[fb["symbol"].astype(str).str.match(r"^IM\d{4}$")].copy()
    im = im[im["delist_date"].astype(str) >= "20250801"]
    im.to_json(os.path.join(DATA, "fut_basic_im_month.json"),
               orient="records", force_ascii=False)
    fr = []
    for code in im["ts_code"].tolist():
        try:
            d = ts_pin.call(pro.fut_daily, ts_code=code, start_date=HIST_START,
                            end_date=TRADE_DATE)
        except Exception as e:
            print("fut_daily", code, "ERR", str(e)[:50]); continue
        if not len(d):
            continue
        d = d.sort_values("trade_date")
        d["code"] = code
        fr.append(d[["code", "trade_date", "close", "settle", "vol", "oi"]])
        print("futm", code, d.shape)
    if fr:
        save_json("fut_im_month_hist.json", pd.concat(fr, ignore_index=True))


# ---------------- 期权链历史 (逐日) ----------------
def mode_chains(count=None):
    with open(os.path.join(DATA, "trade_cal.json"), encoding="utf-8") as f:
        cal = json.load(f)
    dates = cal["last252"]
    todo = [d for d in dates if not os.path.exists(os.path.join(CH, d + ".json"))]
    if count:
        todo = todo[:int(count)]
    print("todo this run", len(todo))
    for i, d in enumerate(todo):
        frames = []
        for ex in ("SSE", "SZSE", "CFFEX"):
            try:
                x = ts_pin.call(pro.opt_daily, trade_date=d, exchange=ex)
                if len(x):
                    frames.append(x)
            except Exception as e:
                print("ERR", d, ex, str(e)[:50])
        if frames:
            df = pd.concat(frames, ignore_index=True)
            save_json("chains/" + d + ".json", df)
        else:
            print("empty", d)
        if i % 20 == 0:
            print("progress", i, "/", len(todo), d)


if __name__ == "__main__":
    m = sys.argv[1]
    if m == "cal":
        mode_cal()
    elif m == "underlying":
        mode_underlying()
    elif m == "im":
        mode_im()
    elif m == "imm":
        mode_im_monthly()
    elif m == "chains":
        mode_chains(sys.argv[2] if len(sys.argv) > 2 else None)