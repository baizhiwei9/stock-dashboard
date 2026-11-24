# -*- coding: utf-8 -*-
"""A股五标的期权风险指标计算 (从本地落盘数据推导)

期权计算原语（BS 定价 / IV / Gamma / Delta）自 2026-11-13 起统一走 options_utility：
取数经 DataHub 的链与价，计算口径（近月/次月、25Δ 阈值、GEX 公式、分位窗口）
仍由本脚本自己的参数决定，策略判断不在此层。
"""
import os, json, math
import numpy as np
import pandas as pd

import lib_adapter as LIB

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, "data")
CH = os.path.join(D, "chains")
R_FREE = 0.018

TARGETS = {
    "300ETF": dict(opt="OP510300.SH", spot="510300.SH", label="沪深300ETF期权(沪510300)",
                   note="深市159919.SZ备注"),
    "500ETF": dict(opt="OP510500.SH", spot="510500.SH", label="中证500ETF期权(沪510500)", note=""),
    "KC50": dict(opt="OP588000.SH", spot="588000.SH", label="科创50ETF期权(588000华夏)",
                 note="588080备注"),
    "CYB": dict(opt="OP159915.SZ", spot="159915.SZ", label="创业板ETF期权(159915易方达)", note=""),
    "ZZ1000": dict(opt="OP000852.SH", spot="000852.SH", label="中证1000(MO股指期权)", note="期货期权替代"),
}


