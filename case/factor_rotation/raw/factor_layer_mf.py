# -*- coding: utf-8 -*-
"""在纯因子口径的「概念 × 因子」矩阵中追加**资金流因子**（新数据，非价格动量）。

定义（个股层）：
  资金流5/20 = Σ(近5/20个交易日 net_mf_amount×10) / Σ(近5/20个交易日 amount)
  单位对齐：net_mf_amount 为万元 → ×10 得千元；daily.amount 为千元。
  即"近 N 日净流入额占成交额比例"，越大代表资金净流入强度越高，方向 +1。

概念层：成分股（全 A 可交易池内）该因子池内分位的等权平均，与 26 个旧因子同口径。

输出：output/factor_layer_matrix_ext.csv（133 × 28）
"""
import os
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW, OUT = os.path.join(ROOT, "raw"), os.path.join(ROOT, "output")

daily = pd.read_csv(os.path.join(RAW, "daily_260d.csv"),
                    usecols=["ts_code", "trade_date", "amount"], dtype={"ts_code": str, "trade_date": str})
d29 = pd.read_csv(os.path.join(RAW, "daily_20261029.csv"),
                  usecols=["ts_code", "trade_date", "amount"], dtype={"ts_code": str, "trade_date": str})
daily = pd.concat([daily, d29], ignore_index=True).drop_duplicates(["ts_code", "trade_date"])
daily["amount"] = pd.to_numeric(daily["amount"], errors="coerce")
dates = sorted(daily["trade_date"].unique())
amt = daily.pivot_table(index="trade_date", columns="ts_code", values="amount", aggfunc="last").sort_index()

sb = pd.read_csv(os.path.join(RAW, "stock_basic.csv"), dtype=str)
NAME = sb.set_index("ts_code")["name"].to_dict()
ST = pd.Series({t: ("ST" in (NAME.get(t, "") or "")) or ("退" in (NAME.get(t, "") or "")) for t in amt.columns})
UPTO = len(dates) - 1
obs = amt.notna().sum()
tr = (obs >= 120) & amt.iloc[UPTO].notna() & (amt.iloc[UPTO] > 0) & ~ST.reindex(amt.columns).fillna(False)
pool = sorted(tr[tr].index)
print(f"[pool] {len(pool)}  截面日 {dates[UPTO]}")

mf = pd.read_csv(os.path.join(RAW, "moneyflow_daily.csv"),
                 usecols=["trade_date", "ts_code", "net_mf_amount"],
                 dtype={"trade_date": str, "ts_code": str})
mf["net_mf_amount"] = pd.to_numeric(mf["net_mf_amount"], errors="coerce")
print(f"[mf] rows={len(mf)}  dates={mf['trade_date'].nunique()}  range={mf['trade_date'].min()}..{mf['trade_date'].max()}")

piv = mf.pivot_table(index="trade_date", columns="ts_code", values="net_mf_amount", aggfunc="last").sort_index()

new = {}
for w in (5, 20):
    msum = piv.iloc[-w:].sum(min_count=1)            # 万元
    asum = amt.iloc[-w:].sum(min_count=1)            # 千元
    ratio = (msum * 10.0) / asum.replace(0, np.nan)  # 净流入强度
    v = ratio.reindex(pool)
    pct = v.rank(pct=True, ascending=True, method="average")
    new[f"资金流{w}"] = pct
    print(f"[资金流{w}] 非空 {int(pct.notna().sum())}/{len(pool)}  中位 {pct.median():.3f}")

# 概念层聚合
tm = pd.concat([pd.read_csv(os.path.join(RAW, "theme_member_all.csv"), dtype=str),
                pd.read_csv(os.path.join(RAW, "theme_member_ai.csv"), dtype=str)],
               ignore_index=True).drop_duplicates(["board_code", "con_code"])
con_members = {bc: sorted(set(g["con_code"])) for bc, g in tm.groupby("board_code")}
con_names = tm.drop_duplicates("board_code").set_index("board_code")["board_name"].to_dict()
con_members = {bc: m for bc, m in con_members.items() if len([c for c in m if c in amt.columns]) >= 5}

M = pd.read_csv(os.path.join(OUT, "factor_layer_matrix.csv"), index_col=0)
for cname, pct in new.items():
    scores = {}
    for bc, mem in con_members.items():
        s = pct.reindex(mem).dropna()
        scores[bc] = s.mean() if len(s) else np.nan
    M[cname] = pd.Series(scores).reindex(M.index)

M.to_csv(os.path.join(OUT, "factor_layer_matrix_ext.csv"))
print(f"[matrix_ext] {M.shape}")

rk = M.rank(ascending=False, method="min")
for t in ["电机", "AI视频"]:
    bc = [c for c in M.index if con_names.get(c) == t][0]
    print(f"[{t}] {bc}  资金流5 #{int(rk.loc[bc,'资金流5'])}  资金流20 #{int(rk.loc[bc,'资金流20'])}")
print("\n[资金流20 概念层 Top10]")
for bc, v in M["资金流20"].sort_values(ascending=False, kind="mergesort").head(10).items():
    print(f"  {con_names.get(bc,bc):<14s} {v:.4f}")
print("\n[done] -> output/factor_layer_matrix_ext.csv")