# -*- coding: utf-8 -*-
"""A股五标的期权风险指标汇总: 8字段 + 252日基线 + 判灯 + 释放进度"""
import os, json, math
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, "data")
# 数据日：优先取主链路注入的 TRADE_DATE，否则在 main() 中取期权链指标的最新交易日
# （禁止硬编码日期，避免汇总口径日落后于实际期权数据日）
ASOF = os.environ.get("TRADE_DATE") or ""

TARGETS = [
    ("300ETF", "沪深300ETF期权", "沪510300 / 深159919备注", "OP510300.SH", "510300.SH", "qvix_300etf.json"),
    ("500ETF", "中证500ETF期权", "沪510500", "OP510500.SH", "510500.SH", "qvix_500etf.json"),
    ("KC50", "科创50ETF期权", "588000华夏 / 588080备注", "OP588000.SH", "588000.SH", "qvix_kcb.json"),
    ("CYB", "创业板ETF期权", "159915易方达创业板ETF期权", "OP159915.SZ", "159915.SZ", "qvix_cyb.json"),
    ("ZZ1000", "中证1000(MO股指期权)", "期货期权替代·IM各月基差", "OP000852.SH", "000852.SH", None),
]


def pct_rank(arr, val):
    a = np.asarray([x for x in arr if x is not None and np.isfinite(x)], dtype=float)
    if not len(a) or val is None or not np.isfinite(val):
        return None
    return float((a <= val).sum() / len(a) * 100)


def hv(closes, n=20):
    c = np.asarray(closes, dtype=float)
    if len(c) < n + 1:
        return None
    r = np.diff(np.log(c[-(n + 1):]))
    return float(np.std(r, ddof=1) * math.sqrt(252) * 100)


