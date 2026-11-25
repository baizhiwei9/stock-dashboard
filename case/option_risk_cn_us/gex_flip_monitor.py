# -*- coding: utf-8 -*-
"""A股五轨 期权 GEX / Gamma Flip 分位监控（并入《中美期权风险释放汇总》每日链路的附加步骤）

覆盖 A股五轨（与偏度监控同一批标的）：
  · 沪深300ETF 510300  （上交所 OP510300.SH，乘数 10000）
  · 中证500ETF 510500  （上交所 OP510500.SH，乘数 10000）
  · 科创50ETF  588000  （上交所 OP588000.SH，乘数 10000）
  · 创业板ETF  159915  （深交所 OP159915.SZ，乘数 10000）
  · 中证1000 MO 000852 （中金所 OP000852.SH，乘数 100）

口径（用户 2026-10-25 确认 + 《GEX_Flip 设计文档》v1.0，不得更改）：
  · 净 GEX = Σ [ sign · Γ · OI · MULT · S² · 0.01 ]（Call +1 / Put −1），单位亿元。
  · 期限：保留 DTE ∈ [30, 180]；无效（仅 1 个到期月）降级 quality=low。
  · 加权：30/90 恒定期限（VIX 式线性插值）gex_weighted = 0.5·gex30 + 0.5·gex90。
  · Gamma Flip：S*∈[0.90S, 1.10S] 步长 0.5% 重算全链净 GEX 求零点，flip_1 取离现价最近者。
  · Flip 距离 dist = (S − flip_1) / S × 100（%）。
  · 报警（红）：A = 净 GEX(gex_weighted) < 0 ；B = dist 的一年分位 ≤ 阈值（默认 5%）。
              两者同时满足 → 报警；quality=low 时降级为黄。逐标的独立判定。
  · 分位：窗口 252 个交易日（不含当日），最少样本 min_periods（默认 60）；可用
    GEX_PCT_WIN / GEX_PCT_MIN / GEX_PCT_THR 覆盖；r=0.02（与设计文档一致）。

输入：data/basic_*.json、data/underlying_hist.json、data/chains/*.json
输出：
  · data/gex_flip_series.json  —— {标的: [逐日快照, ...]}（供出图；只增量计算未缓存的交易日）
  · data/gex_flip_summary.json —— {asof, thr, win, r, targets:{标的: {...}}, alarms:[...], alarm, level}

可用 TRADE_DATE=YYYYMMDD 限定数据日（与主链路一致）；未给则取 chains 中最新可得交易日。
"""
import json
import os
import time

import numpy as np
import pandas as pd

from gex_core import (prepare_chain, daily_snapshot, percentile_rank, DEF_R)

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
CH = os.path.join(DATA, "chains")
SERIES = os.path.join(DATA, "gex_flip_series.json")
SUMMARY = os.path.join(DATA, "gex_flip_summary.json")

TARGETS = {
    "300ETF": dict(opt="OP510300.SH", spot="510300.SH", mult=10000.0,
                   label="沪深300ETF期权(510300)"),
    "500ETF": dict(opt="OP510500.SH", spot="510500.SH", mult=10000.0,
                   label="中证500ETF期权(510500)"),
    "KC50":   dict(opt="OP588000.SH", spot="588000.SH", mult=10000.0,
                   label="科创50ETF期权(588000)"),
    "CYB":    dict(opt="OP159915.SZ", spot="159915.SZ", mult=10000.0,
                   label="创业板ETF期权(159915)"),
    "ZZ1000": dict(opt="OP000852.SH", spot="000852.SH", mult=100.0,
                   label="中证1000股指期权(MO 000852)"),
}
ORDER = ["300ETF", "500ETF", "KC50", "CYB", "ZZ1000"]

WIN = int(os.environ.get("GEX_PCT_WIN", "252"))
MINP = int(os.environ.get("GEX_PCT_MIN", "60"))
THR = float(os.environ.get("GEX_PCT_THR", "5"))
R_FREE = DEF_R


def _dates():
    """数据日序列：chains 目录中实际落盘的交易日（升序），按 TRADE_DATE 截断。"""
    cut = os.environ.get("TRADE_DATE")
    dates = sorted(f[:-5] for f in os.listdir(CH) if f.endswith(".json"))
    if cut:
        dates = [d for d in dates if d <= str(cut)]
    return dates


