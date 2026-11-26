# -*- coding: utf-8 -*-
"""全标的 R 值口径复算（A股五轨 + 美股三轨）

- A股：队列口径 = 单腿(最接近现价、先取到有效IV的一张认购/认沽)
        平值口径 = ATM 行权价上 认购IV 与 认沽IV 的均值
- 美股：队列口径 = 最接近现价的 4 张有效合约(bid>0 且 OI>0) IV 均值
- 另附 IC(中证500) / IM(中证1000) 三个月份合约的年化基差
输出 data/verify_r_all.json
"""
import os, json, math
import numpy as np
import pandas as pd

import compute_us as CU


def _basic_files(d):
    """合约基础表清单：basic_extra.json 是仓库外补充表，新环境常见缺失。
    存在才读，缺了用 sse/szse/cffex 三张照跑，不整条链路炸。"""
    return [f for f in ("basic_sse.json", "basic_szse.json", "basic_cffex.json",
                        "basic_extra.json")
            if os.path.exists(os.path.join(d, f))]


HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, "data")
CH = os.path.join(D, "chains")
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
    return pd.read_json(os.path.join(CH, date + ".json"), orient="records",
                        dtype={"ts_code": str})


A_TGT = [("300ETF", "OP510300.SH", "510300.SH", "沪深300ETF期权(沪510300)"),
         ("500ETF", "OP510500.SH", "510500.SH", "中证500ETF期权(沪510500)"),
         ("KC50", "OP588000.SH", "588000.SH", "科创50ETF期权(588000华夏)"),
         ("CYB", "OP159915.SZ", "159915.SZ", "创业板ETF期权(159915易方达)"),
         ("ZZ1000", "OP000852.SH", "000852.SH", "中证1000(MO股指期权，期货期权替代)")]


def month_block(sub, S):
    """给定单个月份的全部期权，返回单腿与平值两种口径。"""
    valid = []
    for r in sub.itertuples():
        iv = solve_iv(r.px, S, r.K, r.T, r.cp)
        if iv is not None:
            valid.append(dict(ts_code=r.ts_code, K=float(r.K), cp=r.cp,
                              px=float(r.px), iv=float(iv), dist=abs(float(r.K) - S)))
    if not valid:
        return {}
    vdf = pd.DataFrame(valid)
    # --- 队列口径：按 |K-S| 排序取第一张有效合约 ---
    sp = vdf.sort_values("dist").iloc[0]
    # --- 平值口径：现价最近、且认购+认沽都有效的行权价 ---
    pair = None
    for k in sorted(set(vdf["K"]), key=lambda x: abs(x - S)):
        cs = vdf[(vdf["K"] == k) & (vdf["cp"] == "C")]
        ps = vdf[(vdf["K"] == k) & (vdf["cp"] == "P")]
        if len(cs) and len(ps):
            ic = float(cs.iloc[0]["iv"]); ip = float(ps.iloc[0]["iv"])
            pair = dict(K=k, iv_c=ic, iv_p=ip, iv_avg=(ic + ip) / 2.0,
                        code_c=cs.iloc[0]["ts_code"], code_p=ps.iloc[0]["ts_code"])
            break
    return dict(single=dict(ts_code=sp["ts_code"], K=sp["K"], cp=sp["cp"],
                            px=sp["px"], iv=sp["iv"]),
                atm_pair=pair)


def basis_block(hist_name, basic_name, index_code, spot_hist, win):
    hist = pd.read_json(os.path.join(D, hist_name), orient="records")
    hist["trade_date"] = hist["trade_date"].astype(str)
    basic = pd.read_json(os.path.join(D, basic_name), orient="records")
    meta = basic.set_index("ts_code")[["delist_date", "list_date"]].to_dict("index")
    s_map = dict(spot_hist[index_code])
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
            "hist_mean_pct": round(m, 4), "hist_sd_pct": round(sd, 4), "n": len(series),
            "formula": "年化基差 = (F - S)/S × 365/dte × 100%；Z = (当季年化基差 - 252日均值)/252日标准差"}


