# -*- coding: utf-8 -*-
"""用「本周强势因子」的同一套因子口径，分别按【行业】与【概念主题】做因子择股/择主题。

输入：
    raw/top_factor_picks.json     入选因子与方向（同个股选股口径）
    raw/latest_cross_section.csv  最新打分截面（2026-10-23）
    raw/stock_basic.csv           Tushare 行业
    raw/theme_member_all.csv      概念板块成分（近 60 日热点 + 关注主题）
输出：
    output/industry_factor_rank.csv   全行业综合分排名
    output/theme_factor_rank.csv      概念主题综合分排名
    output/factor_by_industry.csv     5 因子 × 行业 Top5
    output/factor_by_theme.csv        5 因子 × 主题 Top5
"""
import os, json
import numpy as np
import pandas as pd

RAW = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.abspath(os.path.join(RAW, "..", "output"))
os.makedirs(OUT, exist_ok=True)

picks = json.load(open(os.path.join(RAW, "top_factor_picks.json"), encoding="utf-8"))
FACS = [(f["factor"], f["direction"]) for f in picks["factors"]]
XSEC = picks["xsec_date"]; WEEK = picks["week"]
print("入选因子:", FACS)

xs = pd.read_csv(os.path.join(RAW, "latest_cross_section.csv"))
sb = pd.read_csv(os.path.join(RAW, "stock_basic.csv"), dtype={"ts_code": str})[["ts_code", "name", "industry"]]
xs = xs.merge(sb, on="ts_code", how="left", suffixes=("", "_basic"))
print("截面:", xs.shape, "周截面 rank:", WEEK, "打分截面:", XSEC)

# 个股层因子分（方向调整后分位，越大越好）
S = pd.DataFrame(index=xs.index)
for fac, d in FACS:
    pr = xs[fac].rank(pct=True)
    S[fac] = pr if d > 0 else 1.0 - pr
xs["score"] = S.mean(axis=1, skipna=True)
xs["n_valid"] = S.notna().sum(axis=1)
xs["mv"] = xs["总市值"]
full = xs[xs["n_valid"] == len(FACS)].copy()   # 全部入选因子均有值
print("全因子样本:", len(full), "/", len(xs))

# ---------- 行业维度 ----------
ind = full.groupby("industry").agg(n=("score", "size"), score=("score", "mean"),
                                   mv=("mv", "sum")).reset_index()
ind["mv_yi"] = (ind["mv"] / 10000).round(1)
ind = ind[ind["n"] >= 10].sort_values("score", ascending=False, kind="mergesort")
ind.to_csv(os.path.join(OUT, "industry_factor_rank.csv"), index=False, encoding="utf-8-sig")
print("\n=== 行业综合分 Top15（样本≥10）===")
print(ind.head(15)[["industry", "score", "n", "mv_yi"]].to_string(index=False))

# ---------- 主题维度 ----------
mem = pd.read_csv(os.path.join(RAW, "theme_member_all.csv"), dtype={"board_code": str, "con_code": str})
mm = mem.merge(full[["ts_code", "score", "n_valid", "mv"]], left_on="con_code", right_on="ts_code", how="inner")
th = mm.groupby(["board_code", "board_name"]).agg(n_in=("score", "size"), score=("score", "mean")).reset_index()
tot = mem.groupby(["board_code", "board_name"]).size().reset_index(name="n_total")
th = th.merge(tot, on=["board_code", "board_name"])
th["coverage"] = (th["n_in"] / th["n_total"]).round(3)
th = th[th["n_in"] >= 5].sort_values("score", ascending=False, kind="mergesort")
th.to_csv(os.path.join(OUT, "theme_factor_rank.csv"), index=False, encoding="utf-8-sig")
print("\n=== 概念主题综合分 Top25（样本≥5）===")
print(th.head(25)[["board_name", "score", "n_in", "n_total", "coverage"]].to_string(index=False))

# ---------- 单因子 × 行业 / 主题 Top5 ----------
def best(grp, key):
    out = []
    for fac, d in FACS:
        pr = full[fac].rank(pct=True)
        full["_s"] = pr if d > 0 else 1.0 - pr
        a = full.dropna(subset=["_s"]).groupby(key).agg(v=("_s", "mean"), n=("_s", "size")).reset_index()
        a = a[a["n"] >= (10 if key == "industry" else 5)].sort_values("v", ascending=False, kind="mergesort")
        for _, r in a.head(5).iterrows():
            out.append({"factor": fac, "direction": d, key: r[key], "score": round(r["v"], 4), "n": int(r["n"])})
    return pd.DataFrame(out)

bi = best(full.copy(), "industry")
bi.to_csv(os.path.join(OUT, "factor_by_industry.csv"), index=False, encoding="utf-8-sig")
print("\n=== 单因子 → 行业 Top5 ===")
print(bi.to_string(index=False))

# 主题：把板块名并入
mm2 = mem.merge(full[["ts_code"] + [f for f, _ in FACS]], left_on="con_code", right_on="ts_code", how="inner")
rows = []
for fac, d in FACS:
    pr = mm2[fac].rank(pct=True)
    mm2["_s"] = pr if d > 0 else 1.0 - pr
    a = mm2.dropna(subset=["_s"]).groupby(["board_code", "board_name"]).agg(v=("_s", "mean"), n=("_s", "size")).reset_index()
    a = a[a["n"] >= 5].sort_values("v", ascending=False, kind="mergesort")
    for _, r in a.head(5).iterrows():
        rows.append({"factor": fac, "direction": d, "board": r["board_name"], "score": round(r["v"], 4), "n": int(r["n"])})
bt = pd.DataFrame(rows)
bt.to_csv(os.path.join(OUT, "factor_by_theme.csv"), index=False, encoding="utf-8-sig")
print("\n=== 单因子 → 主题 Top5 ===")
print(bt.to_string(index=False))

# 关注主题落点
watch = ["人形机器人", "机器人概念", "减速器", "机器视觉", "特斯拉概念", "一体化压铸", "无人驾驶", "电机", "汽车零部件", "存储芯片", "共封装光学(CPO)", "创新药", "CRO概念", "AI应用", "半导体", "芯片概念"]
w = th[th["board_name"].isin(watch)][["board_name", "score", "n_in", "n_total", "coverage"]]
w["rank_all"] = [int((th["score"] > s).sum()) + 1 for s in w["score"]]
print("\n=== 关注主题落点（共 %d 个主题参与排名）===" % len(th))
print(w.sort_values("score", ascending=False).to_string(index=False))