def _load_basics():
    frames = []
    for f in ["basic_sse.json", "basic_szse.json", "basic_cffex.json", "basic_extra.json"]:
        p = os.path.join(DATA, f)
        if os.path.exists(p):
            frames.append(pd.read_json(p, orient="records", dtype={"ts_code": str}))
    basics = pd.concat(frames, ignore_index=True).drop_duplicates("ts_code")
    return basics.set_index("ts_code")[
        ["opt_code", "call_put", "exercise_price", "maturity_date", "per_unit"]].to_dict("index")


def _load_chains(date, bmap):
    """加载单个交易日的全部期权链，并补 opt_code/cp/K/maturity 列（供多标的切片）。"""
    p = os.path.join(CH, date + ".json")
    if not os.path.exists(p):
        return None
    ch = pd.read_json(p, orient="records", dtype={"ts_code": str})
    ch = ch[ch["ts_code"].isin(bmap.keys())].copy()
    if not len(ch):
        return None
    ch["opt_code"] = [bmap[t]["opt_code"] for t in ch["ts_code"]]
    ch["cp"] = [bmap[t]["call_put"] for t in ch["ts_code"]]
    ch["K"] = [bmap[t]["exercise_price"] for t in ch["ts_code"]]
    ch["maturity"] = [bmap[t]["maturity_date"] for t in ch["ts_code"]]
    return ch


def _load_series():
    """返回 {标的: {date: 快照}}；兼容旧的「单标的一维 list」缓存（挂到 KC50）。"""
    if os.path.exists(SERIES):
        try:
            obj = json.load(open(SERIES, encoding="utf-8"))
            if isinstance(obj, dict):
                return {k: {str(r["date"]): r for r in v} for k, v in obj.items()}
            if isinstance(obj, list) and obj:
                return {"KC50": {str(r["date"]): r for r in obj}}
        except Exception:
            pass
    return {}


