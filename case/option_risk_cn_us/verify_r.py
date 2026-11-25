# -*- coding: utf-8 -*-
"""复算与追溯：ATM 口径 R 值与 IC/IM 年化基差
输出 data/verify_r.json，并打印可核对明细。
"""
import os
import json
import math
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, "data")
ASOF = "20261021"
R_FREE = 0.018


def N(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(S, K, T, s, r, cp):
    if T <= 0 or s <= 0:
        return max(0.0, (S - K) if cp == "C" else (K - S))
    d1 = (math.log(S / K) + (r + 0.5 * s * s) * T) / (s * math.sqrt(T))
    d2 = d1 - s * math.sqrt(T)
    if cp == "C":
        return S * N(d1) - K * math.exp(-r * T) * N(d2)
    return K * math.exp(-r * T) * N(-d2) - S * N(-d1)


def solve_iv(price, S, K, T, cp, lo=0.005, hi=3.0):
    if price is None or not np.isfinite(price) or price <= 0 or T <= 0:
        return None
    intr = max(0.0, (S - K) if cp == "C" else (K - S))
    if price < intr - 1e-9:
        return None
    f_lo = bs_price(S, K, T, lo, R_FREE, cp) - price
    f_hi = bs_price(S, K, T, hi, R_FREE, cp) - price
    if f_lo * f_hi > 0:
        return None
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        f = bs_price(S, K, T, mid, R_FREE, cp) - price
        if f_lo * f <= 0:
            hi = mid; f_hi = f
        else:
            lo = mid; f_lo = f
    return 0.5 * (lo + hi)


def load_chain(date):
    p = os.path.join(D, "chains", date + ".json")
    return pd.read_json(p, orient="records", dtype={"ts_code": str})


def main():
    cal = json.load(open(os.path.join(D, "trade_cal.json"), encoding="utf-8"))
    win = cal["last252"]
    basics = pd.concat([pd.read_json(os.path.join(D, f), orient="records",
                                     dtype={"ts_code": str})
                        for f in ["basic_sse.json", "basic_szse.json", "basic_cffex.json",
                                  "basic_extra.json"]], ignore_index=True)
    basics = basics.drop_duplicates("ts_code")
    bmap = basics.set_index("ts_code")[
        ["opt_code", "call_put", "exercise_price", "maturity_date", "per_unit"]].to_dict("index")

    uh = pd.read_json(os.path.join(D, "underlying_hist.json"), orient="records")
    uh["trade_date"] = uh["trade_date"].astype(str)
    spot_hist = {c: dict(zip(g["trade_date"], g["close"])) for c, g in uh.groupby("code")}

    out = {"asof": ASOF, "window": [win[0], win[-1]], "r_free": R_FREE, "targets": {}}

    TGT = {"500ETF": ("OP510500.SH", "510500.SH", "中证500ETF期权(沪510500)"),
           "ZZ1000": ("OP000852.SH", "000852.SH", "中证1000(MO股指期权)")}

    ch = load_chain(ASOF)
    ch = ch[ch["ts_code"].isin(bmap.keys())].copy()
    for c in ["opt_code", "call_put", "K", "mat", "per_unit"]:
        ch[c] = [bmap[t][{"opt_code": "opt_code", "call_put": "call_put",
                          "K": "exercise_price", "mat": "maturity_date",
                          "per_unit": "per_unit"}[c]] for t in ch["ts_code"]]
    ch["px"] = ch["settle"].where(ch["settle"].fillna(0) > 0, ch["close"])

    for key, (opt, spotcode, label) in TGT.items():
        S = spot_hist[spotcode][ASOF]
        sub = ch[ch["opt_code"] == opt].copy()
        mats = sorted([m for m in sub["mat"].unique() if int(m) > int(ASOF)])
        # 用全部到期月，找到与既有 R 一致的近月/次月（可能因过滤规则不同需说明）
        rec = {"label": label, "opt_code": opt, "spot_code": spotcode,
               "spot": float(S), "spot_date": ASOF,
               "contract_months_all": [int(m) for m in mats]}
        for tag, mm in [("near", mats[0]), ("next", mats[1])]:
            T = (pd.to_datetime(str(int(mm))) - pd.to_datetime(ASOF)).days / 365.0
            s2 = sub[sub["mat"] == mm].copy()
            s2 = s2.assign(dist=(s2["K"] - S).abs()).sort_values("dist")
            pick = None
            cand = []
            for _, r in s2.iterrows():
                iv = solve_iv(r["px"], S, r["K"], T, r["call_put"])
                cand.append({"ts_code": r["ts_code"], "K": float(r["K"]),
                             "cp": r["call_put"], "px": float(r["px"]),
                             "dist": float(abs(r["K"] - S)),
                             "iv": (None if iv is None else round(iv, 6))})
                if pick is None and iv is not None:
                    pick = (r["ts_code"], float(r["K"]), r["call_put"], float(r["px"]), iv)
            rec[tag] = {"maturity": int(mm), "T_years": round(T, 6),
                        "dte_days": (pd.to_datetime(str(int(mm))) - pd.to_datetime(ASOF)).days,
                        "pick": (None if pick is None else
                                 {"ts_code": pick[0], "K": pick[1], "cp": pick[2],
                                  "settle": pick[3], "iv": round(pick[4], 6)}),
                        "top5_nearest": cand[:5]}
            rec[tag + "_iv"] = (None if pick is None else pick[4])
        if rec["near_iv"] and rec["next_iv"]:
            rec["R"] = round(rec["near_iv"] / rec["next_iv"], 6)
            rec["R_formula"] = "R = IV(near ATM) / IV(next ATM) = %.6f / %.6f" % (
                rec["near_iv"], rec["next_iv"])
        out["targets"][key] = rec

    # ---- IC/IM 年化基差 ----
    def basis_block(hist_name, basic_name, index_code):
        hist = pd.read_json(os.path.join(D, hist_name), orient="records")
        hist["trade_date"] = hist["trade_date"].astype(str)
        basic = pd.read_json(os.path.join(D, basic_name), orient="records")
        meta = basic.set_index("ts_code")[["delist_date", "list_date"]].to_dict("index")
        closes = spot_hist[index_code]
        s_map = dict(closes)
        perv = {}
        for d in win:
            ct = hist[hist["trade_date"] == d]
            act = []
            for _, r in ct.iterrows():
                m = meta.get(r["code"])
                if not m:
                    continue
                act.append((str(m["delist_date"]), float(r["close"]), r["code"]))
            act.sort()
            if len(act) < 3 or d not in s_map:
                continue
            S = s_map[d]
            row = []
            for i in range(3):
                dl, F, code = act[i]
                days = (pd.to_datetime(dl) - pd.to_datetime(d)).days
                row.append(None if days <= 0 else ((F - S) / S * 365.0 / days * 100,
                                                   code, F, days))
            perv[d] = row
        d0 = perv[ASOF]
        series = [perv[d][2][0] for d in win if d in perv and perv[d][2]]
        b3 = d0[2][0]
        m = float(np.mean(series)); sd = float(np.std(series, ddof=1))
        z = (b3 - m) / sd if sd else None
        S0 = s_map[ASOF]
        return {"index_code": index_code, "index_close": float(S0), "index_date": ASOF,
                "legs": [{"slot": ["当月", "次月", "当季"][i],
                          "contract": (None if d0[i] is None else d0[i][1]),
                          "fut_close": (None if d0[i] is None else d0[i][2]),
                          "dte_days": (None if d0[i] is None else d0[i][3]),
                          "annual_basis_pct": (None if d0[i] is None else round(d0[i][0], 4))}
                         for i in range(3)],
                "z_far": (None if z is None else round(z, 4)),
                "hist_mean_pct": round(m, 4), "hist_sd_pct": round(sd, 4),
                "n": len(series),
                "formula": "年化基差 = (F - S)/S × 365/dte × 100%；Z = (b3 - 252日均值)/252日标准差"}

    out["ic_basis_zz500"] = basis_block("fut_ic_month_hist.json", "fut_basic_ic_month.json",
                                        "000905.SH")
    out["im_basis_zz1000"] = basis_block("fut_im_month_hist.json", "fut_basic_im_month.json",
                                         "000852.SH")

    with open(os.path.join(D, "verify_r.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()