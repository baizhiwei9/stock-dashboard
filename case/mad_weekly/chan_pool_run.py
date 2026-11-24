# -*- coding: utf-8 -*-
"""T2：为 114 只待检股票补前复权 OHLC 日线（20240601-20261030），逐只跑缠论分型版结构分析。"""
import os, sys, json, time
import numpy as np
import pandas as pd

WORK = os.path.dirname(os.path.abspath(__file__))
SK = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/skills/user/chan-fenxing-ban"
sys.path.insert(0, SK)
sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5e9bb55354ffe9Rg1AoYf1k3lwu")
import chan_fractal
from ts_http import fetch_many

S, E = "20240601", "20261030"
pool = pd.read_csv(os.path.join(WORK, "chan_smc_pool.csv"), dtype={"ts_code": str})
CODES = sorted(pool.ts_code.unique())
NAME = pool.drop_duplicates("ts_code").set_index("ts_code")["名称"].to_dict()
BARS = os.path.join(WORK, "chan_in_pool")
os.makedirs(BARS, exist_ok=True)

need = [c for c in CODES if not os.path.exists(os.path.join(BARS, "%s.csv" % c))]
print("待取 %d 只（已有 %d 只）" % (len(need), len(CODES) - len(need)), flush=True)
if need:
    pd_ = fetch_many("daily", [{"ts_code": c, "start_date": S, "end_date": E} for c in need], workers=4)
    pa_ = fetch_many("adj_factor", [{"ts_code": c, "start_date": S, "end_date": E} for c in need], workers=4)
    ok = 0; fail = []
    for c, d, a in zip(need, pd_, pa_):
        if isinstance(d, Exception) or isinstance(a, Exception) or d is None or len(d) == 0:
            fail.append(c); continue
        m = d.merge(a[["trade_date", "adj_factor"]], on="trade_date", how="left")
        m["adj_factor"] = m["adj_factor"].ffill().bfill()
        m = m.sort_values("trade_date").reset_index(drop=True)
        la = m["adj_factor"].iloc[-1]
        for col in ("open", "high", "low", "close"):
            m[col] = m[col] * m["adj_factor"] / la
        m["date"] = pd.to_datetime(m["trade_date"], format="%Y%m%d")
        out = m[["date", "open", "high", "low", "close", "vol", "amount"]].rename(columns={"vol": "volume"})
        out.to_csv(os.path.join(BARS, "%s.csv" % c), index=False)
        ok += 1
    print("取数落盘 %d 只，失败 %d 只 %s" % (ok, len(fail), fail), flush=True)

# ---- 缠论结构分析 ----
sig, meta = [], []
files = sorted(f for f in os.listdir(BARS) if f.endswith(".csv"))
for f in files:
    c = f[:-4]
    df = pd.read_csv(os.path.join(BARS, f), parse_dates=["date"])
    try:
        r = chan_fractal.analyze(df, level="day", base="struct", name=NAME.get(c, c))
    except Exception as e:
        print("analyze FAIL", c, type(e).__name__, e, flush=True); continue
    n = len(df)
    ops = r.get("交易机会", []) or []
    bottoms = {p["行号"] for p in r.get("结构", {}).get("笔端点", []) if p.get("类型") == "底"}
    for op in ops:
        sig.append({"ts_code": c, "名称": NAME.get(c, c), "行号": op.get("行号"), "日期": str(op.get("时间"))[:10],
                    "价格": op.get("价格"), "方向": op.get("方向"), "类型": op.get("类型"),
                    "说明": op.get("说明"), "距末日K线": n - 1 - (op.get("行号") or 0),
                    "落在笔底": bool((op.get("行号") in bottoms))})
    meta.append({"ts_code": c, "名称": NAME.get(c, c), "bars": n,
                 "起": str(df.date.iloc[0].date()), "止": str(df.date.iloc[-1].date()),
                 "笔数": len(r.get("结构", {}).get("笔端点", [])), "中枢数": len(r.get("结构", {}).get("中枢", [])),
                 "机会数": len(ops)})
pd.DataFrame(sig).to_csv(os.path.join(WORK, "chan_pool_signals.csv"), index=False, encoding="utf-8-sig")
pd.DataFrame(meta).to_csv(os.path.join(WORK, "chan_pool_meta.csv"), index=False, encoding="utf-8-sig")
print("缠论：覆盖 %d 只，机会记录 %d 条" % (len(meta), len(sig)), flush=True)
print("DONE", flush=True)