def main():
    cal = json.load(open(os.path.join(D, "trade_cal.json"), encoding="utf-8"))
    win = cal["last252"]

    basics = pd.concat([pd.read_json(os.path.join(D, f), orient="records",
                                     dtype={"ts_code": str})
                        for f in _basic_files(D)], ignore_index=True)
    basics = basics.drop_duplicates("ts_code")
    bmap = basics.set_index("ts_code")[["opt_code", "call_put", "exercise_price",
                                        "maturity_date", "per_unit"]].to_dict("index")

    uh = pd.read_json(os.path.join(D, "underlying_hist.json"), orient="records")
    uh["trade_date"] = uh["trade_date"].astype(str)
    spot_hist = {c: dict(zip(g["trade_date"], g["close"])) for c, g in uh.groupby("code")}

    ch = load_chain(ASOF)
    ch = ch[ch["ts_code"].isin(bmap.keys())].copy()
    ch["opt_code"] = [bmap[t]["opt_code"] for t in ch["ts_code"]]
    ch["call_put"] = [bmap[t]["call_put"] for t in ch["ts_code"]]
    ch["K"] = [bmap[t]["exercise_price"] for t in ch["ts_code"]]
    ch["mat"] = [bmap[t]["maturity_date"] for t in ch["ts_code"]]
    ch["px"] = ch["settle"].where(ch["settle"].fillna(0) > 0, ch["close"])

    out = {"asof": ASOF, "window": [win[0], win[-1]], "r_free": R_FREE,
           "ashare": {}, "us": {}, "ic_basis_zz500": {}, "im_basis_zz1000": {}}

    # ================= A股五轨 =================
    for key, opt, spotcode, label in A_TGT:
        S = float(spot_hist[spotcode][ASOF])
        sub = ch[ch["opt_code"] == opt].copy()
        mats = sorted([m for m in sub["mat"].unique() if int(m) > int(ASOF)])
        rec = dict(label=label, opt_code=opt, spot_code=spotcode, spot=S,
                   spot_date=ASOF, contract_months=[int(m) for m in mats[:4]],
                   legs={})
        for tag, mm in [("near", mats[0]), ("next", mats[1])]:
            dte = (pd.to_datetime(str(int(mm))) - pd.to_datetime(ASOF)).days
            T = dte / 365.0
            s2 = sub[sub["mat"] == mm].copy()
            s2["T"] = T
            s2["cp"] = s2["call_put"]
            blk = month_block(s2, S)
            rec["legs"][tag] = dict(maturity=int(mm), dte_days=int(dte),
                                    T_years=round(T, 6), **blk)
        n = rec["legs"]["near"]; x = rec["legs"]["next"]
        try:
            rec["R_single"] = round(n["single"]["iv"] / x["single"]["iv"], 6)
        except Exception:
            rec["R_single"] = None
        try:
            rec["R_atm"] = round(n["atm_pair"]["iv_avg"] / x["atm_pair"]["iv_avg"], 6)
        except Exception:
            rec["R_atm"] = None
        out["ashare"][key] = rec

    # ================= 美股三轨 =================
    for T in CU.TARGETS:
        raw, df = CU.load_chain(T["chain"])
        S = float(raw["spot"])
        df = df[df.exp > pd.Timestamp(CU.SNAP)]
        mons = CU.monthly_expiries(df, pd.Timestamp(CU.SNAP))
        near, nxt = mons[0], mons[1]
        rec = dict(name=T["name"], chain_spot=S, chain_ts=raw["timestamp"],
                   spot_date=CU.SNAP, legs={})
        for tag, e in [("near", near), ("next", nxt)]:
            iv4, picks = CU.atm_iv(df, e, S)
            sub = df[(df.exp == e) & df.iv.notna() & (df.iv < 2.0)].copy()
            sub["dist"] = (sub.K - S).abs()
            s1 = sub.sort_values("dist").iloc[0]
            rec["legs"][tag] = dict(maturity=str(e.date()),
                                    dte_days=int((e - pd.Timestamp(CU.SNAP)).days),
                                    iv_atm4=(None if iv4 is None else round(iv4 / 100.0, 6)),
                                    single=dict(option=str(s1.get("option", "")),
                                                K=float(s1["K"]), cp=s1["cp"],
                                                iv=float(s1["iv"])))
        try:
            rec["R_single"] = round(rec["legs"]["near"]["single"]["iv"] /
                                    rec["legs"]["next"]["single"]["iv"], 6)
        except Exception:
            rec["R_single"] = None
        try:
            rec["R_atm"] = round(rec["legs"]["near"]["iv_atm4"] /
                                 rec["legs"]["next"]["iv_atm4"], 6)
        except Exception:
            rec["R_atm"] = None
        out["us"][T["key"]] = rec

    # ================= IC / IM 年化基差 =================
    out["ic_basis_zz500"] = basis_block("fut_ic_month_hist.json", "fut_basic_ic_month.json",
                                        "000905.SH", spot_hist, win)
    out["im_basis_zz1000"] = basis_block("fut_im_month_hist.json", "fut_basic_im_month.json",
                                         "000852.SH", spot_hist, win)

    with open(os.path.join(D, "verify_r_all.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    # ---- 打印摘要 ----
    print("== A股 ==")
    for k, r in out["ashare"].items():
        n = r["legs"]["near"]; x = r["legs"]["next"]
        print("%-7s spot=%s near=%s(%dd) next=%s(%dd) R_single=%.6f R_atm=%.6f" % (
            k, r["spot"], n["maturity"], n["dte_days"], x["maturity"], x["dte_days"],
            r["R_single"] or -1, r["R_atm"] or -1))
        print("        单腿: 近 %s %s K=%s IV=%.6f | 次 %s %s K=%s IV=%.6f" % (
            n["single"]["cp"], n["single"]["ts_code"], n["single"]["K"], n["single"]["iv"],
            x["single"]["cp"], x["single"]["ts_code"], x["single"]["K"], x["single"]["iv"]))
    print("== 美股 ==")
    for k, r in out["us"].items():
        n = r["legs"]["near"]; x = r["legs"]["next"]
        print("%-5s spot=%.2f near=%s next=%s R_single=%.6f R_atm=%.6f | iv_atm4 near=%s next=%s" % (
            k, r["chain_spot"], n["maturity"], x["maturity"], r["R_single"] or -1,
            r["R_atm"] or -1, n["iv_atm4"], x["iv_atm4"]))
    print("== IC/IM 年化基差 ==")
    for nm, b in [("IC(中证500)", out["ic_basis_zz500"]), ("IM(中证1000)", out["im_basis_zz1000"])]:
        print(nm, "index=%.4f" % b["index_close"],
              [(l["slot"], l["contract"], l["annual_basis_pct"]) for l in b["legs"]],
              "Z=%.4f(均值%.4f/σ%.4f n=%d)" % (b["z_far"], b["hist_mean_pct"],
                                               b["hist_sd_pct"], b["n"]))


if __name__ == "__main__":
    main()