# -*- coding: utf-8 -*-
"""刷新到 2026-10-28 后重算：加权行业榜 + 加权主题概念榜 + 主题近周涨跌 + 相关诊断
输出：output/industry_weighted_1028.csv、output/theme_weighted_1028.csv、output/theme_weekret_1028.csv
"""
import os, json
import numpy as np, pandas as pd

RAW = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.abspath(os.path.join(RAW, "..", "output"))
XSEC = "20261028"; PREV = "20261023"

picks = json.load(open(os.path.join(RAW, "top_factor_picks.json"), encoding="utf-8"))
FACS = [(f["factor"], f["direction"]) for f in picks["factors"]]
W = {f: (len(FACS) + 1 - r[2]) if False else 0 for f in []}  # placeholder
# 权重按报告内名次（1..5）递减
W = {f["factor"]: len(FACS) + 1 - f["rank"] for f in picks["factors"]}
WT = sum(W.values())
print("因子与权重:", {f: round(W[f]/WT, 3) for f, d in FACS})

xs = pd.read_csv(os.path.join(RAW, "latest_cross_section.csv"))
sb = pd.read_csv(os.path.join(RAW, "stock_basic.csv"), dtype={"ts_code": str})[["ts_code", "name", "industry"]]
xs = xs.merge(sb, on="ts_code", how="left")
S = pd.DataFrame(index=xs.index)
for f, d in FACS:
    pr = xs[f].rank(pct=True); S[f] = pr if d > 0 else 1.0 - pr
xs["n_valid"] = S.notna().sum(axis=1)
xs["score_w"] = sum(S[f].fillna(0) * W[f] for f, _ in FACS) / WT
xs["score_eq"] = S.mean(axis=1, skipna=True)
full = xs[xs["n_valid"] == len(FACS)].copy()
print("全因子样本:", len(full), " 打分截面:", XSEC)

# ---------- 近周收益（20261023 收盘 -> 20261028 收盘）----------
dly = pd.read_csv(os.path.join(RAW, "daily_260d.csv"), dtype={"ts_code": str, "trade_date": str},
                  usecols=["ts_code", "trade_date", "close"])
p0 = dly[dly["trade_date"] == PREV].set_index("ts_code")["close"]
p1 = dly[dly["trade_date"] == XSEC].set_index("ts_code")["close"]
wret = (p1 / p0 - 1).rename("wret").to_frame()
# 期间各日涨跌（供诊断）
dd = dly[dly["trade_date"].isin(["20261026", "20261027", "20261028"])]
day = dd.pivot_table(index="trade_date", columns="ts_code", values="close")
print("近周交易日数:", list(day.index))

# ---------- 主题 ----------
mem = pd.read_csv(os.path.join(RAW, "theme_member_all.csv"), dtype={"board_code": str, "con_code": str})
mm = mem.merge(full[["ts_code", "score_w", "score_eq"]].merge(wret, left_on="ts_code", right_index=True, how="left"),
               left_on="con_code", right_on="ts_code")
th = mm.groupby(["board_code", "board_name"]).agg(
    n_in=("score_w", "size"), score_w=("score_w", "mean"), score_eq=("score_eq", "mean"),
    wret=("wret", "mean")).reset_index()
tot = mem.groupby("board_code").size().reset_index(name="n_total")
th = th.merge(tot, on="board_code")
th["coverage"] = (th["n_in"] / th["n_total"]).round(3)
th = th[th["n_in"] >= 5].copy()
th["rank_w"] = th["score_w"].rank(ascending=False, method="min").astype(int)
th["rank_eq"] = th["score_eq"].rank(ascending=False, method="min").astype(int)
th["rank_ret"] = th["wret"].rank(ascending=False, method="min").astype(int)
th = th.sort_values("score_w", ascending=False, kind="mergesort")
th.to_csv(os.path.join(OUT, "theme_weighted_1028.csv"), index=False, encoding="utf-8-sig")

hit = {}
mm2 = mem.merge(full[["ts_code"] + [f for f, _ in FACS]], left_on="con_code", right_on="ts_code")
for f, d in FACS:
    pr = mm2[f].rank(pct=True); s = pr if d > 0 else 1.0 - pr
    a = pd.DataFrame({"b": mm2["board_name"], "s": s}).dropna().groupby("b")["s"].agg(["mean", "size"])
    for b in a[a["size"] >= 5].sort_values("mean", ascending=False, kind="mergesort").head(20).index:
        hit[b] = hit.get(b, 0) + 1
th["hit"] = th["board_name"].map(hit).fillna(0).astype(int)

print("\n=== 加权主题榜 Top20（打分截面 2026-10-28）===")
print(th.head(20)[["rank_w", "board_name", "score_w", "hit", "wret", "rank_ret", "n_in"]].to_string(index=False))

rho = th["score_w"].corr(th["wret"], method="spearman")
print(f"\n[诊断] 主题加权分 vs 近周(10-23→10-28)收益 Spearman 相关: {rho:.3f}  (n={len(th)})")
print("[诊断] 主题加权分 Top20 的平均近周收益: %.4f%%" % (th.head(20)["wret"].mean() * 100))
print("[诊断] 全主题平均近周收益: %.4f%%" % (th["wret"].mean() * 100))

# ---------- 行业 ----------
ind = full.merge(wret, left_on="ts_code", right_index=True, how="left").groupby("industry").agg(
    n=("score_w", "size"), score_w=("score_w", "mean"), score_eq=("score_eq", "mean"), wret=("wret", "mean")).reset_index()
ind = ind[ind["n"] >= 10].sort_values("score_w", ascending=False, kind="mergesort")
ind["rank_w"] = ind["score_w"].rank(ascending=False, method="min").astype(int)
ind["rank_eq"] = ind["score_eq"].rank(ascending=False, method="min").astype(int)
ind.to_csv(os.path.join(OUT, "industry_weighted_1028.csv"), index=False, encoding="utf-8-sig")
print("\n=== 加权行业榜 Top12（打分截面 2026-10-28）===")
print(ind.head(12)[["rank_w", "industry", "score_w", "score_eq", "wret", "n"]].to_string(index=False))
print("\n[诊断] 行业加权分 vs 近周收益 Spearman: %.3f (n=%d)" % (ind["score_w"].corr(ind["wret"], method="spearman"), len(ind)))

# ---------- 新数据下的因子排名（对比）----------
pv = pd.read_csv(os.path.join(RAW, "factor_weekly_pivot.csv"))
pv = pv.sort_values("rank_this_week")
print("\n=== 刷新后 rank_this_week（周截面 20261023，前瞻仅 10-26~10-28）Top5 ===")
print(pv.head(5)[["factor", "rank_this_week", "rank_last_week"]].to_string(index=False))
print("=== 原报告 5 因子的新名次 ===")
print(pv[pv["factor"].isin([f for f, _ in FACS])][["factor", "rank_this_week", "rank_last_week"]].to_string(index=False))