def main():
    with open(os.path.join(D, "trade_cal.json"), encoding="utf-8") as f:
        cal = json.load(f)
    win = cal["last252"]                      # 252 交易日窗口(升序)
    winset = set(win)

    cm = pd.read_json(os.path.join(D, "ashare_chain_metrics.json"), orient="records")
    cm["date"] = cm["date"].astype(str)

    global ASOF
    if not ASOF:
        ASOF = str(cm["date"].max())   # 期权链指标最新交易日（保证口径日与数据同步）
    print("[asof]", ASOF)

    uh = pd.read_json(os.path.join(D, "underlying_hist.json"), orient="records")
    uh["trade_date"] = uh["trade_date"].astype(str)
    closes = {c: g.sort_values("trade_date")["close"].tolist()
              for c, g in uh.groupby("code")}
    dates_u = {c: g.sort_values("trade_date")["trade_date"].tolist()
               for c, g in uh.groupby("code")}

    out = {"asof": ASOF, "window": [win[0], win[-1]], "targets": {}}

    for key, label, note, opt, spot, qvixf in TARGETS:
        g = cm[cm["target"] == key].sort_values("date")
        g = g[g["date"].isin(winset)]
        # ---- VIX 序列 ----
        if qvixf:
            q = pd.read_json(os.path.join(D, qvixf), orient="records")
            q["d"] = pd.to_datetime(q["date"], unit="ms", errors="coerce")
            if q["d"].isna().all():
                q["d"] = pd.to_datetime(q["date"], errors="coerce")
            q = q.dropna(subset=["d", "close"])
            q["ds"] = q["d"].dt.strftime("%Y%m%d")
            q = q[q["ds"].isin(winset)].sort_values("ds")
            vix_ser = list(q["close"].values)
            vix_src = "链上自算 30 日恒定期限 ATM 隐含波动率（原券商 QVIX 源已停用）"
        else:
            vix_ser = [x * 100 if x and np.isfinite(x) else None for x in g["iv_near"]]
            vix_src = "无官方QVIX, 以MO当月(202611)平价期权ATM隐含波动率自算(30日历日)"
        vix_today = next((x for x in reversed(vix_ser) if x and np.isfinite(x)), None)
        vix_pct = pct_rank(vix_ser, vix_today)

        gd = g.set_index("date")
        def last3(col):
            s = [gd[col].get(d, np.nan) for d in win[-3:]]
            return s
        R_today = float(g.iloc[-1]["R"]) if np.isfinite(g.iloc[-1]["R"]) else None
        R3 = last3("R")
        R_up3 = all(x is not None and np.isfinite(x) and x > 1.00 for x in R3)
        R5 = [gd["R"].get(d, np.nan) for d in win[-5:]]
        R_gt105_5 = all(x is not None and np.isfinite(x) and x > 1.05 for x in R5)
        R_prev = [gd["R"].get(d, np.nan) for d in win[-4:]]

        rrs = [x for x in g["rr_near"] if x is not None and np.isfinite(x)]
        rr_today = rrs[-1] if rrs else None
        rr_med = float(np.median(rrs)) if rrs else None
        rr_sd = float(np.std(rrs, ddof=1)) if len(rrs) > 2 else None
        rr_break = (rr_today is not None and rr_med is not None and rr_sd is not None
                    and rr_today > rr_med + rr_sd)
        rr_below_med = (rr_today is not None and rr_med is not None and rr_today < rr_med)

        # ---- HV20 / 隐历差 ----
        cl = closes.get(spot, [])
        h20 = hv(cl)
        ivhv = (vix_today - h20) if (vix_today and h20) else None

        # ---- PCR ----
        pcr_today = gd["pcr"].get(ASOF)
        if pcr_today is None or not np.isfinite(pcr_today):
            pcr_today = float(g.iloc[-1]["pcr"])
        pcr3 = [gd["pcr"].get(d, np.nan) for d in win[-3:]]
        pcr_up3 = all(np.isfinite(x) for x in pcr3) and pcr3[0] < pcr3[1] < pcr3[2]

        # ---- GEX ----
        gex_pct = pct_rank(g["gex_norm"].tolist(), g.iloc[-1]["gex_norm"])
        flip_dist = float(g.iloc[-1]["flip_dist"]) if np.isfinite(g.iloc[-1]["flip_dist"]) else None
        flip = float(g.iloc[-1]["flip"]) if np.isfinite(g.iloc[-1]["flip"]) else None
        gex_norm_now = float(g.iloc[-1]["gex_norm"])

        # ---- 1000 IM 贴水 ----
        im = None
        if key == "ZZ1000":
            im = im_basis(win, closes.get("000852.SH", []), dates_u.get("000852.SH", []))

        out["targets"][key] = dict(
            label=label, note=note, opt_code=opt, spot_code=spot, spot=float(g.iloc[-1]["spot"]),
            vix=vix_today, vix_src=vix_src, vix_pct=vix_pct,
            R=R_today, R_prev=R_prev, R_up3=R_up3, R_gt105_5=R_gt105_5,
            rr=rr_today, rr_med=rr_med, rr_sd=rr_sd, rr_break=rr_break, rr_below_med=rr_below_med,
            hv20=h20, ivhv=ivhv, pcr=pcr_today, pcr3=pcr3, pcr_up3=pcr_up3,
            gex_pct=gex_pct, gex_norm=gex_norm_now, flip=flip, flip_dist=flip_dist,
            im=im, near_mat=int(g.iloc[-1]["near_mat"]), next_mat=int(g.iloc[-1]["next_mat"]),
        )

    with open(os.path.join(D, "ashare_final.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=lambda o: bool(o) if hasattr(o,'item') else float(o))
    for k, v in out["targets"].items():
        print(k, {kk: (round(vv, 4) if isinstance(vv, float) else vv)
                  for kk, vv in v.items() if kk in
                  ["vix", "vix_pct", "R", "R_up3", "rr", "rr_med", "ivhv", "pcr", "gex_pct",
                   "flip_dist", "hv20"]})


def im_basis(win, closes, dts):
    """IM 各月年化基差: 当月/次月/当季 + 远月贴水 Z"""
    hist = pd.read_json(os.path.join(D, "fut_im_month_hist.json"), orient="records")
    hist["trade_date"] = hist["trade_date"].astype(str)
    basic = pd.read_json(os.path.join(D, "fut_basic_im_month.json"), orient="records")
    meta = basic.set_index("ts_code")[["delist_date", "list_date"]].to_dict("index")
    s_map = dict(zip(dts, closes))
    perv = {}
    for d in win:
        ct = hist[hist["trade_date"] == d]
        act = []
        for _, r in ct.iterrows():
            m = meta.get(r["code"])
            if not m:
                continue
            act.append((str(m["delist_date"]), float(r["close"])))
        act.sort()
        if len(act) < 3 or d not in s_map:
            continue
        S = s_map[d]
        row = []
        for i in range(3):
            dl, F = act[i]
            days = (pd.to_datetime(dl) - pd.to_datetime(d)).days
            if days <= 0:
                row.append(None); continue
            row.append((F - S) / S * 365.0 / days * 100)
        perv[d] = row
    if ASOF not in perv:
        return None
    b1, b2, b3 = perv[ASOF]
    series = [perv[d][2] for d in win if d in perv and perv[d][2] is not None]
    z = None
    if len(series) > 30 and b3 is not None:
        m = float(np.mean(series)); sd = float(np.std(series, ddof=1))
        z = (b3 - m) / sd if sd else None
    return dict(b1=b1, b2=b2, b3=b3, z_far=z, n=len(series),
                z_far_open=None, z_far_open_close=None,
                z_of_discount=(None if z is None else -z))


if __name__ == "__main__":
    main()