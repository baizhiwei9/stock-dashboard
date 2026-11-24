# -*- coding: utf-8 -*-
"""A股因子旋转周报 - 数据获取脚本 v2（限流适配）
拉取：交易日历、指数成分、Tushare daily(260交易日)/daily_basic(65交易日)、东财业绩报表、盈利预测
间隔3.5s，失败退避重试，落盘 raw/。
"""
import os, sys, time, json, importlib.util
import pandas as pd

RAW = os.path.dirname(os.path.abspath(__file__))
def log(msg):
    print(msg, flush=True)

spec = importlib.util.spec_from_file_location(
    "ts_config",
    "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f86bdd5bfffeXHo4SFOaOa2sWG/pylibs/ts_config.py")
ts_config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ts_config)
pro = ts_config.init_tushare()
import akshare as ak

# ---------- 1. 交易日历 ----------
cal = pd.read_csv(os.path.join(RAW, "trade_cal.csv"))
cal["trade_date"] = pd.to_datetime(cal["trade_date"])
# 截断口径：执行日之前「最近一个完整交易周」的最后交易日（周五）。
# 动态计算：执行日往回取最近的那个周五（2026-11-08 -> 2026-11-06）；若该周五为节假日则回退到之前最近的交易日。
_today = pd.Timestamp.today().normalize()
_fri = _today - pd.Timedelta(days=(((_today.weekday() - 4) % 7) or 7))
_target = cal[cal["trade_date"] <= _fri]["trade_date"].max()
log(f"[cal] exec_day={_today.strftime('%Y-%m-%d')} last_week_friday={_fri.strftime('%Y-%m-%d')} cutoff={_target.strftime('%Y-%m-%d')}")
recent = cal[cal["trade_date"] <= _target]["trade_date"].dt.strftime("%Y%m%d").tolist()
log(f"[cal] recent_days={len(recent)} last={recent[-1]}")
json.dump(recent, open(os.path.join(RAW, "trading_days.json"), "w"))

# ---------- 2. Tushare ----------
def fetch_batch(kind, days, fname, sleep=3.5):
    fp = os.path.join(RAW, fname)
    existing_dates = set()
    if os.path.exists(fp) and os.path.getsize(fp) > 10000:
        try:
            old = pd.read_csv(fp, dtype={"ts_code": str, "trade_date": str}, usecols=["trade_date"])
            existing_dates = set(old["trade_date"].astype(str).unique())
        except Exception:
            pass
    days = [d for d in days if d not in existing_dates]
    if not days:
        log(f"[{kind}] up-to-date {fname}")
        return
    log(f"[{kind}] incremental fetch {len(days)} new days for {fname}")
    chunks = []
    fails = []
    for i, d in enumerate(days):
        got = None
        for attempt in range(5):
            try:
                df = pro.daily(trade_date=d) if kind == "daily" else pro.daily_basic(trade_date=d)
                if df is not None and len(df) > 0:
                    got = df
                else:
                    log(f"[{kind}] {d} EMPTY (not trade day?)")
                break
            except Exception as e:
                wait = 5 * (attempt + 1)
                log(f"[{kind}] {d} attempt{attempt} ERR {str(e)[:70]} wait={wait}s")
                time.sleep(wait)
        if got is not None:
            chunks.append(got)
        else:
            fails.append(d)
        if (i + 1) % 10 == 0:
            _flush(chunks, fp)
            log(f"[{kind}] {i+1}/{len(days)} done fails={len(fails)}")
        time.sleep(sleep)
    _flush(chunks, fp)
    log(f"[{kind}] DONE days_ok={len(chunks)} fails={fails} -> {fname}")

def _flush(chunks, fp):
    if not chunks:
        return
    tmp = pd.concat(chunks, ignore_index=True)
    tmp = tmp.drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
    if os.path.exists(fp):
        old = pd.read_csv(fp, dtype={"ts_code": str, "trade_date": str})
        tmp = pd.concat([old, tmp], ignore_index=True)
        tmp = tmp.drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
    tmp.to_csv(fp, index=False)
    log(f"  flush {fp} rows={len(tmp)}")

daily_days = recent[-260:]
basic_days = recent[-65:]
fetch_batch("daily", daily_days, "daily_260d.csv")
fetch_batch("basic", basic_days, "daily_basic_65d.csv")

# ---------- 3. 东财业绩报表（多报告期） ----------
for period, tag in [("20250930", "2025Q3"), ("20251231", "2025A"), ("20260331", "2026Q1"), ("20260630", "2026Q2")]:
    fp = os.path.join(RAW, f"yjbb_{tag}.csv")
    if os.path.exists(fp) and os.path.getsize(fp) > 10000:
        log(f"[yjbb] {tag} cached")
        continue
    try:
        df = ak.stock_yjbb_em(date=period)
        df.to_csv(fp, index=False)
        log(f"[yjbb] {tag} rows={len(df)}")
    except Exception as e:
        log(f"[yjbb] {tag} FAIL {str(e)[:120]}")
    time.sleep(2)

# ---------- 4. 盈利预测 / 业绩预告 ----------
for name, fn, fname in [
    ("profit_forecast", ak.stock_profit_forecast_em, "profit_forecast.csv"),
    ("yjyg_2026Q2", lambda: ak.stock_yjyg_em(date="20260630"), "yjyg_2026Q2.csv"),
]:
    fp = os.path.join(RAW, fname)
    if os.path.exists(fp) and os.path.getsize(fp) > 10000:
        log(f"[{name}] cached")
        continue
    try:
        df = fn()
        df.to_csv(fp, index=False)
        log(f"[{name}] rows={len(df)}")
    except Exception as e:
        log(f"[{name}] FAIL {str(e)[:120]}")
    time.sleep(2)

# ---------- 5. 落成最新单日快照 raw/daily_YYYYMMDD.csv ----------
# 供 raw/_daily_snap.py 合并（re.fullmatch(r"daily_(\d{8})\.csv")）与步骤 5.6 及既有脚本读取。
try:
    _span = pd.read_csv(os.path.join(RAW, "daily_260d.csv"), dtype={"ts_code": str, "trade_date": str})
    _maxd = _span["trade_date"].max()
    _snap = os.path.join(RAW, f"daily_{_maxd}.csv")
    _span[_span["trade_date"] == _maxd].to_csv(_snap, index=False)
    log(f"[snap] daily_{_maxd}.csv rows={len(_span[_span['trade_date']==_maxd])} (span max={_maxd})")
    if _maxd != recent[-1]:
        log(f"[snap][WARN] 最新行情截止 {_maxd}，目标截断日 {recent[-1]} 尚不可得（存在最多一个交易周缺口，如实标注）")
except Exception as e:
    log(f"[snap] FAIL {str(e)[:120]}")

log("[ALL DONE]")