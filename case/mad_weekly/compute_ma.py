# -*- coding: utf-8 -*-
"""全市场 MAD = MA20/MA200 - 1 筛选（MA40 上方 + MA40 斜率向上），输出排名。

输出:
  ma_rank.csv        全市场逐股明细（含未通过者，便于核对）
  top20.csv          Top20
  ma_diag.json       诊断信息
"""
import os
import sys
import json
import glob

WORK = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5e9bb55354ffe9Rg1AoYf1k3lwu")
import numpy as np
import pandas as pd

CACHE = os.path.join(WORK, "cache_ma")
SLOPE_LAG = 5          # MA40 斜率对照间隔（交易日）
MA_SHORT, MA_MID, MA_LONG = 20, 40, 200


def load_panel():
    files = sorted(glob.glob(os.path.join(CACHE, "D_*.npz")))
    dates, code_set, rows = [], {}, {}
    for p in files:
        d = os.path.basename(p)[2:10]
        z = np.load(p, allow_pickle=True)
        if "empty" in z:
            continue
        codes = z["codes"]
        close = z["close"]
        adj = z["adj"]
        cadj = close * adj
        cadj = np.where(np.isfinite(cadj), cadj, np.nan)
        dates.append(d)
        rows[d] = pd.Series(cadj, index=codes)
        code_set[d] = set(codes)
    df = pd.DataFrame(rows).T                      # index=date, columns=code
    df = df.sort_index()
    return df


def main():
    df = load_panel()
    last_date = df.index[-1]
    print("panel: %d dates x %d codes, last=%s" % (df.shape[0], df.shape[1], last_date))

    recs = []
    for code in df.columns:
        s = df[code].dropna()
        n = len(s)
        rec = {"ts_code": code, "obs": n}
        if s.index[-1] == last_date:
            rec["last_close_date"] = last_date
        else:
            rec["last_close_date"] = s.index[-1]
        if n < MA_LONG:
            rec["status"] = "insufficient_history"
            recs.append(rec)
            continue
        if s.index[-1] != last_date:               # 最后交易日无成交（停牌/退市）→ 信号不新鲜
            rec["status"] = "no_trade_on_last_day"
            recs.append(rec)
            continue
        ma20 = s.iloc[-MA_SHORT:].mean()
        ma40 = s.iloc[-MA_MID:].mean()
        ma200 = s.iloc[-MA_LONG:].mean()
        ma40_lag = s.iloc[-(MA_MID + SLOPE_LAG):-SLOPE_LAG].mean()
        ma40_lag1 = s.iloc[-(MA_MID + 1):-1].mean()
        c = s.iloc[-1]
        rec.update({
            "status": "ok",
            "close": c, "ma20": ma20, "ma40": ma40, "ma200": ma200,
            "mad": ma20 / ma200 - 1.0,
            "ma40_slope_pct_lag5": ma40 / ma40_lag - 1.0,
            "ma40_slope_pct_lag1": ma40 / ma40_lag1 - 1.0,
            "above_ma40": bool(c > ma40),
            "ma40_up_lag5": bool(ma40 > ma40_lag),
            "ma40_up_lag1": bool(ma40 > ma40_lag1),
            "close_over_ma40_pct": c / ma40 - 1.0,
        })
        recs.append(rec)

    r = pd.DataFrame(recs)
    r["pass_filter"] = (r.status == "ok") & r.above_ma40 & r.ma40_up_lag5
    diag = {
        "last_trade_date": last_date,
        "window_start": df.index[0],
        "trading_days_in_window": int(df.shape[0]),
        "codes_total": int(df.shape[1]),
        "ok_basis": int((r.status == "ok").sum()),
        "insufficient_history": int((r.status == "insufficient_history").sum()),
        "no_trade_on_last_day": int((r.status == "no_trade_on_last_day").sum()),
        "pass_filter": int(r.pass_filter.sum()),
        "slope_lag": SLOPE_LAG,
        "above_ma40_among_ok": int(r.loc[r.status == "ok", "above_ma40"].sum()),
        "ma40_up_among_ok": int(r.loc[r.status == "ok", "ma40_up_lag5"].sum()),
    }
    print(json.dumps(diag, ensure_ascii=False, indent=1))

    ok = r[r.status == "ok"].copy()
    ok = ok.sort_values("mad", ascending=False)
    ok.to_csv(os.path.join(WORK, "ma_rank_all_ok.csv"), index=False, encoding="utf-8-sig")
    passed = ok[ok.pass_filter].copy()
    passed.insert(0, "rank", range(1, len(passed) + 1))
    passed.to_csv(os.path.join(WORK, "ma_rank_passed.csv"), index=False, encoding="utf-8-sig")
    top20 = passed.head(20)
    top20.to_csv(os.path.join(WORK, "top20.csv"), index=False, encoding="utf-8-sig")
    with open(os.path.join(WORK, "ma_diag.json"), "w", encoding="utf-8") as f:
        json.dump(diag, f, ensure_ascii=False, indent=1)

    pd.set_option("display.width", 200)
    print(passed.head(30)[["rank", "ts_code", "close", "ma20", "ma40", "ma200", "mad",
                           "close_over_ma40_pct", "ma40_slope_pct_lag5"]].to_string(index=False))


if __name__ == "__main__":
    main()