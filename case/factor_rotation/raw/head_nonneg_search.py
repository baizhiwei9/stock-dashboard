# -*- coding: utf-8 -*-
"""在「头部因子」范围内搜索非负权重组合，看能否在概念层同时选中 电机 与 AI视频。

- 概念层矩阵 M (133 x F)：成分股（池内）因子分位（direction 已调整）等权平均。
- 非负权重组合：随机 Dirichlet 采样 + 网格，要求 电机 与 AI视频 同时进入概念层前 10。
- 输出满足组合及 margin = min(两目标得分 - 第10名得分)。
"""
import os
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW, OUT = os.path.join(ROOT, "raw"), os.path.join(ROOT, "output")
rng = np.random.default_rng(20261029)

P = pd.read_csv(os.path.join(RAW, "factor_layer_stock_pct.csv"),
                dtype={"Unnamed: 0": str}).rename(columns={"Unnamed: 0": "ts_code"}).set_index("ts_code")
pool = list(P.index)

# 补 预期PEG三年复合
xsA = pd.read_csv(os.path.join(RAW, "latest_cross_section_fullA.csv"), dtype={"ts_code": str}).set_index("ts_code")
fc = pd.read_csv(os.path.join(RAW, "profit_forecast.csv"))
fc["code6"] = fc["代码"].astype(str).str.split(".").str[0].str.zfill(6)
fc = fc.drop_duplicates(subset=["code6"], keep="first").set_index("code6")
e25 = pd.to_numeric(fc["2025预测每股收益"], errors="coerce")
e26 = pd.to_numeric(fc["2026预测每股收益"], errors="coerce")
e28 = pd.to_numeric(fc["2028预测每股收益"], errors="coerce")
close6 = pd.Series(pd.to_numeric(xsA["close"], errors="coerce").values,
                   index=xsA.index.str.split(".").str[0].str.zfill(6))
close6 = close6[~close6.index.duplicated()]
pe_f = (close6 / e26.reindex(close6.index)).replace([np.inf, -np.inf], np.nan)
pos = (e25 > 0) & (e28 > 0)
g3 = pd.Series(np.nan, index=fc.index)
g3[pos] = (e28[pos] / e25[pos]) ** (1.0 / 3.0) - 1.0
g3 = g3.replace([np.inf, -np.inf], np.nan)
peg3 = (pe_f / (g3.reindex(close6.index) * 100)).replace([np.inf, -np.inf], np.nan)
pool6 = pd.Series([c.split(".")[0].zfill(6) for c in pool], index=pool)
peg3_stock = pd.Series(peg3.reindex(pool6.values).values, index=pool)
P["预期PEG三年复合"] = peg3_stock.rank(pct=True, ascending=True, method="average")

# ---------- 概念层 ----------
td_cols = set(pd.read_csv(os.path.join(RAW, "daily_260d.csv"), usecols=["ts_code"], dtype=str)["ts_code"].unique())
td_cols |= set(pd.read_csv(os.path.join(RAW, "daily_20261029.csv"), usecols=["ts_code"], dtype=str)["ts_code"].unique())
tm = pd.concat([pd.read_csv(os.path.join(RAW, "theme_member_all.csv"), dtype=str),
                pd.read_csv(os.path.join(RAW, "theme_member_ai.csv"), dtype=str)],
               ignore_index=True).drop_duplicates(["board_code", "con_code"])
con_members = {bc: sorted(set(g["con_code"])) for bc, g in tm.groupby("board_code")}
con_name = tm.drop_duplicates("board_code").set_index("board_code")["board_name"].to_dict()
con_members = {bc: m for bc, m in con_members.items() if len([c for c in m if c in td_cols]) >= 5}
BCS = sorted(con_members)
NAMES = np.array([con_name.get(bc, bc) for bc in BCS])
TIDX = {n: i for i, n in enumerate(NAMES)}
print(f"[concepts] {len(BCS)} 个；电机 #{TIDX.get('电机')}、AI视频 #{TIDX.get('AI视频')}")

# 候选因子（剔除反转类：三个月反转 / 一个月反转；不含 3/5/10 日短期动量）
CANDS = ["EPTTM一年分位点", "预期PEG", "预期PEG三年复合", "公募重仓",
         "一年动量", "资金流5", "资金流20"]
CANDS = [f for f in CANDS if f in P.columns]
F = len(CANDS)
M = np.full((len(BCS), F), np.nan)
for j, f in enumerate(CANDS):
    v = P[f]
    for i, bc in enumerate(BCS):
        cc = [c for c in con_members[bc] if c in v.index]
        if len(cc) >= 5:
            M[i, j] = v.reindex(cc).mean()
# NaN 以列均值补（不做 min-max，保持与因子层口径一致的 pct rank 聚合）
for j in range(F):
    col = M[:, j]
    if np.isnan(col).any():
        M[np.isnan(col), j] = np.nanmean(col)

TI = TIDX["电机"]
AI = TIDX["AI视频"]


def margin_of(w):
    s = M @ w
    thr = np.partition(s, -10)[-10]
    return min(s[TI] - thr, s[AI] - thr), s[TI], s[AI]


# 网格 + 随机
best = None
best_k = {}
stats = {"k": {}, "sat": 0}
for k in range(1, F + 1):
    from itertools import combinations
    hit = 0
    for sub in combinations(range(F), k):
        Ms = M[:, sub]
        N = 4000
        if k == 1:
            Ws = np.ones((1, 1))
        else:
            Ws = rng.dirichlet(np.ones(k) * 0.6, size=N)
            Ws = np.vstack([Ws, np.ones((1, k)) / k])   # 含等权
        S = Ms @ Ws.T                       # (133, N)
        thr = np.partition(S, -10, axis=0)[-10, :]
        ok = (S[TI] > thr) & (S[AI] > thr)
        hit += int(ok.sum())
        stats["sat"] += int(ok.sum())
        idx = np.where(ok)[0]
        for t in idx:
            w = np.zeros(F)
            w[list(sub)] = Ws[t]
            mm, s_ti, s_ai = margin_of(w)
            if best is None or mm > best[0]:
                best = (mm, w.copy(), s_ti, s_ai)
            bk = best_k.get(k)
            if bk is None or mm > bk[0]:
                best_k[k] = (mm, w.copy(), s_ti, s_ai, sub)
    stats["k"][k] = hit
    bk = best_k.get(k)
    if bk is not None:
        lab = " + ".join(f"{CANDS[i]} {bk[1][i]:.3f}" for i in bk[4])
        print(f"  k={k} 满足样本 {hit}；本 k 最优 margin={bk[0]:.4f} 电机 {bk[2]:.4f}/AI视频 {bk[3]:.4f}  ← {lab}")
    else:
        print(f"  k={k} 满足样本 {hit}")

print(f"\n[search] 总满足样本 {stats['sat']}")
if best is None:
    print("[result] 剔除反转类 + 非负权重：无满足组合（电机 & AI视频 无法同时进概念前10）")
else:
    mm, w, sti, sai = best
    lab = " + ".join(f"{CANDS[i]} {w[i]:.3f}" for i in range(F) if w[i] > 1e-6)
    print(f"[result] 最优非负组合：{lab}")
    print(f"         margin={mm:.4f}（电机 {sti:.4f} / AI视频 {sai:.4f}）")
    s = M @ w
    order = np.argsort(-s, kind="stable")
    print("         概念层前10：")
    for r, i in enumerate(order[:10], 1):
        print(f"           #{r} {NAMES[i]:<20s} {s[i]:.4f}")