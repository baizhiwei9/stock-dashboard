# -*- coding: utf-8 -*-
"""A股 QVIX 自算（30 日恒定期限 ATM 隐含波动率）—— 盘中实时化改造

原实现依赖 akshare 抓取第三方（期权论坛 optbbs）的 QVIX 日线，源头滞后且不可控
（盘中当日不可得、且曾出现源表停更）。现改为**从本地期权链自算**：

  对每个 A股标的（300ETF 510300 / 500ETF 510500 / 科创50 588000 / 创业板 159915）：
    · 取剩余期限在 30 日两侧的两个到期月（T1 ≤ 30d ≤ T2），分别求 ATM 隐含波动率
      （在 K=S 处按行权价线性插值：K<S 用认沽、K≥S 用认购，行权价区间 |K/S-1|≤30%）；
    · 在「方差」空间线性插值到 30 日：v30 = v1 + (v2−v1)(T30−T1)/(T2−T1)，
      QVIX = 100·√v30。
  历史序列同样用已落盘的 252 个交易日期权链逐日重算，保证分位（vix_pct）自洽。

输出（保持与旧文件同构，下游 analyze_ashare / judge / forecast_vol 无需改动）：
    data/qvix_300etf.json、data/qvix_500etf.json、data/qvix_kcb.json、data/qvix_cyb.json
    字段：date(ms) / open / high / low / close

中证1000 无官方 QVIX，仍沿用链上 MO 当月 ATM-IV 自算（analyze_ashare 处理），不在此刷新。
"""
import json
import math
import os

import numpy as np
import pandas as pd

import lib_adapter as LIB  # 期权计算原语走 options_utility（用户口径 2026-11-13）

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
CH = os.path.join(DATA, "chains")
R_FREE = 0.018
T30 = 30.0 / 365.0
ATM_BAND = 0.30   # 行权价筛选：|K/S-1| <= 30%

MAP = {
    "qvix_300etf": "OP510300.SH",
    "qvix_500etf": "OP510500.SH",
    "qvix_kcb": "OP588000.SH",
    "qvix_cyb": "OP159915.SZ",
}
SPOT_CODE = {
    "qvix_300etf": "510300.SH", "qvix_500etf": "510500.SH",
    "qvix_kcb": "588000.SH", "qvix_cyb": "159915.SZ",
}


