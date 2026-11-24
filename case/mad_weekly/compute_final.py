# -*- coding: utf-8 -*-
"""最终口径：全市场 MAD = MA20/MA200 - 1（前复权、剔除停牌日），MA40 门槛筛选，Top20。

口径：
  1) 价量来自 tushare 代理通道 daily + adj_factor，逐日拉取 2025-06-03 ~ 2026-10-30（344 个交易日）
  2) 前复权价 = 原始收盘 × 复权因子 / 末日复权因子；停牌日（成交量=0）从个股 K 线中剔除
  3) MA20 / MA40 / MA200 = 最近 20 / 40 / 200 根实际成交 K 线的算术平均（简单均线）
  4) MAD = MA20 / MA200 - 1
  5) 过滤：股价 > MA40，且 MA40 上行（MA40 当日 > 5 个交易日前）
  6) 过滤后按 MAD 从高到低排序取前 20
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
SLOPE_LAG = 5


def load():
    files = sorted(glob.glob(os.path.join(CACHE, "D_*.npz")))
    raw, adj, vol = {}, {}, {}
    for p in files:
        d = os.path.basename(p)[2:10]
        z = np.load(p, allow_pickle=True)
        if "empty" in z:
            continue
        idx = z["codes"]
        raw[d] = pd.Series(z["close"], index=idx)
        adj[d] = pd.Series(z["adj"], index=idx)
        vol[d] = pd.Series(z["vol"], index=idx)
    R = pd.DataFrame(raw).T.sort_index()
    A = pd.DataFrame(adj).T.sort_index()
    V = pd.DataFrame(vol).T.sort_index()
    return R, A, V


def main():
    R, A, V = load()
    last = R.index[-1]
    n_days = R.shape[0]
    print("panel %d dates x %d codes, last=%s" % (n_days, R.shape[1], last))

    bas = pd.read_csv(os.path.join(WORK, "stock_basic_all.csv"), dtype={"list_date": str})
    NAME = bas.drop_duplicates("ts_code").set_index("ts_code")
    name = NAME["name"].to_dict()
    ind = NAME["industry"].to_dict()
    ldate = NAME["list_date"].to_dict()

    recs = []
    for code in R.columns:
        r = R[code]
        v = V[code]
        a = A[code]
        traded = (v > 0) & r.notna()
        rt = r[traded]
        at = a[traded]
        if len(rt) == 0:
            continue
        n = len(rt)
        rec = {
            "ts_code": code, "name": name.get(code, ""), "industry": ind.get(code, ""),
            "list_date": ldate.get(code, ""),
            "bars": n, "suspended_days": n_days - n,
            "last_bar_date": rt.index[-1],
        }
        if rt.index[-1] != last:
            rec["status"] = "最后交易日停牌/无成交"
            recs.append(rec)
            continue
        if n < 200:
            rec["status"] = "历史不足200根K线"
            recs.append(rec)
            continue
        adj_last = at.iloc[-1]
        s = rt * at / adj_last                                  # 前复权（末日前复权价=原始价）
        ma20, ma40, ma200 = s.iloc[-20:].mean(), s.iloc[-40:].mean(), s.iloc[-200:].mean()
        ma40_lag = s.iloc[-45:-5].mean()
        ma40_lag1 = s.iloc[-46:-6].mean() if n >= 46 else np.nan
        c = s.iloc[-1]
        rec.update({
            "status": "ok",
            "close": float(c), "ma20": float(ma20), "ma40": float(ma40), "ma200": float(ma200),
            "mad": float(ma20 / ma200 - 1.0),
            "ma40_slope5_pct": float(ma40 / ma40_lag - 1.0),
            "ma40_slope1_pct": float(ma40 / s.iloc[-41:-1].mean() - 1.0),
            "above_ma40": bool(c > ma40),
            "ma40_up": bool(ma40 > ma40_lag),
            "close_over_ma40_pct": float(c / ma40 - 1.0),
            "max_abs_ret20": float(s.iloc[-21:].pct_change().abs().max()),
            "adj_factor_last": float(adj_last),
            "is_st": bool(any(t in str(name.get(code, "")) for t in ("ST", "退"))),
            "board": "科创板" if code.endswith(".SH") and code[:3] == "688" else
                     ("创业板" if code[:3] == "300" or code[:3] == "301" else
                      ("北交所" if code.endswith(".BJ") else "主板")),
        })
        recs.append(rec)

    r = pd.DataFrame(recs)
    r["pass_filter"] = (r.status == "ok") & r.above_ma40 & r.ma40_up
    g = r[r.status == "ok"].sort_values("mad", ascending=False).reset_index(drop=True)
    g.insert(0, "mad_rank_all", range(1, len(g) + 1))
    passed = g[g.pass_filter].copy().reset_index(drop=True)
    passed.insert(0, "rank", range(1, len(passed) + 1))
    passed.to_csv(os.path.join(WORK, "ma_rank_passed.csv"), index=False, encoding="utf-8-sig")
    g.to_csv(os.path.join(WORK, "ma_rank_all_ok.csv"), index=False, encoding="utf-8-sig")
    r.to_csv(os.path.join(WORK, "universe_all.csv"), index=False, encoding="utf-8-sig")
    top20 = passed.head(20).copy()
    top20.to_csv(os.path.join(WORK, "top20.csv"), index=False, encoding="utf-8-sig")

    diag = {
        "基准交易日": last, "窗口起点": R.index[0], "窗口交易日数": int(n_days),
        "全市场标的数": int(R.shape[1]),
        "可计算MA200": int((r.status == "ok").sum()),
        "被排除_历史不足200根K线": int((r.status == "历史不足200根K线").sum()),
        "被排除_末日停牌无成交": int((r.status == "最后交易日停牌/无成交").sum()),
        "MA20在MA200上方(全部渠道MAD>0)": int((g.mad > 0).sum()),
        "股价在MA40上方": int(g.above_ma40.sum()),
        "MA40向上": int(g.ma40_up.sum()),
        "同时满足两项": int(passed.shape[0]),
        "MAD分位": {str(q): round(float(g.mad.quantile(q)), 4) for q in (0.5, 0.9, 0.95, 0.99, 1.0)},
        "MA40斜率口径": "MA40(当日) > MA40(5个交易日前)",
        "复权口径": "前复权（剔除停牌日）",
    }
    with open(os.path.join(WORK, "ma_diag.json"), "w", encoding="utf-8") as f:
        json.dump(diag, f, ensure_ascii=False, indent=1)
    print(json.dumps(diag, ensure_ascii=False, indent=1))
    pd.set_option("display.width", 250)
    print(top20[["rank", "ts_code", "name", "industry", "close", "mad", "ma20", "ma40", "ma200",
                 "close_over_ma40_pct", "ma40_slope5_pct", "suspended_days"]].to_string(index=False))


if __name__ == "__main__":
    main()