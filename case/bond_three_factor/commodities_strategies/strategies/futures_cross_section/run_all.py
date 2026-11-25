# -*- coding: utf-8 -*-
"""跑全部回测配置并输出结果表（单因子 / 组合 / 风控归因 / 敏感性 / 分年度 / 分体制 / 极端月份 / 样本内外）。"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
from .common import DATA, OUT, log
from . import engine as E

TAGS = ["mom", "carry", "bmom", "skew", "hp_chg"]
MKEY = ["n", "cagr", "vol", "sharpe", "sortino", "mdd", "calmar", "nav", "hit", "skew", "tail5"]
IS_END = "2016-12-31"

def pools_runs(Rt, sigs, blocks, full, sig_key, cfg):
    br = {}
    for b, v in blocks.items():
        v = [x for x in v if x in Rt.columns]
        if len(v) >= 6:
            br[b] = E.run_pool(Rt, sigs[sig_key], v, cfg)
    blk = pd.concat([v["pnl"].rename(b) for b, v in br.items()], axis=1).mean(axis=1) if br else pd.Series(dtype=float)
    fl = E.run_pool(Rt, sigs[sig_key], full, cfg)
    return blk, fl["pnl"], br, fl

def row(tag, series, br=None, fl=None, extra=None):
    m = E.metrics(series)
    r = {"config": tag}
    r.update({k: m.get(k, np.nan) for k in MKEY})
    g = []
    if br:
        g += [x["recs"]["gross"].mean() for x in br.values()]
    if fl is not None:
        g += [fl["recs"]["gross"].mean()]
    r["mean_gross"] = float(np.mean(g)) if g else np.nan
    r["mean_turnover"] = float(np.mean([x["recs"]["turnover"].mean() for x in (list(br.values()) if br else [])] + ([fl["recs"]["turnover"].mean()] if fl is not None else []))) if (br or fl is not None) else np.nan
    if extra:
        r.update(extra)
    return r

def main():
    p, dirs = E.load()
    liq, keep, full, blocks = E.universe(p)
    Rt = E.wide(p, "ret_near")
    sigs = E.build_signals(p, dirs)
    liqj = keep[["variety", "first", "days", "amt"]].copy()
    liqj["first"] = liqj["first"].astype(str)
    json.dump({"full": full, "blocks": blocks, "liq": liqj.to_dict("records")},
              open(os.path.join(OUT, "universe.json"), "w"), ensure_ascii=False, indent=1)
    log("pool", len(full), blocks)

    # ---------- A 单因子 ----------
    rows = []
    store_a = {}
    for f in TAGS:
        for pl in ["block", "full"]:
            for sp in ["trad", "at"]:
                cfg = dict(spread=sp, weight="equal", cost_bp=5.0, params={})
                blk, fp, br, fl = pools_runs(Rt, sigs, blocks, full, (f, "12m"), cfg)
                ser = blk if pl == "block" else fp
                store_a[(f, pl, sp)] = ser
                rows.append(row(f"{f}|{'同板块' if pl=='block' else '全市场'}|{'传统价差' if sp=='trad' else 'AT价差'}",
                                ser, br if pl == "block" else None, fl) | {"factor": f, "pool": pl, "spread": sp})
    for f in TAGS:
        for pl in ["block", "full"]:
            for cost, nm in [(10.0, "10bp"), (20.0, "20bp")]:
                cfg2 = dict(spread="trad", weight="equal", cost_bp=cost, params={})
                b2, f2, _, _ = pools_runs(Rt, sigs, blocks, full, (f, "12m"), cfg2)
                rows.append(row(f"{f}|{'同板块' if pl=='block' else '全市场'}|成本{nm}",
                                b2 if pl == "block" else f2, None, None) | {"factor": f, "pool": pl, "spread": nm})
    pd.DataFrame(rows).to_csv(os.path.join(OUT, "single_factor.csv"), index=False)
    log("A 单因子", len(rows))

    # ---------- B 组合层面（三档权重 × 两价差 × 两池） ----------
    rows, keep_runs = [], {}
    for sp in ["trad", "at"]:
        for w in ["equal", "gmv", "rp"]:
            for pl in ["block", "full"]:
                cfg = dict(spread=sp, weight=w, cost_bp=5.0, params={})
                blk, fp, br, fl = pools_runs(Rt, sigs, blocks, full, ("triple", "12m"), cfg)
                rows.append(row(f"三重筛选|{'传统价差' if sp=='trad' else 'AT价差'}|{w}|{'同板块' if pl=='block' else '全市场'}",
                                blk if pl == "block" else fp, br if pl == "block" else None, fl)
                            | {"spread": sp, "weight": w, "pool": pl})
                if sp == "trad" and w == "equal":
                    keep_runs[pl] = (blk, fp, br, fl)
    # 三重筛选 vs 单因子（组合层面主对照）
    for f in ["skew", "mom", "carry"]:
        cfg = dict(spread="trad", weight="equal", cost_bp=5.0, params={})
        blk, fp, br, fl = pools_runs(Rt, sigs, blocks, full, (f, "12m"), cfg)
        rows.append(row(f"单因子{f}|传统价差|equal|同板块", blk, br, None) | {"spread": "trad", "weight": "equal", "pool": "block"})
        rows.append(row(f"单因子{f}|传统价差|equal|全市场", fp, None, fl) | {"spread": "trad", "weight": "equal", "pool": "full"})
    pd.DataFrame(rows).to_csv(os.path.join(OUT, "portfolio.csv"), index=False)
    log("B 组合", len(rows))

    # ---------- C 风控开关归因 ----------
    seq = [("无风控层", set()), ("+单品种波动率目标", {"vol_name"}), ("+组合波动率目标", {"vol_name", "vol_port"}),
           ("+分数Kelly与杠杆上限", {"vol_name", "vol_port", "kelly"}), ("+无交易带", {"vol_name", "vol_port", "kelly", "band"}),
           ("+相关性降杠杆", {"vol_name", "vol_port", "kelly", "band", "corr"}),
           ("+200日均线回撤保护", {"vol_name", "vol_port", "kelly", "band", "corr", "dd_ma"}),
           ("+波动率快崩保护(全套)", set(E.FULL))]
    def extras_of(frames):
        e = {}
        fr = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        for c in ["kelly", "avg_corr", "corr_delev", "dd_ma_on", "dd_vol_on"]:
            e[c + "_mean"] = float(fr[c].mean()) if c in fr.columns else np.nan
        return e
    rows = []
    for nm, flg in seq:
        cfg = dict(spread="trad", weight="equal", cost_bp=5.0, flags=flg, params={})
        blk, fp, br, fl = pools_runs(Rt, sigs, blocks, full, ("triple", "12m"), cfg)
        eb = extras_of([v["recs"] for v in br.values()]) if br else {}
        ef = extras_of([fl["recs"]]) if fl is not None else {}
        eb["flags"] = ef["flags"] = ",".join(sorted(flg))
        rows.append(row(nm + "|同板块", blk, br, None, eb))
        rows.append(row(nm + "|全市场", fp, None, fl, ef))
    pd.DataFrame(rows).to_csv(os.path.join(OUT, "risk_attr.csv"), index=False)
    log("C 风控归因", len(rows))

    # ---------- D 参数敏感性 ----------
    rows = []
    base = dict(spread="trad", weight="equal", cost_bp=5.0, params={})
    for k in ["6m", "9m", "12m", "18m"]:
        for pl in ["block", "full"]:
            blk, fp, br, fl = pools_runs(Rt, sigs, blocks, full, ("triple", k), dict(base))
            rows.append(row(f"窗口{k}|动量+基差动量|{'同板块' if pl=='block' else '全市场'}",
                            blk if pl == "block" else fp, br if pl == "block" else None, fl)
                        | {"param": "窗口", "value": k, "pool": pl})
    for fq, nm in [("M", "月度"), ("2W", "双周"), ("W", "周度")]:
        for pl in ["block", "full"]:
            cfg = dict(base); cfg["params"] = {"reb": fq}
            blk, fp, br, fl = pools_runs(Rt, sigs, blocks, full, ("triple", "12m"), cfg)
            rows.append(row(f"调仓{nm}|{'同板块' if pl=='block' else '全市场'}",
                            blk if pl == "block" else fp, br if pl == "block" else None, fl)
                        | {"param": "调仓频率", "value": nm, "pool": pl})
    for t in [0.12, 0.15, 0.25]:
        for pl in ["block", "full"]:
            cfg = dict(base); cfg["tgt"] = t
            blk, fp, br, fl = pools_runs(Rt, sigs, blocks, full, ("triple", "12m"), cfg)
            rows.append(row(f"目标波动率{t:.0%}|{'同板块' if pl=='block' else '全市场'}",
                            blk if pl == "block" else fp, br if pl == "block" else None, fl)
                        | {"param": "目标波动率", "value": t, "pool": pl})
    for q in [0.50, 0.60, 0.70]:
        for pl in ["block", "full"]:
            cfg = dict(base); cfg["params"] = {"q": q}
            blk, fp, br, fl = pools_runs(Rt, sigs, blocks, full, ("triple", "12m"), cfg)
            rows.append(row(f"阈值分位{q:.2f}|{'同板块' if pl=='block' else '全市场'}",
                            blk if pl == "block" else fp, br if pl == "block" else None, fl)
                        | {"param": "阈值分位", "value": q, "pool": pl})
    pd.DataFrame(rows).to_csv(os.path.join(OUT, "sensitivity.csv"), index=False)
    log("D 敏感性", len(rows))

    # ---------- E 分年度 / 分体制 / 极端月份 / 样本内外 / 协方差分解 ----------
    main_runs = {}
    for pl in ["block", "full"]:
        cfg = dict(spread="trad", weight="equal", cost_bp=5.0, flags=set(E.FULL), params={})
        blk, fp, br, fl = pools_runs(Rt, sigs, blocks, full, ("triple", "12m"), cfg)
        main_runs[pl] = (blk if pl == "block" else fp, fl, br)
    ann, ext, isoos = [], [], []
    for pl, (s, fl, br) in main_runs.items():
        mm = s.resample("ME").sum()
        for y, g in mm.groupby(mm.index.year):
            ann.append({"pool": pl, "year": y, "n_months": len(g), "sum": g.sum(),
                        "mean": g.mean(), "pos": (g > 0).mean(), "cum_nav": (1 + g).prod()})
        top = mm.nlargest(max(1, int(round(len(mm) * 0.05))))
        rest = mm.drop(top.index)
        ext.append({"pool": pl, "n_months": len(mm), "best5_n": len(top),
                    "best5_sum": top.sum(), "total_sum": mm.sum(),
                    "share_best5": top.sum() / mm.sum() if mm.sum() != 0 else np.nan,
                    "mean_all": mm.mean(), "mean_ex_best5": rest.mean(),
                    "ann_all": (1 + mm).prod() ** (12 / len(mm)) - 1,
                    "ann_ex_best5": (1 + rest).prod() ** (12 / len(rest)) - 1})
        for nm, seg in [("样本内 2010-2016", s[s.index <= IS_END]), ("样本外 2017-2026", s[s.index > IS_END]),
                        ("全样本", s), ("2026 年初至今", s[s.index >= "2026-01-01"])]:
            m = E.metrics(seg)
            isoos.append({"pool": pl, "period": nm, **{k2: m.get(k2, np.nan) for k2 in MKEY}})
        s.to_csv(os.path.join(OUT, f"nav_{pl}.csv"), header=["ret"])
        fl["recs"].to_csv(os.path.join(OUT, f"recs_{pl}.csv"), index=False)
        if br:
            pd.concat({b: v["pnl"] for b, v in br.items()}, axis=1).to_csv(os.path.join(OUT, "nav_blocks.csv"))
    pd.DataFrame(ann).to_csv(os.path.join(OUT, "annual.csv"), index=False)
    pd.DataFrame(ext).to_csv(os.path.join(OUT, "extreme.csv"), index=False)
    pd.DataFrame(isoos).to_csv(os.path.join(OUT, "is_oos.csv"), index=False)

    # 体制分类（仅用于归因，不参与交易）
    pool = full
    vol_i = Rt[pool].rolling(20).std() * np.sqrt(252)
    agg = vol_i.median(axis=1)
    disp = (Rt[pool].rolling(21).sum()).std(axis=1)
    ma = agg.resample("ME").last()
    md = disp.resample("ME").last()
    lab = pd.Series(index=ma.index, dtype=object)
    for d in ma.index:
        a = ma[d] > ma.median()
        b = md[d] > md.median()
        lab[d] = "高波动高离散" if (a and b) else "高波动低离散" if a else "低波动高离散" if b else "低波动低离散"
    lab.to_frame("regime").to_csv(os.path.join(OUT, "regimes.csv"))
    reg_rows = []
    for pl, (s, fl, br) in main_runs.items():
        mm = s.resample("ME").sum()
        mm.index = mm.index.to_period("M")
        labp = lab.copy(); labp.index = labp.index.to_period("M")
        df = pd.concat([mm.rename("r"), labp.rename("regime")], axis=1).dropna()
        for rg, g in df.groupby("regime"):
            reg_rows.append({"pool": pl, "regime": rg, "n_months": len(g), "mean": g["r"].mean(),
                             "ann": (1 + g["r"]).prod() ** (12 / len(g)) - 1, "pos": (g["r"] > 0).mean(),
                             "vol": g["r"].std() * np.sqrt(12)})
    pd.DataFrame(reg_rows).to_csv(os.path.join(OUT, "regime_perf.csv"), index=False)

    # 协方差分解（GMV / 风险平价）
    cd = []
    for w in ["gmv", "rp", "equal"]:
        cfg = dict(spread="trad", weight=w, cost_bp=5.0, flags=set(E.FULL), params={})
        _, _, _, fl = pools_runs(Rt, sigs, blocks, full, ("triple", "12m"), cfg)
        d = fl["recs"].dropna(subset=["diag_var"])
        cd.append({"weight": w, "pool": "full", "n_reb": len(d),
                   "diag_var_ann": d["diag_var"].mean(), "offdiag_var_ann": d["offdiag_var"].mean(),
                   "cost_ann": d["cost"].mean() * 12, "gross": d["gross"].mean(),
                   "offdiag_net_cost": d["offdiag_var"].mean() - d["cost"].mean() * 12})
    pd.DataFrame(cd).to_csv(os.path.join(OUT, "cov_decomp.csv"), index=False)
    # ---------- A2 单因子分年度 / 分体制 ----------
    fa, fr = [], []
    labp0 = lab.copy()
    labp0.index = labp0.index.to_period("M")
    for (f, pl, sp), s in store_a.items():
        mm = s.resample("ME").sum()
        for y, gg in mm.groupby(mm.index.year):
            fa.append({"factor": f, "pool": pl, "spread": sp, "year": int(y), "n": len(gg),
                       "sum": gg.sum(), "mean": gg.mean(), "pos": (gg > 0).mean()})
        mp = mm.copy()
        mp.index = mp.index.to_period("M")
        dfa = pd.concat([mp.rename("r"), labp0.rename("regime")], axis=1).dropna()
        for rg, gg in dfa.groupby("regime"):
            fr.append({"factor": f, "pool": pl, "spread": sp, "regime": rg, "n_months": len(gg),
                       "mean": gg["r"].mean(), "ann": (1 + gg["r"]).prod() ** (12 / len(gg)) - 1,
                       "pos": (gg["r"] > 0).mean()})
    pd.DataFrame(fa).to_csv(os.path.join(OUT, "factor_annual.csv"), index=False)
    pd.DataFrame(fr).to_csv(os.path.join(OUT, "factor_regime.csv"), index=False)
    # 主配置分年度（同板块 / 全市场）
    ma = []
    for pl, (s, fl, br) in main_runs.items():
        mm = s.resample("ME").sum()
        for y, gg in mm.groupby(mm.index.year):
            ma.append({"pool": pl, "year": int(y), "n": len(gg), "sum": gg.sum(), "mean": gg.mean(),
                       "pos": (gg > 0).mean()})
        for b, v in (br or {}).items():
            mb = v["pnl"].resample("ME").sum()
            for y, gg in mb.groupby(mb.index.year):
                ma.append({"pool": f"block_{b}", "year": int(y), "n": len(gg), "sum": gg.sum(),
                           "mean": gg.mean(), "pos": (gg > 0).mean()})
    pd.DataFrame(ma).to_csv(os.path.join(OUT, "annual_detail.csv"), index=False)

    # ---------- F ℓ1 换手正则化叠加（高频调仓，路径依赖） ----------
    f1r = []
    for l1 in [None, 0.05, 0.10]:
        for fq in ["W", "2W"]:
            for pl in ["block", "full"]:
                cfg = dict(spread="trad", weight="equal", cost_bp=5.0, params={"reb": fq, "l1": l1})
                blk, fp, br, fl = pools_runs(Rt, sigs, blocks, full, ("triple", "12m"), cfg)
                f1r.append(row(f"调仓{fq}|ℓ1={'关' if l1 is None else format(l1, '.2f')}|{'同板块' if pl == 'block' else '全市场'}",
                               blk if pl == "block" else fp, br if pl == "block" else None, fl))
    pd.DataFrame(f1r).to_csv(os.path.join(OUT, "l1_overlay.csv"), index=False)
    log("F ℓ1 叠加", len(f1r))
    log("E 汇总完成")

if __name__ == "__main__":
    main()