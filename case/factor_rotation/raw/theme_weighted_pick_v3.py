# -*- coding: utf-8 -*-
"""打分截面刷新到 2026-10-28 后的四列输出：因子 / 加权行业 / 加权主题概念 / 加权个股
+ 共振（hit）计数 + 与近期涨跌的相关性诊断 + 非反转三因子对照榜
输出：output/industry_weighted_final.csv、output/theme_weighted_final.csv、
      output/stock_weighted_final.csv、output/diag_weighted_vs_ret.csv
"""
import os, json
import numpy as np, pandas as pd

RAW = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.abspath(os.path.join(RAW, "..", "output"))
XSEC, PREV = "20261028", "20261023"
N_IND, N_THEME, N_STOCK = 20, 20, 20
MIN_IND_N, MIN_THEME_N = 10, 5
MIN_MV = 300000.0     # 万元 => 30 亿

picks = json.load(open(os.path.join(RAW, "top_factor_picks.json"), encoding="utf-8"))
FACS = [(f["factor"], f["direction"]) for f in picks["factors"]]
W = {f["factor"]: len(FACS) + 1 - f["rank"] for f in picks["factors"]}
WT = sum(W.values())
print("因子来源周(完整周口径):", picks["week"], "| 原打分截面:", picks["xsec_date"], "| 本次打分截面:", XSEC)
print("因子与权重:", {f: f"{W[f]}/{WT}({W[f]/WT:.1%})" for f, _ in FACS})

sb = pd.read_csv(os.path.join(RAW, "stock_basic.csv"), dtype={"ts_code": str})[["ts_code", "name", "industry"]]
xs = pd.read_csv(os.path.join(RAW, "latest_cross_section.csv")).merge(sb, on="ts_code", how="left")
S = pd.DataFrame(index=xs.index)
for f, d in FACS:
    pr = xs[f].rank(pct=True)
    S[f] = pr if d > 0 else 1.0 - pr
xs["n_valid"] = S.notna().sum(axis=1)
xs["score_w"] = sum(S[f].fillna(0) * W[f] for f, _ in FACS) / WT
xs["score_eq"] = S.mean(axis=1, skipna=True)
Wnr = {f: W[f] for f, d in FACS if d > 0}
xs["score_nr"] = sum(S[f].fillna(0) * Wnr[f] for f in Wnr) / sum(Wnr.values())
print(f"候选池(全部 {len(FACS)} 因子有值): {int((xs['n_valid']==len(FACS)).sum())} 只")
full = xs[xs["n_valid"] == len(FACS)].copy()

# ---------- 行情：近3日(10-23→10-28) / 近20日 / 近60日 ----------
dly = pd.read_csv(os.path.join(RAW, "daily_260d.csv"), dtype={"ts_code": str, "trade_date": str},
                  usecols=["ts_code", "trade_date", "close"])
px = dly.pivot_table(index="trade_date", columns="ts_code", values="close").sort_index()
alld = [d for d in px.index if d <= XSEC]
def ret(n):
    return (px.loc[alld[-1]] / px.loc[alld[-1 - n]] - 1).rename(f"r{n}")
R = pd.concat([ret(3).rename("r3"), ret(20).rename("r20"), ret(60).rename("r60")], axis=1)
full = full.merge(R, left_on="ts_code", right_index=True, how="left")

# ---------- 个股榜 ----------
pool = full[full["name"].notna() & ~full["name"].astype(str).str.contains("ST|退", regex=True)]
pool = pool[pool["总市值"] >= MIN_MV]
pool = pool.sort_values(["score_w", "总市值"], ascending=[False, False], kind="mergesort")
stk = pool.head(N_STOCK).copy()
stk[["ts_code", "name", "industry", "score_w", "score_nr", "r3", "r20", "r60", "总市值"]].to_csv(
    os.path.join(OUT, "stock_weighted_final.csv"), index=False, encoding="utf-8-sig")
print(f"\n个股池(全因子有值+≥30亿+非ST/退): {len(pool)} 只")
print("=== 加权个股 Top20 ===")
for i, (_, r) in enumerate(stk.iterrows(), 1):
    print(f"{i:>2}. {r['name']:<6} {str(r['industry']):<6} 加权分 {r['score_w']:.4f}  "
          f"近3日 {r['r3']*100:6.1f}% 近20日 {r['r20']*100:6.1f}% 近60日 {r['r60']*100:6.1f}%  市值 {r['总市值']/10000:.0f}亿")

