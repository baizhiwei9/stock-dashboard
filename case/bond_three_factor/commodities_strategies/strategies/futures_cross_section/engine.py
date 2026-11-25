# -*- coding: utf-8 -*-
"""组合构建 + 四层风控 回测引擎（重设日重算权重，日度盯市）。

组合构建：信号分四档 → 组权重 gw（traditional 高减低 / Abelson–Tukey 全档加权）
          → 组内按 等权/GMV/风险平价 分配 → 四层风控缩放。
层1 波动率目标：单品种 w=σ_tgt/σ_i（EWMA，θ=0.94），组合级目标波动率；
层2 杠杆与下注：杠杆上限 2.0、分数 Kelly λ=0.40、Kelly 近零保底 25%；
层3 换手与成本：无交易带 b=0.02、阈值分位 q=0.60、单仓上限 0.6、成本 bp 情景；
层4 状态与降杠杆：相关性降杠杆、200 日均线慢熊、波动率目标快崩。

数值口径：横截面 z 分数按 ±3 截尾；GMV 用收缩协方差的解析逆（Σ⁻¹1 归一化，长仓侧截断负权重）；
风险平价用等风险贡献定点迭代。主循环用 numpy 向量化，避免逐日 pandas 开销。
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
from .common import DATA, OUT, log

BLOCKS = {
    "黑色": ["RB", "HC", "I", "J", "JM", "SF", "SM", "SS"],
    "能化": ["RU", "TA", "FG", "MA", "V", "FU", "PP", "L", "BU", "SC", "EB", "EG", "SA", "UR", "PF", "PG", "NR", "SP", "PX"],
    "农产品": ["P", "M", "OI", "Y", "CF", "SR", "RM", "C", "JD", "A", "B", "CS", "AP", "LH", "PK"],
    "有色及贵金属": ["AU", "AG", "CU", "SN", "NI", "AL", "ZN", "PB", "LC", "SI", "AO"],
}
# 金融期货（股指/国债）与航运指数不属于商品板块，整体剔除
EXCLUDE = ["IM", "IF", "IC", "IH", "T", "TF", "TS", "TL", "EC"]
DEF = dict(theta=0.94, vol_name=0.25, band=0.02, q=0.60, name_cap=0.6, lev_cap=2.0,
           kelly_lam=0.40, kelly_floor=0.25, kelly_win=24, corr_win=60, cost_bp=5.0,
           reb="M", win="12m", tgt=0.15, corr_delev=0.6, dd_mult=0.5, vol_crash=1.5, nav_ma=200, l1=None)
AT_W = [-0.375, -0.125, 0.125, 0.375]
TRAD_W = [-0.5, 0.0, 0.0, 0.5]
LAYERS = ["vol_name", "vol_port", "kelly", "band", "corr", "dd_ma", "dd_vol"]
FULL = set(LAYERS)
KEY = {"mom": "mom_12m", "carry": "carry", "bmom": "bmom_12m", "skew": "skew", "hp_chg": "hp_chg"}

def load():
    p = pd.read_parquet(os.path.join(DATA, "daily_factors.parquet"))
    p["date"] = pd.to_datetime(p["date"])
    d = pd.read_csv(os.path.join(OUT, "factor_directions.csv")).set_index("factor")["direction"].to_dict()
    return p, d

def universe(p):
    """入选条件：商品期货（剔除金融期货与航运指数）、2015-12-31 前上市、样本内交易日 ≥2500（合约连续性），
    按 2023-2026 中位成交额降序。板块子池每板块前 8；全市场池前 30。"""
    liq = p.groupby("variety").agg(first=("date", "min"), days=("date", "nunique"),
                                   amt=("amt_total", "median")).reset_index()
    keep = liq[(liq["first"] <= "2015-12-31") & (liq["days"] >= 2500) & (~liq["variety"].isin(EXCLUDE))]
    keep = keep.sort_values("amt", ascending=False)
    top = keep["variety"].tolist()
    blocks = {b: [v for v in top if v in vs][:8] for b, vs in BLOCKS.items()}
    return liq, keep, top[:30], blocks

def wide(p, col):
    return p.pivot(index="date", columns="variety", values=col).sort_index()

def zs(df):
    """横截面标准化后按 ±3 截尾，抑制极端合约结构造成的离群值。"""
    z = df.sub(df.mean(1), axis=0).div(df.std(1).replace(0, np.nan), axis=0)
    return z.clip(-3, 3)

def build_signals(p, dirs):
    cols = {"carry": "carry", "skew": "skew", "hp_chg": "hp_chg"}
    W = {k: wide(p, v) for k, v in cols.items()}
    for f in ("mom", "bmom"):
        for k in ("6m", "9m", "12m", "18m"):
            W[f"{f}_{k}"] = wide(p, f"{f}_{k}")
    sigs = {}
    for f in ("mom", "carry", "bmom", "skew", "hp_chg"):
        for k in ("6m", "9m", "12m", "18m"):
            col = f"{f}_{k}" if f in ("mom", "bmom") else f
            sigs[(f, k)] = dirs.get(KEY[f], 1) * zs(W[col])
    for k in ("6m", "9m", "12m", "18m"):
        sigs[("triple", k)] = (sigs[("skew", k)] + sigs[("mom", k)] + sigs[("carry", k)]) / 3.0
    return sigs

def reb_dates(dates, freq):
    """返回重设日（实际交易日）。M=月末交易日，W=每周最后交易日，2W=隔周。"""
    s = pd.Series(pd.to_datetime(list(dates)))
    if freq == "M":
        return list(s.groupby(s.dt.to_period("M")).last().values)
    lastw = list(s.groupby(s.dt.to_period("W")).last().values)
    return lastw if freq == "W" else lastw[::2]

def alloc_idx(pos, S, scheme, tgt):
    """组内配权：等权 / GMV（收缩解析逆，长仓截断）/ 风险平价（等风险贡献定点迭代）。"""
    n = len(pos)
    if scheme == "equal" or S is None or n < 3:
        return np.full(n, tgt / n)
    A = S[np.ix_(pos, pos)]
    if not np.all(np.isfinite(A)):
        return np.full(n, tgt / n)
    A = 0.5 * A + 0.5 * np.diag(np.diag(A))
    if scheme == "gmv":
        try:
            v = np.linalg.pinv(A) @ np.ones(n)
            v = np.clip(v, 0.0, None)
            if v.sum() <= 1e-12:
                v = np.ones(n)
            return v / v.sum() * tgt
        except Exception:      # noqa: BLE001
            return np.full(n, tgt / n)
    w = np.full(n, 1.0 / n)
    target = tgt / n
    for _ in range(200):
        rc = np.maximum(w * (A @ w), 1e-12)
        w = np.clip(w * np.sqrt(target / rc), 1e-8, None)
        w = w / w.sum() * tgt
    return w

def metrics(r, freq=252):
    r = pd.Series(r).dropna()
    if len(r) < 20:
        return {}
    nav = (1 + r).cumprod()
    yrs = len(r) / freq
    cagr = nav.iloc[-1] ** (1 / yrs) - 1
    vol = r.std() * np.sqrt(freq)
    dn = r[r < 0].std() * np.sqrt(freq)
    mdd = (nav / nav.cummax() - 1).min()
    m = r.resample("ME").sum() if isinstance(r.index, pd.DatetimeIndex) else pd.Series(dtype=float)
    return dict(n=len(r), cagr=cagr, vol=vol, sharpe=cagr / vol if vol > 0 else np.nan,
                sortino=cagr / dn if dn > 0 else np.nan, mdd=mdd,
                calmar=cagr / abs(mdd) if mdd < 0 else np.nan, nav=nav.iloc[-1],
                hit=(r > 0).mean(), skew=r.skew(),
                tail5=m.nsmallest(max(1, int(len(m) * 0.05))).sum() if len(m) else np.nan)

def run_pool(Rt, sigw, pool, cfg):
    P = dict(DEF)
    P.update(cfg.get("params", {}))
    flags = set(cfg.get("flags", FULL))
    tgt = float(cfg.get("tgt", P["tgt"]))
    cost_bp = float(cfg.get("cost_bp", P["cost_bp"]))
    dates = list(Rt.index)
    T, N = len(dates), len(pool)
    Reb = Rt[pool].fillna(0.0)
    R = Reb.values
    volv = np.sqrt(Reb.pow(2).ewm(alpha=1 - P["theta"], adjust=False).mean() * 252).values
    SIG = sigw.reindex(index=Rt.index, columns=pool).values
    rbd = set(reb_dates(Rt.index, P["reb"]))
    per = pd.Series(pd.to_datetime(dates)).dt.to_period("M").values
    mstart = np.array([i for i in range(T) if i == 0 or per[i] != per[i - 1]])
    cw = int(P["corr_win"])
    w = np.zeros(N)
    w_unit_cur = np.zeros(N)
    w_eff = np.zeros(N)          # 当日实际生效权重（上一重设日设定，T+1 生效，避免前视）
    w_unit_eff = np.zeros(N)
    pnl = np.zeros(T)
    unit = np.zeros(T)
    nav_arr = np.zeros(T)
    nav = 1.0
    recs = []
    corrs = []
    whist = []          # 逐期持仓快照（仅 dump_weights=True 时收集）
    for i, d in enumerate(dates):
        cost = 0.0
        if d in rbd:
            info = {"date": d, "n_sig": 0, "n_long": 0, "n_short": 0, "gross": 0.0, "turnover": 0.0,
                    "cost": 0.0, "unit_gross": 0.0, "diag_var": np.nan, "offdiag_var": np.nan,
                    "kelly": np.nan, "avg_corr": np.nan, "rv20": np.nan}
            w_new = np.zeros(N)
            w_unit = np.zeros(N)
            lo = max(0, i - cw + 1)
            X = R[lo:i + 1]
            S = np.cov(X.T) * 252 if X.shape[0] > 5 else None
            s = SIG[i]
            ok = np.isfinite(s)
            if ok.sum() >= 6:
                npos = np.where(ok)[0]
                sv = s[npos]
                q = P["q"]
                if q < 1:
                    thr = np.quantile(np.abs(sv), 1 - q)
                    kk = np.abs(sv) >= thr
                    if kk.sum() >= 8:          # 样本不足时阈值分位不生效（板块子池）
                        npos, sv = npos[kk], sv[kk]
                if len(sv) >= 6:
                    order = np.argsort(sv)
                    rank = np.empty(len(order), int)
                    rank[order] = np.arange(1, len(order) + 1)
                    bounds = np.quantile(rank, [0.25, 0.5, 0.75])
                    grp = np.digitize(rank, bounds)
                    gw = list(AT_W if cfg.get("spread", "trad") == "at" else TRAD_W)
                    side = cfg.get("side", "both")
                    if side in ("long", "short"):       # 去掉对冲：只保留单边腿
                        tgt_g = float(cfg.get("side_gross", 0.5))
                        keep_sign = 1.0 if side == "long" else -1.0
                        gw = [x if np.sign(x) == keep_sign else 0.0 for x in gw]
                        tot = sum(abs(x) for x in gw) or 1.0
                        gw = [x * tgt_g / tot for x in gw]
                    for g in range(4):
                        m = np.where(grp == g)[0]
                        if len(m) == 0 or abs(gw[g]) <= 0:
                            continue
                        pos = npos[m]
                        w_new[pos] = np.sign(gw[g]) * alloc_idx(pos, S, cfg.get("weight", "equal"), abs(gw[g]))
                        info[f"n_g{g + 1}"] = len(pos)
                    info["n_sig"] = int(len(sv))
                    if "vol_name" in flags:
                        vv = volv[i, :]
                        sc = np.where(vv > 1e-8, P["vol_name"] / np.maximum(vv, 1e-8), 1.0)
                        w_new = w_new * np.clip(sc, 0.0, 5.0)
                        for m in (w_new > 0, w_new < 0):
                            tot = np.abs(w_new[m]).sum()
                            if tot > 0:
                                w_new[m] = w_new[m] * (0.5 / tot)
                    w_new = np.clip(w_new, -P["name_cap"], P["name_cap"])
                    w_unit = w_new.copy()
                    if "vol_port" in flags and S is not None:
                        sp = float(np.sqrt(max(w_unit @ S @ w_unit, 0.0)))
                        if sp > 1e-8:
                            w_new = w_new * (tgt / sp)
                    if "kelly" in flags:
                        ms = mstart[mstart <= i]
                        st = ms[-(int(P["kelly_win"]) + 1):]
                        segs = [unit[st[j]:st[j + 1]].sum() for j in range(len(st) - 1)]
                        segs.append(unit[st[-1]:i + 1].sum())
                        arr = np.array(segs)
                        if len(arr) >= 12:
                            mu = arr.mean() * 12
                            sg = float(arr.std(ddof=1) * np.sqrt(12))
                            fk = mu / sg ** 2 if sg > 1e-8 else 0.0
                            sc_k = float(np.clip(P["kelly_lam"] * fk, P["kelly_floor"], P["lev_cap"]))
                        else:
                            sc_k = 1.0
                        w_new = w_new * sc_k
                        info["kelly"] = sc_k
                    gr = float(np.abs(w_new).sum())
                    if gr > P["lev_cap"]:
                        w_new = w_new * (P["lev_cap"] / gr)
                    if "corr" in flags and N > 2 and X.shape[0] > 5:
                        C = np.corrcoef(X.T)
                        avg = float(C[np.triu_indices_from(C, 1)].mean())
                        info["avg_corr"] = avg
                        if len(corrs) > 12:
                            med = float(np.median(corrs))
                            if avg > 1.2 * med:
                                w_new = w_new * P["corr_delev"]
                                info["corr_delev"] = 1
                        corrs.append(avg)
                    if i > 5:
                        if "dd_ma" in flags and i >= int(P["nav_ma"]):
                            if nav_arr[i - 1] < nav_arr[i - int(P["nav_ma"]):i].mean():
                                w_new = w_new * P["dd_mult"]
                                info["dd_ma_on"] = 1
                        if "dd_vol" in flags:
                            rv = float(pnl[max(0, i - 20):i].std() * np.sqrt(252))
                            info["rv20"] = rv
                            if rv > P["vol_crash"] * tgt:
                                w_new = w_new * P["dd_mult"]
                                info["dd_vol_on"] = 1
                    if "band" in flags:
                        kk = np.abs(w_new - w) < P["band"]
                        w_new[kk] = w[kk]
                    if P.get("l1") is not None:      # ℓ1 换手正则化（软阈值，路径依赖）
                        dw = w_new - w
                        w_new = w + np.sign(dw) * np.maximum(np.abs(dw) - float(P["l1"]), 0.0)
                        gr2 = float(np.abs(w_new).sum())
                        if gr2 > P["lev_cap"]:
                            w_new = w_new * (P["lev_cap"] / gr2)
                    info["n_long"] = int((w_new > 0).sum())
                    info["n_short"] = int((w_new < 0).sum())
                    info["gross"] = float(np.abs(w_new).sum())
                    info["net"] = float(w_new.sum())
                    info["unit_gross"] = float(np.abs(w_unit).sum())
                    if S is not None:
                        info["diag_var"] = float(((w_new ** 2) * np.diag(S)).sum())
                        info["offdiag_var"] = float(w_new @ S @ w_new - info["diag_var"])
            turn = float(np.abs(w_new - w).sum())
            cost = turn * cost_bp / 1e4
            info["turnover"] = turn
            info["cost"] = cost
            w = w_new
            w_unit_cur = w_unit
            recs.append(info)
            if cfg.get("dump_weights"):
                whist.append((d, pd.Series(w_new.copy(), index=list(pool))))
        row = R[i]
        pnl[i] = float(w_eff @ row) - cost
        unit[i] = float(w_unit_eff @ row)
        w_eff = w
        w_unit_eff = w_unit_cur
        nav *= (1.0 + pnl[i])
        nav_arr[i] = nav
    return dict(pnl=pd.Series(pnl, index=dates), recs=pd.DataFrame(recs),
                nav=pd.Series(nav_arr, index=dates), unit=pd.Series(unit, index=dates),
                weights=whist)

def combine(block_runs):
    """四个板块各自多空后等权合并为“同板块组合”。"""
    if not block_runs:
        return None
    frames = pd.concat([v["pnl"].rename(k) for k, v in block_runs.items()], axis=1)
    return frames.mean(axis=1, skipna=True)