def N(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(S, K, T, s, r, cp):
    """BS 理论价（转调 options_utility.calc.option_monitor.bs_call/bs_put）。"""
    if T <= 0 or s <= 0:
        return max(0.0, (S - K) if cp == "C" else (K - S))
    return LIB.bs_call(S, K, T, r, s) if cp == "C" else LIB.bs_put(S, K, T, r, s)


def bs_delta_gamma(S, K, T, s, r, cp):
    """Delta / Gamma（转调 calc.cn.skew_dual.bs_delta + calc.option_monitor.bs_gamma）。"""
    if T <= 0 or s <= 0:
        return (1.0 if cp == "C" and S > K else 0.0), 0.0
    return LIB.lib_bs_delta(S, K, T, s, cp == "C"), LIB.bs_gamma(S, K, T, r, s)


def solve_iv(price, S, K, T, cp, lo=0.005, hi=3.0):
    """反解隐含波动率（转调 calc.option_monitor.bs_iv，Brent 法）。"""
    if price is None or not np.isfinite(price) or price <= 0 or T <= 0:
        return None
    intr = max(0.0, (S - K) if cp == "C" else (K - S))
    if price < intr - 1e-9:
        return None
    v = LIB.bs_iv(S, K, T, R_FREE, float(price), "call" if cp == "C" else "put")
    return float(v) if v is not None and np.isfinite(v) and v > 0 else None


def load_chain(date):
    p = os.path.join(CH, date + ".json")
    if not os.path.exists(p):
        return None
    return pd.read_json(p, orient="records", dtype={"ts_code": str})


def main():
    with open(os.path.join(D, "trade_cal.json"), encoding="utf-8") as f:
        cal = json.load(f)
    dates = cal["last252"]

    basics = pd.concat([pd.read_json(os.path.join(D, f), orient="records",
                                     dtype={"ts_code": str})
                        for f in ["basic_sse.json", "basic_szse.json", "basic_cffex.json",
                                  "basic_extra.json"]],
                       ignore_index=True)
    basics = basics.drop_duplicates("ts_code")
    bmap = basics.set_index("ts_code")[
        ["opt_code", "call_put", "exercise_price", "maturity_date", "per_unit"]].to_dict("index")

    uh = pd.read_json(os.path.join(D, "underlying_hist.json"), orient="records")
    uh["trade_date"] = uh["trade_date"].astype(str)
    spot_hist = {}
    for code, g in uh.groupby("code"):
        spot_hist[code] = dict(zip(g["trade_date"], g["close"]))

    # ---------- 逐日逐标的 ----------
    recs = []
    for date in dates:
        ch = load_chain(date)
        if ch is None or not len(ch):
            continue
        ch = ch[ch["ts_code"].isin(bmap.keys())].copy()
        if not len(ch):
            continue
        ch["opt_code"] = [bmap[t]["opt_code"] for t in ch["ts_code"]]
        ch["call_put"] = [bmap[t]["call_put"] for t in ch["ts_code"]]
        ch["K"] = [bmap[t]["exercise_price"] for t in ch["ts_code"]]
        ch["mat"] = [bmap[t]["maturity_date"] for t in ch["ts_code"]]
        ch["per_unit"] = [bmap[t]["per_unit"] for t in ch["ts_code"]]
        ch["px"] = ch["settle"].where(ch["settle"].fillna(0) > 0, ch["close"])
        for tkey, cfg in TARGETS.items():
            S = spot_hist.get(cfg["spot"], {}).get(date)
            if not S or not np.isfinite(S):
                continue
            sub = ch[ch["opt_code"] == cfg["opt"]].copy()
            if len(sub) < 6:
                continue
            mats = sorted([m for m in sub["mat"].unique() if int(m) > int(date)])
            if len(mats) < 2:
                continue
            m1, m2 = mats[0], mats[1]
            out = dict(date=date, target=tkey, spot=float(S))
            ivs = {}
            for tag, mm in [("near", m1), ("next", m2)]:
                T = (pd.to_datetime(str(int(mm))) - pd.to_datetime(date)).days / 365.0
                s2 = sub[sub["mat"] == mm].copy()
                s2 = s2.assign(dist=(s2["K"] - S).abs()).sort_values("dist")
                iv_atm = None
                for _, r in s2.iterrows():
                    iv = solve_iv(r["px"], S, r["K"], T, r["call_put"])
                    if iv:
                        iv_atm = iv; break
                ivs[tag] = iv_atm
                # 25-delta RR
                if iv_atm:
                    bestP = bestC = None
                    for _, r in s2.iterrows():
                        d, g = bs_delta_gamma(S, r["K"], T, iv_atm, R_FREE, r["call_put"])
                        ivx = solve_iv(r["px"], S, r["K"], T, r["call_put"])
                        if ivx is None:
                            continue
                        if r["call_put"] == "P" and bestP is None and abs(d) < 0.30:
                            bestP = (abs(abs(d) - 0.25), ivx)
                        if r["call_put"] == "C" and bestC is None and abs(d) < 0.30:
                            bestC = (abs(d - 0.25), ivx)
                    if bestP and bestC:
                        out["rr_" + tag] = bestP[1] - bestC[1]
                        out["iv25p_" + tag] = bestP[1]
                        out["iv25c_" + tag] = bestC[1]
                    else:
                        out["rr_" + tag] = None
            # ---- GEX 代理 (近月+次月全部在场合约; 认购正/认沽负, 交易商口径) ----
            gex_k = []
            for mm in (m1, m2):
                s3 = sub[sub["mat"] == mm]
                Tg = (pd.to_datetime(str(int(mm))) - pd.to_datetime(date)).days / 365.0
                ivm = (ivs.get("near") if mm == m1 else ivs.get("next")) or 0.25
                for _, r in s3.iterrows():
                    ivr = solve_iv(r["px"], S, r["K"], Tg, r["call_put"]) or ivm
                    _, g = bs_delta_gamma(S, r["K"], Tg, ivr, R_FREE, r["call_put"])
                    oi = r["oi"] or 0
                    mult = r["per_unit"] or 1
                    sign = 1.0 if r["call_put"] == "C" else -1.0
                    gex_k.append((r["K"], sign * oi * g * mult))
            gdf = pd.DataFrame(gex_k, columns=["K", "gex"]).groupby("K", as_index=False)["gex"].sum()
            gdf = gdf.sort_values("K").reset_index(drop=True)
            gdf["cum"] = gdf["gex"].cumsum()
            ks = gdf["K"].tolist(); gs = gdf["gex"].tolist()
            # GammaFlip: 逐档净Gamma(认购正/认沽负)由负转正的行权价, 取离现价最近的一个
            cands = []
            for i in range(1, len(ks)):
                if gs[i - 1] < 0 <= gs[i]:
                    r = (ks[i - 1] + ks[i]) / 2.0
                    cands.append(r)
            if cands:
                cands.sort(key=lambda x: abs(x - S))
                out["flip"] = float(cands[0])
                out["flip_extrap"] = False
            else:
                out["flip"] = None
                out["flip_extrap"] = True
            oi_tot = float(sum((r["oi"] or 0) * (r["per_unit"] or 1) for _, r in
                               sub[sub["mat"].isin([m1, m2])].iterrows())) or 1.0
            out["gex_total"] = float(gdf["gex"].sum())
            out["gex_norm"] = float(gdf["gex"].sum()) / oi_tot
            out["flip_dist"] = (abs(S - out["flip"]) / S * 100) if out["flip"] else None
            out["iv_near"] = ivs.get("near")
            out["iv_next"] = ivs.get("next")
            if ivs.get("near") and ivs.get("next"):
                out["R"] = ivs["near"] / ivs["next"]
            # PCR 持仓
            po = sub[sub["call_put"] == "P"]["oi"].fillna(0).sum()
            co = sub[sub["call_put"] == "C"]["oi"].fillna(0).sum()
            out["pcr"] = float(po / co) if co else None
            out["near_mat"] = int(m1); out["next_mat"] = int(m2)
            recs.append(out)

    df = pd.DataFrame(recs).sort_values(["target", "date"])
    df.to_json(os.path.join(D, "ashare_chain_metrics.json"), orient="records",
               force_ascii=False, indent=1)
    print(df.shape)
    for t, g in df.groupby("target"):
        print(t, "dates", len(g), "last", g.iloc[-1][["date", "spot", "iv_near", "iv_next", "R",
                                                     "rr_near", "pcr", "flip_dist"]].to_dict())


if __name__ == "__main__":
    main()