# ---------- 主题概念 ----------
mem = pd.read_csv(os.path.join(RAW, "theme_member_all.csv"), dtype={"board_code": str, "con_code": str})
tot = mem.groupby("board_code").size().reset_index(name="n_total")
mm = mem.merge(full[["ts_code", "score_w", "score_eq", "score_nr", "r3", "r20", "r60"]],
               left_on="con_code", right_on="ts_code")
th = mm.groupby(["board_code", "board_name"]).agg(
    n_in=("score_w", "size"), score_w=("score_w", "mean"), score_eq=("score_eq", "mean"),
    score_nr=("score_nr", "mean"), r3=("r3", "mean"), r20=("r20", "mean"), r60=("r60", "mean")).reset_index()
th = th.merge(tot, on="board_code")
th["coverage"] = (th["n_in"] / th["n_total"]).round(3)
th = th[th["n_in"] >= MIN_THEME_N].copy()
for c in ["score_w", "score_eq", "score_nr"]:
    th["rank_" + c] = th[c].rank(ascending=False, method="min").astype(int)
th["rank_r3"] = th["r3"].rank(ascending=False, method="min").astype(int)

# 共振：某主题进入几个单因子前 20
mm2 = mem.merge(full[["ts_code"] + [f for f, _ in FACS]], left_on="con_code", right_on="ts_code")
hit_th = {}
for f, d in FACS:
    pr = mm2[f].rank(pct=True); s = pr if d > 0 else 1.0 - pr
    a = pd.DataFrame({"b": mm2["board_name"], "s": s}).dropna().groupby("b")["s"].agg(["mean", "size"])
    for b in a[a["size"] >= MIN_THEME_N].sort_values("mean", ascending=False, kind="mergesort").head(20).index:
        hit_th[b] = hit_th.get(b, 0) + 1
th["hit"] = th["board_name"].map(hit_th).fillna(0).astype(int)
th.to_csv(os.path.join(OUT, "theme_weighted_final.csv"), index=False, encoding="utf-8-sig")
th = th.sort_values("score_w", ascending=False, kind="mergesort")
print(f"\n参与排名的主题数: {len(th)}（样本≥{MIN_THEME_N} 只）")
print("=== 加权主题概念 Top20（打分截面 2026-10-28）===")
print(th.head(N_THEME)[["rank_score_w", "board_name", "score_w", "hit", "n_in", "r3", "r20", "r60", "rank_r3"]].to_string(
    index=False, float_format=lambda v: f"{v:.4f}"))

# ---------- 行业 ----------
ind = full.groupby("industry").agg(n=("score_w", "size"), score_w=("score_w", "mean"),
                                   score_eq=("score_eq", "mean"), score_nr=("score_nr", "mean"),
                                   r3=("r3", "mean"), r20=("r20", "mean"), r60=("r60", "mean")).reset_index()
ind = ind[ind["n"] >= MIN_IND_N].copy()
for c in ["score_w", "score_eq", "score_nr"]:
    ind["rank_" + c] = ind[c].rank(ascending=False, method="min").astype(int)
ind["rank_r3"] = ind["r3"].rank(ascending=False, method="min").astype(int)
mm3 = full[["industry"] + [f for f, _ in FACS]]
hit_ind = {}
for f, d in FACS:
    pr = mm3[f].rank(pct=True); s = pr if d > 0 else 1.0 - pr
    a = pd.DataFrame({"b": mm3["industry"], "s": s}).dropna().groupby("b")["s"].agg(["mean", "size"])
    for b in a[a["size"] >= MIN_IND_N].sort_values("mean", ascending=False, kind="mergesort").head(20).index:
        hit_ind[b] = hit_ind.get(b, 0) + 1
ind["hit"] = ind["industry"].map(hit_ind).fillna(0).astype(int)
ind = ind.sort_values("score_w", ascending=False, kind="mergesort")
ind.to_csv(os.path.join(OUT, "industry_weighted_final.csv"), index=False, encoding="utf-8-sig")
print(f"\n参与排名的行业数: {len(ind)}（样本≥{MIN_IND_N} 只）")
print("=== 加权行业 Top20 ===")
print(ind.head(N_IND)[["rank_score_w", "industry", "score_w", "hit", "n", "r3", "r20", "r60", "rank_r3"]].to_string(
    index=False, float_format=lambda v: f"{v:.4f}"))

