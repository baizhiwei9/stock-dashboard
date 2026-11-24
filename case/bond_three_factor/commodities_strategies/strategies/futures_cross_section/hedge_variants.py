# -*- coding: utf-8 -*-
"""对冲 vs 去对冲（单边腿）对照 + 对冲有效性度量。

- both：原多空对冲（多头 0.5 / 空头 0.5，净敞口 0）
- long / short：去掉对冲，只保留单边腿（单边目标敞口 1.0，与原组合总敞口一致）
- 同时跑「全套风控」与「无风控」两档，用于拆分“对冲”与“风控”各自的作用
- 对冲有效性：对全市场等权多头组合（商品 beta 代理）回归，给出 beta 与相关性
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
from .common import DATA, OUT, log
from . import engine as E

def beta_corr(y, x):
    d = pd.concat([pd.Series(y).rename("y"), pd.Series(x).rename("x")], axis=1).dropna()
    if len(d) < 60:
        return np.nan, np.nan
    b = np.polyfit(d["x"], d["y"], 1)[0]
    return float(b), float(d["y"].corr(d["x"]))

def main():
    p, dirs = E.load()
    liq, keep, full, blocks = E.universe(p)
    Rt = E.wide(p, "ret_near")
    sigs = E.build_signals(p, dirs)
    basket = Rt[full].mean(axis=1)          # 商品 beta 代理：全市场等权多头
    pool_list = [("全市场", full)] + [(f"板块-{b}", v) for b, v in blocks.items()]
    rows = []
    for pname, pool in pool_list:
        for side in ["both", "long", "short"]:
            for flags, rname in [(set(E.FULL), "全套风控"), (set(), "无风控")]:
                cfg = dict(spread="trad", weight="equal", cost_bp=5.0, side=side,
                           side_gross=(0.5 if side == "both" else 1.0), flags=flags)
                r = E.run_pool(Rt, sigs[("triple", "12m")], pool, cfg)
                m = E.metrics(r["pnl"])
                rr = (1 + r["pnl"]).cumprod()
                # 组合层面（四板块合并）
                rows.append({"pool": pname, "side": side, "risk": rname,
                             "cagr": m.get("cagr"), "vol": m.get("vol"), "sharpe": m.get("sharpe"),
                             "mdd": m.get("mdd"), "calmar": m.get("calmar"),
                             "hit": m.get("hit"), "mean_gross": float(r["recs"]["gross"].mean()),
                             "ret": r["pnl"]})
    df = pd.DataFrame(rows)
    # 合并后的“同板块组合”= 四个板块等权平均
    comb = []
    for side in ["both", "long", "short"]:
        for flags, rname in [(set(E.FULL), "全套风控"), (set(), "无风控")]:
            cfg = dict(spread="trad", weight="equal", cost_bp=5.0, side=side,
                       side_gross=(0.5 if side == "both" else 1.0), flags=flags)
            fr = []
            for b, v in blocks.items():
                fr.append(E.run_pool(Rt, sigs[("triple", "12m")], v, cfg)["pnl"].rename(b))
            s = pd.concat(fr, axis=1).mean(axis=1)
            m = E.metrics(s)
            b_, c_ = beta_corr(s, basket)
            comb.append({"pool": "同板块(四板块合并)", "side": side, "risk": rname,
                         "cagr": m.get("cagr"), "vol": m.get("vol"), "sharpe": m.get("sharpe"),
                         "mdd": m.get("mdd"), "calmar": m.get("calmar"), "hit": m.get("hit"),
                         "mean_gross": np.nan, "beta_to_basket": b_, "corr_to_basket": c_,
                         "ret": s})
    df["beta_to_basket"] = np.nan
    df["corr_to_basket"] = np.nan
    for i, row in df.iterrows():
        b_, c_ = beta_corr(row["ret"], basket)
        df.loc[i, "beta_to_basket"] = b_
        df.loc[i, "corr_to_basket"] = c_
    for i, row in pd.DataFrame(comb).iterrows():
        pass
    allrows = pd.concat([df.drop(columns=["ret"]), pd.DataFrame(comb).drop(columns=["ret"])],
                        ignore_index=True)
    allrows.to_csv(os.path.join(OUT, "hedge_variants.csv"), index=False)
    pd.set_option("display.width", 250)
    show = allrows[["pool", "side", "risk", "cagr", "vol", "sharpe", "mdd", "calmar",
                    "mean_gross", "beta_to_basket", "corr_to_basket"]].copy()
    for c in show.columns:
        if show[c].dtype.kind in "fi":
            show[c] = show[c].astype(float).round(4)
    print(show.to_string(index=False))
    log("saved out/hedge_variants.csv")

if __name__ == "__main__":
    main()