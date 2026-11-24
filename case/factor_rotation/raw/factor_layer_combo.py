# -*- coding: utf-8 -*-
"""因子层组合搜索（纯因子口径）。

做法：
  1) 复用 factor_layer_diag 的口径，构造 **概念层因子得分矩阵**：
     concept × factor = 该概念成分股（在全 A 可交易池内、有该因子值）的
     direction 调整后池内分位（pct rank, 0~1）等权平均。
  2) 组合得分 = 选定因子子集的概念层得分等权平均（等价于"因子名次累加"）。
  3) 枚举 1~5 个因子的全部子集，找出 **概念层前 10 同时含 电机 与 AI视频** 的组合。
  4) 同时给出"原有 26 因子等权聚合"作为旧口径对照。

不做任何板块价格动量（40/60 日等权涨幅）打分。

输出：
  output/factor_layer_matrix.csv   概念 × 因子 得分矩阵（133 × 26）
  output/factor_layer_combo.csv    满足约束的组合清单
"""
import os
import itertools
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW, OUT = os.path.join(ROOT, "raw"), os.path.join(ROOT, "output")

FACTORS = [
    ("BP", +1), ("单季EP", +1), ("单季SP", +1), ("EPTTM", +1), ("SPTTM", +1),
    ("EPTTM一年分位点", +1), ("股息率", +1),
    ("一个月反转", -1), ("三个月反转", -1), ("一年动量", +1),
    ("单季净利同比增速", +1), ("单季营收同比增速", +1),
    ("单季ROE", +1), ("DELTAROE", +1),
    ("非流动性冲击", -1), ("一个月换手", -1), ("三个月换手", -1),
    ("一个月波动", -1), ("三个月波动", -1), ("特异度", -1),
    ("预期EPTTM", +1), ("预期PEG", +1), ("预期净利润环比", +1),
    ("三个月机构覆盖", +1), ("公募重仓", +1), ("机构持仓", +1),
]
FNAMES = [f for f, _ in FACTORS]
TARGETS = ["电机", "AI视频"]

# ---------- 池 ----------
daily = pd.read_csv(os.path.join(RAW, "daily_260d.csv"),
                    usecols=["ts_code", "trade_date", "amount"], dtype={"ts_code": str, "trade_date": str})
d29 = pd.read_csv(os.path.join(RAW, "daily_20261029.csv"),
                  usecols=["ts_code", "trade_date", "amount"], dtype={"ts_code": str, "trade_date": str})
daily = pd.concat([daily, d29], ignore_index=True).drop_duplicates(["ts_code", "trade_date"])
daily["amount"] = pd.to_numeric(daily["amount"], errors="coerce")
dates = sorted(daily["trade_date"].unique())
amt = daily.pivot_table(index="trade_date", columns="ts_code", values="amount", aggfunc="last").sort_index(axis=1)
sb = pd.read_csv(os.path.join(RAW, "stock_basic.csv"), dtype=str)
NAME = sb.set_index("ts_code")["name"].to_dict()
ST = pd.Series({t: ("ST" in (NAME.get(t, "") or "")) or ("退" in (NAME.get(t, "") or "")) for t in amt.columns})
UPTO = len(dates) - 1
obs = amt.notna().sum()
tr = (obs >= 120) & amt.iloc[UPTO].notna() & (amt.iloc[UPTO] > 0) & ~ST.reindex(amt.columns).fillna(False)
pool = sorted(tr[tr].index)
print(f"[pool] {len(pool)}")

xs = pd.read_csv(os.path.join(RAW, "latest_cross_section_fullA.csv"), dtype={"ts_code": str}).set_index("ts_code")[FNAMES]

tm = pd.concat([pd.read_csv(os.path.join(RAW, "theme_member_all.csv"), dtype=str),
                pd.read_csv(os.path.join(RAW, "theme_member_ai.csv"), dtype=str)],
               ignore_index=True).drop_duplicates(["board_code", "con_code"])
con_members = {bc: sorted(set(g["con_code"])) for bc, g in tm.groupby("board_code")}
con_names = tm.drop_duplicates("board_code").set_index("board_code")["board_name"].to_dict()
con_members = {bc: m for bc, m in con_members.items() if len([c for c in m if c in amt.columns]) >= 5}
BCS = list(con_members)
print(f"[concept] {len(BCS)}")

# ---------- 概念 × 因子 得分矩阵 ----------
M = pd.DataFrame(index=BCS, columns=FNAMES, dtype=float)
N = pd.DataFrame(index=BCS, columns=FNAMES, dtype=int)
for f, d in FACTORS:
    v = pd.to_numeric(xs[f], errors="coerce") * d
    pct = v.reindex(pool).rank(pct=True, ascending=True, method="average")
    for bc in BCS:
        s = pct.reindex(con_members[bc]).dropna()
        M.loc[bc, f] = s.mean() if len(s) else np.nan
        N.loc[bc, f] = len(s)
M.to_csv(os.path.join(OUT, "factor_layer_matrix.csv"))
N.to_csv(os.path.join(OUT, "factor_layer_matrix_n.csv"))
print("[matrix] 写入 output/factor_layer_matrix.csv / _n.csv")

arr = M.values  # 133 x 26
cols = list(M.columns)
tgt = [BCS.index([bc for bc in BCS if con_names[bc] == t][0]) for t in TARGETS]
TNAME = {i: t for i, t in zip(tgt, TARGETS)}


def rank_of(vec):
    s = pd.Series(vec, index=BCS)
    return s.rank(ascending=False, method="min")


# ---------- 旧口径对照：26 因子等权 ----------
allv = np.nanmean(arr, axis=1)
_allrk = rank_of(allv)
for i, t in TNAME.items():
    print(f"[旧口径 26因子等权] {t}: {int(_allrk.iloc[i])}/{len(BCS)}")

# ---------- 枚举子集 ----------
recs = []
for k in (1, 2, 3, 4, 5):
    for comb in itertools.combinations(range(len(cols)), k):
        vec = np.nanmean(arr[:, comb], axis=1)
        rk = rank_of(vec)
        r33 = int(rk.iloc[tgt[0]]); r34 = int(rk.iloc[tgt[1]])
        if r33 <= 10 and r34 <= 10:
            recs.append({"size": k, "factors": "+".join(cols[j] for j in comb),
                         "rk_电机": r33, "rk_AI视频": r34,
                         "top10": "、".join(rk.sort_values(kind="mergesort").head(10).index.values)})
    print(f"[scan] size={k} done, 累计满足约束 {len(recs)}")

rc = pd.DataFrame(recs)
rc = rc.sort_values(["size", "rk_电机"] if len(rc) else [], kind="mergesort")
rc.to_csv(os.path.join(OUT, "factor_layer_combo.csv"), index=False)
print(f"\n满足约束的组合数：{len(rc)}")
if len(rc):
    print(rc["size"].value_counts().sort_index().to_string())
    print("\n最小规模（size=%d）的组合：" % rc["size"].min())
    print(rc[rc["size"] == rc["size"].min()].to_string(index=False))
print("\n[done] -> output/factor_layer_combo.csv")