# ---------- 诊断：加权分 vs 近期涨跌 ----------
print("\n===== 诊断：加权综合分与近期涨跌幅的关系 =====")
rows = []
for nm, t in [("主题概念", th), ("行业", ind)]:
    rows.append({"层级": nm, "n": len(t),
                 "spearman_scorew_r3": t["score_w"].corr(t["r3"], method="spearman"),
                 "spearman_scorew_r20": t["score_w"].corr(t["r20"], method="spearman"),
                 "spearman_scorew_r60": t["score_w"].corr(t["r60"], method="spearman")})
dg = pd.DataFrame(rows)
print(dg.to_string(index=False, float_format=lambda v: f"{v:.3f}"))
dg.to_csv(os.path.join(OUT, "diag_weighted_vs_ret.csv"), index=False, encoding="utf-8-sig")
_t20 = th.head(N_THEME)
print("[对照] 主题加权分 Top20 平均近3日收益: %.2f%%  全部主题平均: %.2f%%" % (
    _t20["r3"].mean() * 100, th["r3"].mean() * 100))
print("[对照] 主题加权分 Top20 近3日跌幅名次(1=跌得最惨, 共%d): %s" % (
    len(th), th["r3"].rank(ascending=True, method="min").astype(int)[_t20.index].tolist()))
core = {"三个月反转", "一个月反转"}
print("\n[结构] 反转类因子合计权重: %.1f%%  非反转(EPTTM分位/PEG/公募重仓): %.1f%%" % (
    100 * sum(W[f] for f, _ in FACS if f in core) / WT,
    100 * sum(W[f] for f, _ in FACS if f not in core) / WT))

# ---------- 非反转三因子对照榜 ----------
print("\n=== 对照：去掉两个反转因子后（只剩 EPTTM一年分位点4/预期PEG2/公募重仓1 归一化）主题 Top10 ===")
print(th.sort_values("score_nr", ascending=False).head(10)[["board_name", "score_nr", "rank_score_nr", "r20", "rank_score_w"]].to_string(
    index=False, float_format=lambda v: f"{v:.4f}"))
print("=== 对照：非反转三因子 行业 Top10 ===")
print(ind.sort_values("score_nr", ascending=False).head(10)[["industry", "score_nr", "rank_score_nr", "r20", "rank_score_w"]].to_string(
    index=False, float_format=lambda v: f"{v:.4f}"))

# ---------- 权重结构敏感度：换成指数权重 1/2/4/8/16 ----------
We = {f["factor"]: 2 ** (len(FACS) - f["rank"]) for f in picks["factors"]}
Sexp = pd.DataFrame(index=full.index)
for f, d in FACS:
    pr = full[f].rank(pct=True)
    Sexp[f] = pr if d > 0 else 1.0 - pr
sxe = sum(Sexp[f].fillna(0) * We[f] for f, _ in FACS) / sum(We.values())
ie = pd.DataFrame({"b": full["industry"], "s": sxe}).dropna().groupby("b")["s"].agg(["mean", "size"])
ie = ie[ie["size"] >= MIN_IND_N].sort_values("mean", ascending=False, kind="mergesort")
print("\n===== 单因子各自指向哪里（按方向调整后分位取均值，样本门槛同上）=====")
for f, d in FACS:
    pr = full[f].rank(pct=True); ss = pr if d > 0 else 1.0 - pr
    ai = pd.DataFrame({"b": full["industry"], "s": ss}).dropna().groupby("b")["s"].agg(["mean", "size"])
    ai = ai[ai["size"] >= MIN_IND_N].sort_values("mean", ascending=False, kind="mergesort").head(5)
    th2 = pd.DataFrame({"b": mm2["board_name"], "s": (mm2[f].rank(pct=True) if d > 0 else 1.0 - mm2[f].rank(pct=True))}).dropna().groupby("b")["s"].agg(["mean", "size"])
    th2 = th2[th2["size"] >= MIN_THEME_N].sort_values("mean", ascending=False, kind="mergesort").head(5)
    arrow = "越跌越好" if d < 0 else "越大越好"
    print(f"[{f} · 权重{W[f]}/15 · {arrow}] 行业: " + " / ".join(f"{b}{r['mean']:.3f}" for b, r in ai.iterrows()))
    print(f"{' ' * (len(f)+9)}主题: " + " / ".join(f"{b}{r['mean']:.3f}" for b, r in th2.iterrows()))
print("\n=== 指数权重 16/8/4/2/1 下的行业 Top10 ===")
for i, (b, r) in enumerate(ie.head(10).iterrows(), 1):
    print(f"  {i}. {b:<6} {r['mean']:.4f} (n={int(r['size'])})")