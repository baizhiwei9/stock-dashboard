# -*- coding: utf-8 -*-
"""按因子名次加权的主题/行业选择：
  权重：名次 1→5、2→4、3→3、4→2、5→1（归一化），名次最高的因子权重最大。
  个股加权分 = Σ(w_f·S_f)/Σ(w_f)  （S_f 为方向调整后横截面分位）
  主题分 = 成分股（5 因子全有值）加权分均值；同时给出等权口径与「命中因子数」诊断。
输出：output/theme_weighted_rank.csv、output/industry_weighted_rank.csv
"""
import os, json
import pandas as pd

RAW = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.abspath(os.path.join(RAW, "..", "output"))

picks = json.load(open(os.path.join(RAW, "top_factor_picks.json"), encoding="utf-8"))
FACS = [(f["factor"], f["direction"], f["rank"]) for f in picks["factors"]]
W = {f: (len(FACS) + 1 - r) for f, d, r in FACS}          # 5,4,3,2,1
WT = sum(W.values())
print("因子权重:", {f: W[f] for f, d, r in FACS}, "归一化:", {f: round(W[f]/WT, 3) for f, d, r in FACS})

xs = pd.read_csv(os.path.join(RAW, "latest_cross_section.csv"))
sb = pd.read_csv(os.path.join(RAW, "stock_basic.csv"), dtype={"ts_code": str})[["ts_code", "name", "industry"]]
xs = xs.merge(sb, on="ts_code", how="left")
S = pd.DataFrame(index=xs.index)
for fac, d, r in FACS:
    pr = xs[fac].rank(pct=True)
    S[fac] = pr if d > 0 else 1.0 - pr
xs["n_valid"] = S.notna().sum(axis=1)
xs["score_w"] = sum(S[f] * W[f] for f, _, _ in FACS) / sum(W[f] for f, _, _ in FACS if True) if False else \
              sum(S[f].fillna(0) * W[f] for f, _, _ in FACS) / sum(W[f] for f, _, _ in FACS)
xs["score_eq"] = S.mean(axis=1, skipna=True)
full = xs[xs["n_valid"] == len(FACS)].copy()
print("全因子样本:", len(full))

mem = pd.read_csv(os.path.join(RAW, "theme_member_all.csv"), dtype={"board_code": str, "con_code": str})
mm = mem.merge(full[["ts_code", "score_w", "score_eq"]], left_on="con_code", right_on="ts_code")
th = mm.groupby(["board_code", "board_name"]).agg(n_in=("score_w", "size"),
        score_w=("score_w", "mean"), score_eq=("score_eq", "mean")).reset_index()
tot = mem.groupby(["board_code", "board_name"]).size().reset_index(name="n_total")
th = th.merge(tot, on=["board_code", "board_name"])
th["coverage"] = (th["n_in"] / th["n_total"]).round(3)
th = th[th["n_in"] >= 5].copy()
th["rank_w"] = th["score_w"].rank(ascending=False, method="min").astype(int)
th["rank_eq"] = th["score_eq"].rank(ascending=False, method="min").astype(int)
th["chg"] = th["rank_eq"] - th["rank_w"]     # 正=加权后名次上升

# 命中因子数：该主题进入几个单因子的 Top20
mm2 = mem.merge(full[["ts_code"] + [f for f, _, _ in FACS]], left_on="con_code", right_on="ts_code")
hits = {}
for fac, d, r in FACS:
    pr = mm2[fac].rank(pct=True)
    s = pr if d > 0 else 1.0 - pr
    a = pd.DataFrame({"board": mm2["board_name"], "s": s}).dropna().groupby("board")["s"].agg(["mean", "size"])
    top = a[a["size"] >= 5].sort_values("mean", ascending=False, kind="mergesort").head(20).index
    for b in top:
        hits[b] = hits.get(b, 0) + 1
th["hit"] = th["board_name"].map(hits).fillna(0).astype(int)
th = th.sort_values("score_w", ascending=False, kind="mergesort")
th.to_csv(os.path.join(OUT, "theme_weighted_rank.csv"), index=False, encoding="utf-8-sig")

print("\n=== 加权主题榜 Top20（权重 5/4/3/2/1）===")
print(th.head(20)[["rank_w", "board_name", "score_w", "score_eq", "rank_eq", "chg", "hit", "n_in"]].to_string(index=False))

ind = full.groupby("industry").agg(n=("score_w", "size"), score_w=("score_w", "mean"),
        score_eq=("score_eq", "mean")).reset_index()
ind = ind[ind["n"] >= 10].sort_values("score_w", ascending=False, kind="mergesort")
ind["rank_w"] = ind["score_w"].rank(ascending=False, method="min").astype(int)
ind.to_csv(os.path.join(OUT, "industry_weighted_rank.csv"), index=False, encoding="utf-8-sig")
print("\n=== 加权行业榜 Top12 ===")
print(ind.head(12)[["rank_w", "industry", "score_w", "score_eq", "n"]].to_string(index=False))

watch = ["存储芯片","光刻机","国家大基金持股","先进封装","光刻胶","玻璃基板","MLCC概念","PET铜箔",
         "第三代半导体","磷化工","PCB概念","光纤概念","铜缆高速连接","氟化工概念","共封装光学(CPO)",
         "芯片概念","特斯拉概念","无人驾驶","一体化压铸","机器人概念","机器视觉","人形机器人",
         "减速器","创新药","CRO概念","AI应用","人工智能"]
w = th[th["board_name"].isin(watch)][["board_name","score_w","rank_w","rank_eq","hit","n_in"]].sort_values("score_w", ascending=False)
print("\n=== 关注主题加权落点 ===")
print(w.to_string(index=False))