def _save_series(cache):
    out = {k: [cache[k][d] for d in sorted(cache[k].keys())] for k in cache}
    with open(SERIES, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


def _summarize(key, cfg, recs):
    """单标的：最新截面 + dist 一年分位 + 报警。recs 为该标的按日期升序的快照列表。"""
    base = dict(key=key, label=cfg["label"], opt=cfg["opt"], spot=cfg["spot"],
                mult=cfg["mult"], thr=THR, win=WIN, min_periods=MINP, r=R_FREE,
                date=None, S=None, gex30=None, gex90=None, gex_weighted=None,
                gex_mix_simple=None, n_legs=0, n_maturities=0, dte_list="", quality="empty",
                flip_1=None, flip_2=None, flip_count=0, dist=None, pct=None, n_pct=0,
                thr_dist=None, cond_a=False, cond_b=False, breach=False,
                level="none", alarm=False, series_n=0)
    recs = [r for r in recs if r.get("S")]
    if not recs:
        base["error"] = "no-data"
        return base
    idx = len(recs) - 1
    cur = recs[idx]
    base["series_n"] = len(recs)
    base["date"] = cur["date"]
    base["S"] = round(float(cur["S"]), 4)
    for f in ("gex30", "gex90", "gex_weighted", "gex_mix_simple"):
        v = cur.get(f)
        base[f] = round(float(v), 4) if v is not None and np.isfinite(float(v)) else None
    base["n_legs"] = int(cur.get("n_legs", 0))
    base["n_maturities"] = int(cur.get("n_maturities", 0))
    base["dte_list"] = cur.get("dte_list", "")
    base["quality"] = cur.get("quality", "")
    base["flip_1"] = round(float(cur["flip_1"]), 4) if cur.get("flip_1") else None
    base["flip_2"] = round(float(cur["flip_2"]), 4) if cur.get("flip_2") else None
    base["flip_count"] = int(cur.get("flip_count", 0))
    dv = cur.get("dist")
    base["dist"] = round(float(dv), 2) if dv is not None and np.isfinite(float(dv)) else None

    dists = [float(r["dist"]) for r in recs if r.get("dist") is not None
             and np.isfinite(float(r["dist"]))]
    if idx == 0:
        past = dists
    else:
        past = dists[:-1][-WIN:] if len(dists) == len(recs) else \
            [float(r["dist"]) for r in recs[:idx]
             if r.get("dist") is not None and np.isfinite(float(r["dist"]))][-WIN:]

    if base["dist"] is not None:
        pct, n_pct = percentile_rank(past, float(base["dist"]))
    else:
        pct, n_pct = float("nan"), 0
    if not past or not np.isfinite(pct) or n_pct < MINP:
        pct = float("nan")
    base["pct"] = round(pct, 1) if np.isfinite(pct) else None
    base["n_pct"] = int(n_pct)
    base["thr_dist"] = round(float(np.percentile(past, THR)), 2) if past else None

    base["cond_a"] = bool(base["gex_weighted"] is not None and base["gex_weighted"] < 0)
    base["cond_b"] = bool(base["pct"] is not None and base["pct"] <= THR)
    base["breach"] = bool(base["dist"] is not None and base["dist"] < 0)
    if base["cond_a"] and base["cond_b"]:
        base["level"] = "yellow" if base["quality"] == "low" else "red"
    else:
        base["level"] = "none"
    base["alarm"] = base["level"] in ("red", "yellow")
    return base


def main():
    t0 = time.time()
    dates = _dates()
    if not dates:
        raise SystemExit("gex monitor: 无可用交易日序列")
    bmap = _load_basics()

    uh = pd.read_json(os.path.join(DATA, "underlying_hist.json"), orient="records")
    uh["trade_date"] = uh["trade_date"].astype(str)
    spot_hist = {c: dict(zip(g["trade_date"], g["close"])) for c, g in uh.groupby("code")}

    cache = _load_series()
    for k in TARGETS:
        cache.setdefault(k, {})
    need = {k: {d for d in dates if d not in cache[k]} for k in TARGETS}
    total_need = sum(len(v) for v in need.values())
    print("[gex] 交易日序列 %s ~ %s (%d 日)；待算 %d 个 标的×日 组合"
          % (dates[0], dates[-1], len(dates), total_need))

    for d in dates:
        todo = [k for k in ORDER if d in need[k]]
        if not todo:
            continue
        ch = _load_chains(d, bmap)
        if ch is None:
            continue
        for k in todo:
            cfg = TARGETS[k]
            S = spot_hist.get(cfg["spot"], {}).get(d)
            if not S or not np.isfinite(S):
                continue
            sub = ch[ch["opt_code"] == cfg["opt"]]
            if not len(sub):
                continue
            chain = prepare_chain(sub[["trade_date", "K", "cp", "maturity",
                                       "close", "settle", "oi"]])
            snap = daily_snapshot(chain, int(d), float(S), r=R_FREE,
                                  mult=cfg["mult"], with_flip=True)
            snap.pop("curve", None)
            snap.pop("legs", None)
            snap.pop("meta", None)
            snap["date"] = d
            cache[k][d] = snap

    _save_series(cache)

    targets_out = {}
    for k in ORDER:
        recs = [cache[k][d] for d in sorted(cache[k].keys())]
        targets_out[k] = _summarize(k, TARGETS[k], recs)

    alarms = [targets_out[k] for k in ORDER if targets_out[k]["alarm"]]
    lvl_rank = {"red": 2, "yellow": 1, "none": 0}
    top = "none"
    for a in alarms:
        if lvl_rank[a["level"]] > lvl_rank[top]:
            top = a["level"]
    asof = max([t["date"] for t in targets_out.values() if t.get("date")] or [dates[-1]])

    out = dict(asof=asof, thr=THR, win=WIN, min_periods=MINP, r=R_FREE,
               dte_lo=30, dte_hi=180, n_targets=len(ORDER),
               targets=targets_out, alarms=alarms,
               alarm=bool(alarms), level=top, n_alarm=len(alarms))
    with open(SUMMARY, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print("== A股五轨 期权 GEX / Gamma Flip（净GEX<0 且 dist 一年分位≤%.0f%%）==" % THR)
    lvl_disp = {"red": "🔴报警", "yellow": "🟡降级报警", "none": "—"}
    for k in ORDER:
        t = targets_out[k]
        if t.get("date") is None:
            print("  %-7s 无数据" % k)
            continue
        print("  %-7s %s S=%.4f 乘数=%g 腿数=%d 到期[%s] q=%s ｜ 净GEX=%s 亿元(<0:%s) ｜ "
              "Flip=%s dist=%s%% 一年分位=%s%%(n=%d) ≤%g%%:%s -> %s"
              % (k, t["date"], t["S"], t["mult"], t["n_legs"], t["dte_list"], t["quality"],
                 t["gex_weighted"], t["cond_a"], t["flip_1"], t["dist"], t["pct"],
                 t["n_pct"], THR, t["cond_b"], lvl_disp[t["level"]]))
    print("\nsaved -> data/gex_flip_summary.json / data/gex_flip_series.json  (%.1fs)"
          % (time.time() - t0))
    print("ALARM_GEXFLIP:", bool(alarms), "LEVEL:", top,
          "TARGETS:", [a["key"] for a in alarms])


if __name__ == "__main__":
    main()