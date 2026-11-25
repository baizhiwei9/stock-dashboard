# -*- coding: utf-8 -*-
"""五个因子的构造（日频网格）与样本内方向确定。

因子口径（报告需逐条披露）：
1. 动量 mom_L    = ln(近月连续指数_t / 指数_{t−L 交易日})，L = 6/9/12/18 个月（126/189/252/378）
2. 基差 carry    = ln(settle1 / settle_far) / (mat_far − mat1)，far = 近四月中最远可用合约（年化对数价差）
3. 基差动量 bmom_L = 过去 L 交易日 bslope_gap 的等权累积；bslope_gap = b_near − b_far，
   b 为 ln(价格) 对到期时间(年)的斜率：b_near 由近月+次近月两点确定，b_far 由第三+第四近月两点确定
4. 偏度 skew     = 近月连续日收益过去 60 个交易日的已实现偏度（替代口径，分钟数据不可得，见报告）
   上下行拆解 ds_ratio = 下行半方差 / 上行半方差
5. 套保压力 hp    = 近月合约持仓量占品种总持仓比例 hp_front 的 63 个交易日变化
   不使用前 20 会员净持仓等净持仓类信号（按用户要求剔除）

样本内 2010-01~2016-12 用 IC 定方向，样本外 2017-01~2026-11 沿用，不再优化。
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
from .common import DATA, OUT, log

IS_END = "2016-12-31"
WIN = {"6m": 126, "9m": 189, "12m": 252, "18m": 378}

def last_nonnull(arrs):
    a = np.vstack([np.asarray(x, dtype=float) for x in arrs]).T
    out = np.full(len(a), np.nan)
    for i, r in enumerate(a):
        nz = np.where(~np.isnan(r))[0]
        if len(nz):
            out[i] = r[nz[-1]]
    return out

def build(p):
    p = p.sort_values(["variety", "date"]).copy()
    p["idx_near"] = p.groupby("variety")["ret_near"].transform(lambda s: (1 + s.fillna(0)).cumprod())
    g = p.groupby("variety")["idx_near"]
    for k, L in WIN.items():
        p[f"mom_{k}"] = np.log(p["idx_near"] / g.shift(L))

    p["far_px"] = last_nonnull([p["settle2"], p["settle3"], p["settle4"]])
    p["far_mat"] = last_nonnull([p["mat2"], p["mat3"], p["mat4"]])
    p["carry"] = np.log(p["settle1"] / p["far_px"]) / (p["far_mat"] - p["mat1"]).replace(0, np.nan)

    b_near = (np.log(p["settle2"]) - np.log(p["settle1"])) / (p["mat2"] - p["mat1"]).replace(0, np.nan)
    b_far = (np.log(p["settle4"]) - np.log(p["settle3"])) / (p["mat4"] - p["mat3"]).replace(0, np.nan)
    p["bslope_gap"] = b_near - b_far
    p = p.sort_values(["variety", "date"])
    gb = p.groupby("variety")["bslope_gap"]
    for k, L in WIN.items():
        p[f"bmom_{k}"] = gb.transform(lambda s, L=L: s.rolling(L, min_periods=int(L * 0.7)).sum())

    r = p.groupby("variety")["ret_near"]
    p["skew"] = r.transform(lambda s: s.rolling(60, min_periods=40).skew())
    p["up_semivar"] = r.transform(lambda s: s.clip(lower=0).pow(2).rolling(60, min_periods=40).mean())
    p["dn_semivar"] = r.transform(lambda s: (-s.clip(upper=0)).pow(2).rolling(60, min_periods=40).mean())
    p["ds_ratio"] = p["dn_semivar"] / p["up_semivar"].replace(0, np.nan)

    p["hp_chg"] = p["hp_front"] - p.groupby("variety")["hp_front"].shift(63)

    p["ym"] = p["date"].dt.to_period("M")
    mon = p.groupby(["variety", "ym"]).tail(1).copy()
    mon["fwd_ret"] = mon.groupby("variety")["idx_near"].transform(lambda s: s.shift(-1) / s - 1.0)
    return p, mon

FACTORS = {"mom_12m": "动量(12M)", "carry": "基差(carry)", "bmom_12m": "基差动量",
           "skew": "偏度", "hp_chg": "套保压力"}

def directions(mon):
    isd = mon[mon["date"] <= IS_END]
    rows = []
    for f, nm in FACTORS.items():
        ics = []
        for _, g in isd.groupby("ym"):
            g = g[[f, "fwd_ret"]].dropna()
            if len(g) >= 8:
                ics.append(g[f].rank().corr(g["fwd_ret"].rank()))
        s = pd.Series(ics, dtype=float)
        t = s.mean() / (s.std(ddof=1) / np.sqrt(len(s))) if len(s) > 2 else np.nan
        rows.append({"factor": f, "name": nm, "n_months": len(s), "ic_mean": s.mean(),
                     "ic_t": t, "direction": int(np.sign(s.mean())) if abs(s.mean()) > 1e-9 else 1})
    d = pd.DataFrame(rows)
    return d

def main():
    p = pd.read_parquet(os.path.join(DATA, "panel.parquet"))
    p["date"] = pd.to_datetime(p["date"])
    p, mon = build(p)
    cols = ["variety", "date", "ym", "idx_near", "ret_near", "ret_main", "near_code", "main_code",
            "settle1", "mat1", "oi_total", "amt_total", "hp_front", "bslope_gap",
            "carry", "skew", "ds_ratio", "hp_chg"] + [c for c in p.columns if c.startswith(("mom_", "bmom_"))]
    p[cols].to_parquet(os.path.join(DATA, "daily_factors.parquet"), index=False)
    mon[["variety", "date", "ym", "fwd_ret", "idx_near", "ret_near"] + list(FACTORS)].to_parquet(
        os.path.join(DATA, "monthly_factors.parquet"), index=False)
    d = directions(mon)
    d.to_csv(os.path.join(OUT, "factor_directions.csv"), index=False)
    log("daily rows", len(p), "monthly rows", len(mon))
    print(d.to_string(index=False))
    desc = mon[list(FACTORS) + ["ds_ratio"]].describe().T
    desc["missing"] = mon[list(FACTORS) + ["ds_ratio"]].isna().mean()
    desc.to_csv(os.path.join(OUT, "factor_desc.csv"))
    print(desc.to_string())

if __name__ == "__main__":
    main()