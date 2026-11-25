# -*- coding: utf-8 -*-
"""中美期权偏度与 VVIX 分位监控（并入《中美期权风险释放汇总》每日链路的附加步骤）

口径（用户 2026-10-25 确认，不得更改）：
  · A股 5 标的：近月合约（剩余 > 20 个自然日）的 25Δ 认沽 IV − 25Δ 认购 IV，
    delta 按各合约自身 IV 计算、在 |delta| = 0.25 处按 delta 线性插值。
  · 美股：CBOE SKEW 指数（标普500 官方偏度指数）。
  · 美股：CBOE VVIX（标普500，波动率的波动率 = VIX 期权隐含波动率）。
  · 各标的以「自身近一年 252 个交易日」同序列算分位；分位 ≥ 阈值即报警。
  · 报警阈值默认 90（用户口径：大于 90 分位），可用环境变量 SKEW_ALARM_THR 覆盖；
    A股偏度 / 美股 SKEW / 美股 CBOE VVIX 三组共用同一阈值。
  · A股 5 标的 VVIX（回望口径，见 vvix_ashare.py）已按用户口径停用：本步不再读取
    data/vvix_ashare.json，对应汇总块、报警项与打印一并移除；美股 CBOE VVIX 保留。

输入：data/trade_cal.json、data/basic_*.json、data/underlying_hist.json、
      data/chains/*.json、data/us_SKEW.json、data/us_VVIX.json
输出：data/skew_pct_cn_raw.json（逐日明细）、data/skew_pct_summary.json（最新截面 + 分位 + 报警；
      含 thr / cn / us / vvix 四块，后三者各为独立一块）

可用 TRADE_DATE=YYYYMMDD 限定数据日（与主链路一致）；未给则取 chains 中最新可得交易日。
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
MIN_DTE = 20          # 近月门槛：剩余自然日 > 20
WIN = 252             # 一年交易日
THR = float(os.environ.get("SKEW_ALARM_THR", "90"))

TARGETS = {
    "300ETF": dict(opt="OP510300.SH", spot="510300.SH", label="沪深300ETF期权"),
    "500ETF": dict(opt="OP510500.SH", spot="510500.SH", label="中证500ETF期权"),
    "KC50":   dict(opt="OP588000.SH", spot="588000.SH", label="科创50ETF期权"),
    "CYB":    dict(opt="OP159915.SZ", spot="159915.SZ", label="创业板ETF期权"),
    "ZZ1000": dict(opt="OP000852.SH", spot="000852.SH", label="中证1000股指期权(MO)"),
}


def N(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(S, K, T, s, r, cp):
    """BS 理论价（转调 options_utility.calc.option_monitor）。"""
    if T <= 0 or s <= 0:
        return max(0.0, (S - K) if cp == "C" else (K - S))
    return LIB.bs_call(S, K, T, r, s) if cp == "C" else LIB.bs_put(S, K, T, r, s)


def bs_delta(S, K, T, s, r, cp):
    """BS delta（转调 calc.cn.skew_dual.bs_delta，带符号）。"""
    if T <= 0 or s <= 0:
        return (1.0 if cp == "C" and S > K else 0.0)
    return LIB.lib_bs_delta(S, K, T, s, cp == "C")


def solve_iv(price, S, K, T, cp, lo=0.005, hi=3.0):
    """反解 IV（转调 calc.option_monitor.bs_iv）。"""
    if price is None or not np.isfinite(price) or price <= 0 or T <= 0:
        return None
    intr = max(0.0, (S - K) if cp == "C" else (K - S))
    if price < intr - 1e-9:
        return None
    v = LIB.bs_iv(S, K, T, R_FREE, float(price), "call" if cp == "C" else "put")
    return float(v) if v is not None and np.isfinite(v) and v > 0 else None


def pick25(items):
    """items: [(x, iv)]，x=认沽的 |delta| 或认购的 delta；线性插值出 x=0.25 处的 IV

    插值逻辑转调 calc.cn.gex_weighted.interp_at；落在样本区间外时沿用「取最近档」的
    本地口径（不外推），保证与历史序列可比。
    """
    items = [(x, iv) for x, iv in items if iv is not None and np.isfinite(iv) and x > 0.02]
    if not items:
        return None
    items.sort()
    v, mode = LIB.interp_at(items, 0.25)
    if mode in ("interp", "exact", "single") and np.isfinite(v):
        return float(v)
    return min(items, key=lambda t: abs(t[0] - 0.25))[1]


def pct_rank(arr, val):
    """分位（转调 calc.cn.gex_weighted.percentile_rank）。"""
    if val is None or not np.isfinite(val):
        return None
    p, n = LIB.lib_percentile_rank(list(arr), float(val))
    if not n or not np.isfinite(p):
        return None
    return float(p)


def _dates():
    """数据日序列：优先 trade_cal.last252，否则退化为 chains 目录文件名排序。"""
    cut = os.environ.get("TRADE_DATE")
    cal_p = os.path.join(DATA, "trade_cal.json")
    dates = None
    if os.path.exists(cal_p):
        try:
            dates = [str(d) for d in json.load(open(cal_p, encoding="utf-8"))["last252"]]
        except Exception:
            dates = None
    if not dates:
        dates = sorted(f[:-5] for f in os.listdir(CH) if f.endswith(".json"))
    if cut:
        dates = [d for d in dates if d <= str(cut)]
    return dates[-WIN:]


def main():
    dates = _dates()
    if not dates:
        raise SystemExit("skew monitor: 无可用交易日序列")
    print("[skew] 交易日序列 %s ~ %s (%d 日), 阈值 %g%%"
          % (dates[0], dates[-1], len(dates), THR))

    basics = pd.concat([pd.read_json(os.path.join(DATA, f), orient="records",
                                     dtype={"ts_code": str})
                        for f in ["basic_sse.json", "basic_szse.json", "basic_cffex.json",
                                  "basic_extra.json"]], ignore_index=True)
    basics = basics.drop_duplicates("ts_code")
    bmap = basics.set_index("ts_code")[
        ["opt_code", "call_put", "exercise_price", "maturity_date", "per_unit"]].to_dict("index")

    uh = pd.read_json(os.path.join(DATA, "underlying_hist.json"), orient="records")
    uh["trade_date"] = uh["trade_date"].astype(str)
    spot_hist = {c: dict(zip(g["trade_date"], g["close"])) for c, g in uh.groupby("code")}

    recs = []
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
        d_int = int(date)

        for tkey, cfg in TARGETS.items():
            S = spot_hist.get(cfg["spot"], {}).get(date)
            if not S or not np.isfinite(S):
                continue
            sub = ch[ch["opt_code"] == cfg["opt"]]
            if len(sub) < 6:
                continue
            mats = sorted(set(int(m) for m in sub["mat"].unique() if int(m) > d_int))
            cand = [m for m in mats
                    if (pd.to_datetime(str(m)) - pd.to_datetime(date)).days > MIN_DTE]
            if not cand:
                continue
            mm = cand[0]
            T = (pd.to_datetime(str(mm)) - pd.to_datetime(date)).days / 365.0
            s2 = sub[sub["mat"].astype(int) == mm]
            puts, calls = [], []
            for _, r in s2.iterrows():
                if abs(r["K"] / S - 1.0) > 0.30 or not np.isfinite(r["px"]) or r["px"] <= 0:
                    continue
                iv = solve_iv(r["px"], S, r["K"], T, r["call_put"])
                if iv is None:
                    continue
                dl = bs_delta(S, r["K"], T, iv, R_FREE, r["call_put"])
                if r["call_put"] == "P":
                    puts.append((abs(dl), iv))
                else:
                    calls.append((dl, iv))
            ivp = pick25(puts); ivc = pick25(calls)
            if ivp is None or ivc is None:
                continue
            recs.append(dict(date=date, target=tkey, spot=float(S), mat=int(mm),
                             dte=int((pd.to_datetime(str(mm)) - pd.to_datetime(date)).days),
                             iv25p=float(ivp), iv25c=float(ivc), skew=float(ivp - ivc)))

    if not recs:
        raise SystemExit("skew monitor: 未算出任何偏度样本")

    df = pd.DataFrame(recs).sort_values(["target", "date"])
    df.to_json(os.path.join(DATA, "skew_pct_cn_raw.json"), orient="records",
               force_ascii=False, indent=1)

    print("== A股 25D(近月>20天) 偏度 = 认沽IV − 认购IV，单位: IV点数 ==")
    cn_sum = {}
    for t, g in df.groupby("target"):
        g = g.sort_values("date")
        w = g.tail(WIN)
        cur = w.iloc[-1]
        pct = pct_rank(w["skew"].tolist(), cur["skew"])
        cn_sum[t] = dict(label=TARGETS[t]["label"], date=cur["date"], mat=int(cur["mat"]),
                         dte=int(cur["dte"]),
                         iv25p=round(float(cur["iv25p"]) * 100, 2),
                         iv25c=round(float(cur["iv25c"]) * 100, 2),
                         skew=round(float(cur["skew"]) * 100, 2),
                         pct=round(pct, 1), n=len(w),
                         p90=round(float(np.percentile(w["skew"], 90)) * 100, 2),
                         alarm=bool(pct >= THR))
        print("  %-7s %s 到期%d(%dd)  25D认沽=%.2f%%  25D认购=%.2f%%  偏度=%.2fpt  "
              "一年分位=%.1f%%  (P90=%.2f)  %s"
              % (t, cur["date"], int(cur["mat"]), int(cur["dte"]),
                 cur["iv25p"] * 100, cur["iv25c"] * 100, cur["skew"] * 100, pct,
                 np.percentile(w["skew"], 90) * 100, "🔴报警" if pct >= THR else ""))

    # ---------- 美股：CBOE SKEW 指数 ----------
    sk = pd.read_json(os.path.join(DATA, "us_SKEW.json"), orient="records")
    sk["d"] = pd.to_datetime(sk["DATE"])
    sk = sk.dropna(subset=["SKEW"]).sort_values("d")
    cut = os.environ.get("TRADE_DATE")
    if cut:
        sk = sk[sk["d"] <= pd.to_datetime(str(cut))]
    w = sk.tail(WIN)
    cur = sk.iloc[-1]
    sk_pct = pct_rank(w["SKEW"].tolist(), float(cur["SKEW"]))
    us_sum = dict(label="标普500 CBOE SKEW 指数", date=cur["d"].strftime("%Y-%m-%d"),
                  skew=round(float(cur["SKEW"]), 2), pct=round(sk_pct, 1), n=len(w),
                  p90=round(float(np.percentile(w["SKEW"], 90)), 2),
                  med=round(float(np.median(w["SKEW"])), 2),
                  alarm=bool(sk_pct >= THR))
    print("\n== 美股 偏度 = CBOE SKEW 指数(标普500) ==")
    print("  标普500 SKEW=%.2f (%s)  一年分位=%.1f%%  (P90=%.2f, 中位=%.2f)  %s"
          % (cur["SKEW"], us_sum["date"], sk_pct, us_sum["p90"], us_sum["med"],
             "🔴报警" if sk_pct >= THR else ""))

    # ---------- 美股：CBOE VVIX（波动率的波动率 = VIX 期权隐含波动率） ----------
    vv = pd.read_json(os.path.join(DATA, "us_VVIX.json"), orient="records")
    vv["d"] = pd.to_datetime(vv["DATE"])
    vv = vv.dropna(subset=["VVIX"]).sort_values("d")
    if cut:
        vv = vv[vv["d"] <= pd.to_datetime(str(cut))]
    wv = vv.tail(WIN)
    vv_cur = vv.iloc[-1]
    vv_pct = pct_rank(wv["VVIX"].tolist(), float(vv_cur["VVIX"]))
    vv_sum = dict(label="标普500 CBOE VVIX（波动率的波动率）",
                  date=vv_cur["d"].strftime("%Y-%m-%d"),
                  vvix=round(float(vv_cur["VVIX"]), 2), pct=round(vv_pct, 1), n=len(wv),
                  p90=round(float(np.percentile(wv["VVIX"], 90)), 2),
                  med=round(float(np.median(wv["VVIX"])), 2),
                  alarm=bool(vv_pct >= THR))
    print("\n== 美股 VVIX = 波动率的波动率（标普500，VIX 期权隐含波动率）==")
    print("  标普500 VVIX=%.2f (%s)  一年分位=%.1f%%  (P90=%.2f, 中位=%.2f)  %s"
          % (vv_cur["VVIX"], vv_sum["date"], vv_pct, vv_sum["p90"], vv_sum["med"],
             "🔴报警" if vv_pct >= THR else ""))

    # A股 5 标的 VVIX（回望口径）已按用户口径停用：不再读取 data/vvix_ashare.json，不再输出该块。

    out = dict(thr=THR, cn=cn_sum, us=us_sum, vvix=vv_sum)
    with open(os.path.join(DATA, "skew_pct_summary.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("\nsaved -> data/skew_pct_summary.json / data/skew_pct_cn_raw.json")
    print("ALARM_CN:", [k for k, v in cn_sum.items() if v["alarm"]],
          "| ALARM_US:", [us_sum["label"]] if us_sum["alarm"] else [],
          "| ALARM_VVIX:", [vv_sum["label"]] if vv_sum["alarm"] else [])


if __name__ == "__main__":
    main()