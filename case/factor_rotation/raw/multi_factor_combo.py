# -*- coding: utf-8 -*-
"""按因子名次累加的多因子综合分（用户口径）。

口径：
  1) 以 raw/factor_weekly_pivot.csv 的 rank_this_week 为因子排序（第 1 名 = 本周最强）；
  2) 取前 n 名因子参与合成（n = 1..10），第 i 名权重 = n+1-i（线性递减），归一化到 Σw=1；
  3) 单因子先做方向调整分位：direction>0 取分位，direction<0 取 1-分位（direction 取自 factor_weekly_raw.csv）；
  4) 综合分 = Σ 权重 × 方向调整分位；要求入选因子全部有取值（dropna）。
输出：output/combo_weights.csv、output/combo_scores.csv
"""
import os
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW, OUT = os.path.join(ROOT, "raw"), os.path.join(ROOT, "output")

piv = pd.read_csv(os.path.join(RAW, "factor_weekly_pivot.csv"))
raw = pd.read_csv(os.path.join(RAW, "factor_weekly_raw.csv"))
DIR = raw.drop_duplicates("factor").set_index("factor")["direction"].to_dict()

ordf = piv[["factor", "rank_this_week"]].dropna().sort_values("rank_this_week", kind="mergesort")
order = ordf["factor"].tolist()
print("[因子本周排名]")
for i, f in enumerate(order, 1):
    print(f"  {i:>2} {f}  (direction={DIR.get(f)})")

xs = pd.read_csv(os.path.join(RAW, "latest_cross_section.csv"), dtype={"ts_code": str})
NS = list(range(1, 11))
scores = xs[["ts_code", "总市值"]].copy()
wrows = []
for n in NS:
    facs = order[:n]
    w = {f: float(n + 1 - i) for i, f in enumerate(facs)}
    tot = sum(w.values())
    for i, f in enumerate(facs, 1):
        wrows.append({"n": n, "rank": i, "factor": f, "direction": DIR.get(f),
                      "weight": w[f] / tot})
    d = xs.dropna(subset=facs).copy()
    d["score"] = 0.0
    for f in facs:
        pr = d[f].rank(pct=True, method="average")
        d["score"] += (w[f] / tot) * (pr if DIR.get(f, 1) > 0 else (1.0 - pr))
    scores = scores.merge(d[["ts_code", "score"]].rename(columns={"score": f"score_n{n}"}),
                          on="ts_code", how="left")
    print(f"[n={n:>2}] 因子 {' + '.join(facs)}  → 全有值股票 {len(d)} / {len(xs)}")

wdf = pd.DataFrame(wrows)
wdf.to_csv(os.path.join(OUT, "combo_weights.csv"), index=False)
scores.to_csv(os.path.join(OUT, "combo_scores.csv"), index=False)
print(f"[saved] combo_weights.csv {wdf.shape}  combo_scores.csv {scores.shape}")
print("[权重表 n=7]")
print(wdf[wdf["n"] == 7].to_string(index=False))
print("[权重表 n=10]")
print(wdf[wdf["n"] == 10].to_string(index=False))