# -*- coding: utf-8 -*-
"""诊断 + 生成带名称/行业/原始价的名单。"""
import os
import sys
import json
import glob

WORK = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5e9bb55354ffe9Rg1AoYf1k3lwu")
import numpy as np
import pandas as pd
from ts_http import fetch_df

CACHE = os.path.join(WORK, "cache_ma")

# 1) 基础信息
bas = []
for st in ("L", "D", "P"):
    d = fetch_df("stock_basic", {"exchange": "", "list_status": st},
                 fields="ts_code,name,industry,area,list_date,list_status")
    bas.append(d)
bas = pd.concat(bas, ignore_index=True).drop_duplicates("ts_code")
bas.to_csv(os.path.join(WORK, "stock_basic_all.csv"), index=False, encoding="utf-8-sig")
name = bas.set_index("ts_code")["name"].to_dict()
ind = bas.set_index("ts_code")["industry"].to_dict()
ldate = bas.set_index("ts_code")["list_date"].to_dict()
print("stock_basic rows=%d" % len(bas))

# 2) 面板
files = sorted(glob.glob(os.path.join(CACHE, "D_*.npz")))
rows, raw_rows = {}, {}
for p in files:
    d = os.path.basename(p)[2:10]
    z = np.load(p, allow_pickle=True)
    if "empty" in z:
        continue
    hfq = z["close"] * z["adj"]
    rows[d] = pd.Series(np.where(np.isfinite(hfq), hfq, np.nan), index=z["codes"])
    raw_rows[d] = pd.Series(z["close"], index=z["codes"])
hfq = pd.DataFrame(rows).T.sort_index()
raw = pd.DataFrame(raw_rows).T.sort_index()
last = hfq.index[-1]

recs = []
for code in hfq.columns:
    s = hfq[code].dropna()
    n = len(s)
    if n < 200 or s.index[-1] != last:
        continue
    ma20, ma40, ma200 = s.iloc[-20:].mean(), s.iloc[-40:].mean(), s.iloc[-200:].mean()
    ma40_lag = s.iloc[-45:-5].mean()
    ret = s.pct_change()
    recs.append({
        "ts_code": code, "name": name.get(code, ""), "industry": ind.get(code, ""),
        "list_date": ldate.get(code, ""),
        "obs": n, "missing_days": 344 - n,
        "close_raw": raw[code].dropna().iloc[-1],
        "mad": ma20 / ma200 - 1, "ma20": ma20, "ma40": ma40, "ma200": ma200,
        "close_hfq": s.iloc[-1], "above_ma40": s.iloc[-1] > ma40, "ma40_up5": ma40 > ma40_lag,
        "close_over_ma40": s.iloc[-1] / ma40 - 1, "ma40_slope5": ma40 / ma40_lag - 1,
        "max_abs_ret": float(ret.abs().max()),
        "max_ret_date": str(ret.abs().idxmax()),
        "max_ret_last20": float(ret.iloc[-20:].abs().max()),
    })
r = pd.DataFrame(recs)
r.to_csv(os.path.join(WORK, "universe_ok.csv"), index=False, encoding="utf-8-sig")

diag = {
    "last_trade_date": last,
    "universe_ok": int(len(r)),
    "mad_gt_1": int((r.mad > 1).sum()),
    "mad_pctiles": {str(q): float(r.mad.quantile(q)) for q in (0.5, 0.75, 0.9, 0.95, 0.99, 1.0)},
    "max_abs_ret_gt_45pct": int((r.max_abs_ret > 0.45).sum()),
    "obs_lt_300": int((r.obs < 300).sum()),
}
print(json.dumps(diag, ensure_ascii=False, indent=1))

passed = r[r.above_ma40 & r.ma40_up5].sort_values("mad", ascending=False).reset_index(drop=True)
passed.insert(0, "rank", range(1, len(passed) + 1))
cols = ["rank", "ts_code", "name", "industry", "close_raw", "mad", "ma20", "ma40", "ma200",
        "close_over_ma40", "ma40_slope5", "obs", "missing_days", "max_abs_ret", "max_ret_date", "list_date"]
passed[cols].to_csv(os.path.join(WORK, "ma_rank_passed.csv"), index=False, encoding="utf-8-sig")
passed.head(20)[cols].to_csv(os.path.join(WORK, "top20.csv"), index=False, encoding="utf-8-sig")
pd.set_option("display.width", 260)
pd.set_option("display.max_columns", 40)
print(passed.head(20)[["rank", "ts_code", "name", "industry", "close_raw", "mad", "close_over_ma40", "ma40_slope5", "missing_days"]].to_string(index=False))