def N(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(S, K, T, s, r, cp):
    """BS 理论价（转调 options_utility.calc.option_monitor）。"""
    if T <= 0 or s <= 0:
        return max(0.0, (S - K) if cp == "C" else (K - S))
    return LIB.bs_call(S, K, T, r, s) if cp == "C" else LIB.bs_put(S, K, T, r, s)


def solve_iv(price, S, K, T, cp, lo=0.005, hi=3.0):
    """反解 IV（转调 calc.option_monitor.bs_iv，Brent 法）。"""
    if price is None or not np.isfinite(price) or price <= 0 or T <= 0:
        return None
    intr = max(0.0, (S - K) if cp == "C" else (K - S))
    if price < intr - 1e-9:
        return None
    v = LIB.bs_iv(S, K, T, R_FREE, float(price), "call" if cp == "C" else "put")
    return float(v) if v is not None and np.isfinite(v) and v > 0 else None


def atm_iv_at_expiry(sub, S, T):
    """某到期月的 ATM IV：K=S 处按行权价线性插值（K<S 用认沽、K>=S 用认购）。"""
    pts = []
    for _, r in sub.iterrows():
        K = float(r["K"])
        if not np.isfinite(K) or K <= 0 or abs(K / S - 1.0) > ATM_BAND:
            continue
        px = r["px"]
        if px is None or not np.isfinite(px) or px <= 0:
            continue
        iv = solve_iv(float(px), S, K, T, r["call_put"])
        if iv is None:
            continue
        pts.append((K, iv, r["call_put"]))
    if not pts:
        return None
    otm = [(K, iv) for K, iv, cp in pts if (cp == "P" and K < S) or (cp == "C" and K >= S)]
    use = otm if len(otm) >= 2 else [(K, iv) for K, iv, _ in pts]
    use.sort()
    for i in range(len(use) - 1):
        K0, iv0 = use[i]; K1, iv1 = use[i + 1]
        if K0 <= S <= K1:
            if K1 == K0:
                return iv0
            return iv0 + (iv1 - iv0) * (S - K0) / (K1 - K0)
    return min(use, key=lambda t: abs(t[0] - S))[1]


def qvix_for_date(ch, opt_code, S, date_int):
    sub = ch[ch["opt_code"] == opt_code]
    if not len(sub):
        return None
    mats = sorted({int(m) for m in sub["mat"].unique() if int(m) > date_int})
    if not mats:
        return None
    pairs = []
    for mm in mats:
        dte = (pd.to_datetime(str(mm)) - pd.to_datetime(str(date_int))).days
        if dte <= 0:
            continue
        s2 = sub[sub["mat"].astype(int) == mm]
        iv = atm_iv_at_expiry(s2, S, dte / 365.0)
        if iv is not None:
            pairs.append((dte, iv))
    if not pairs:
        return None
    pairs.sort()
    lo = [p for p in pairs if p[0] <= 30]
    hi = [p for p in pairs if p[0] >= 30]
    if lo and hi:
        (T1d, iv1), (T2d, iv2) = lo[-1], hi[0]
    elif len(pairs) >= 2:
        (T1d, iv1), (T2d, iv2) = pairs[0], pairs[1]
    else:
        return float(pairs[0][1] * 100)
    T1, T2 = T1d / 365.0, T2d / 365.0
    if T2 <= T1:
        return float(iv1 * 100)
    v1, v2 = iv1 * iv1 * T1, iv2 * iv2 * T2
    vtot = v1 + (v2 - v1) * (T30 - T1) / (T2 - T1)   # 30 日累计方差
    if vtot <= 0:
        return None
    return float(math.sqrt(vtot / T30) * 100)         # 年化 -> QVIX 点位


def main():
    with open(os.path.join(DATA, "trade_cal.json"), encoding="utf-8") as f:
        dates = [str(d) for d in json.load(f)["last252"]]
    basics = pd.concat([pd.read_json(os.path.join(DATA, f), orient="records",
                                     dtype={"ts_code": str})
                        for f in ["basic_sse.json", "basic_szse.json", "basic_cffex.json",
                                  "basic_extra.json"]], ignore_index=True).drop_duplicates("ts_code")
    bmap = basics.set_index("ts_code")[["opt_code", "call_put", "exercise_price",
                                        "maturity_date"]].to_dict("index")
    uh = pd.read_json(os.path.join(DATA, "underlying_hist.json"), orient="records")
    uh["trade_date"] = uh["trade_date"].astype(str)
    spot_hist = {c: dict(zip(g["trade_date"], g["close"])) for c, g in uh.groupby("code")}

    recs_by = {name: [] for name in MAP}
    for date in dates:
        p = os.path.join(CH, date + ".json")
        if not os.path.exists(p):
            continue
        ch = pd.read_json(p, orient="records", dtype={"ts_code": str})
        ch = ch[ch["ts_code"].isin(bmap.keys())].copy()
        if not len(ch):
            continue
        ch["opt_code"] = [bmap[t]["opt_code"] for t in ch["ts_code"]]
        ch["call_put"] = [bmap[t]["call_put"] for t in ch["ts_code"]]
        ch["K"] = [bmap[t]["exercise_price"] for t in ch["ts_code"]]
        ch["mat"] = [bmap[t]["maturity_date"] for t in ch["ts_code"]]
        ch["px"] = ch["settle"].where(ch["settle"].fillna(0) > 0, ch["close"])
        for name, oc in MAP.items():
            S = spot_hist.get(SPOT_CODE[name], {}).get(date)
            if not S or not np.isfinite(S):
                continue
            v = qvix_for_date(ch, oc, float(S), int(date))
            if v is not None:
                recs_by[name].append(dict(date=date, close=v))

    for name, recs in recs_by.items():
        if not recs:
            print("WARN %s 未算出任何 QVIX 样本" % name)
            continue
        df = pd.DataFrame(recs)
        # 日期统一写成毫秒时间戳（与旧 QVIX 文件同构；forecast_vol 按 ms 解析）
        df["date"] = (pd.to_datetime(df["date"]).astype("datetime64[ns]")
                      .astype("int64") // 10 ** 6)
        for c in ("open", "high", "low"):
            df[c] = df["close"]
        df = df[["date", "open", "high", "low", "close"]]
        df.to_json(os.path.join(DATA, name + ".json"), orient="records",
                   force_ascii=False)
        print("saved", name, df.shape, "last", df.tail(1).to_dict("records"))

    print("DONE fetch_qvix (自算 30 日恒定期限 ATM IV)")


if __name__ == "__main__":
    main()