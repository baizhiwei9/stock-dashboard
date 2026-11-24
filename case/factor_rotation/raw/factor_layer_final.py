# -*- coding: utf-8 -*-
"""因子层最终组合搜索（纯个股因子口径，聚合到概念层）。

口径（与 factor_layer_diag.py / factor_layer_mf.py 完全一致）：
  1) 打分池 = 全 A 可交易池（obs>=120 & 最近交易日有成交 & 非 ST）—— 不是 4 指数池。
  2) 每个个股因子 direction 调整后（值×direction，统一"大=好"）在池内做 pct rank（0~1）。
  3) 概念层得分 = 成分股（在池内且有该因子值）分位的等权平均；名次 = 133 概念降序排名。
  4) 新增资金流因子：资金流N = Σ(近N日 net_mf_amount×10) / Σ(近N日 amount)，方向 +1。
  5) 组合得分 S = Σ w_i × 概念层因子分位。

约束：概念层前 10 同时含 「电机」与「AI视频」。
做法：枚举 k=1..5 因子子集 × 权重网格（非负 simplex / 允许反向 signed），
      直接按 S > S[target] 计名次（不做 big-M 线性化），并给稳健边际 margin。

输出：
  output/factor_layer_final.csv       全部满足约束的候选（去重后按 max_rk 排序）
  output/factor_layer_final_best.json 推荐组合 + 各自概念层 Top10/前10名单
  raw/factor_layer_stock_pct.csv      个股层 28 因子分位矩阵（池内），供个股名单复用
"""
import os
import sys
import json
import itertools
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW, OUT = os.path.join(ROOT, "raw"), os.path.join(ROOT, "output")
sys.path.insert(0, RAW)
from _daily_snap import read_daily  # noqa: E402

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
TARGETS = ["电机", "AI视频"]

# ---------- 池 ----------
# 区间表 daily_260d.csv + 最新单日快照 daily_YYYYMMDD.csv（自动跟随最新交易日，不再硬编码日期）
daily, AS_OF = read_daily(RAW, usecols=["ts_code", "trade_date", "amount"],
                          dtypes={"ts_code": str, "trade_date": str})
daily["amount"] = pd.to_numeric(daily["amount"], errors="coerce")
dates = sorted(daily["trade_date"].unique())
amt = daily.pivot_table(index="trade_date", columns="ts_code", values="amount", aggfunc="last").sort_index(axis=1)
sb = pd.read_csv(os.path.join(RAW, "stock_basic.csv"), dtype=str)
NAME = sb.set_index("ts_code")["name"].to_dict()
ST = pd.Series({t: ("ST" in (NAME.get(t, "") or "")) or ("退" in (NAME.get(t, "") or "")) for t in amt.columns})
obs = amt.notna().sum()
UPTO = len(dates) - 1
tr = (obs >= 120) & amt.iloc[UPTO].notna() & (amt.iloc[UPTO] > 0) & ~ST.reindex(amt.columns).fillna(False)
pool = sorted(tr[tr].index)
print(f"[pool] 全 A 可交易 {len(pool)}；截面日 {dates[UPTO]}")

# ---------- 个股层 28 因子分位 ----------
xs = pd.read_csv(os.path.join(RAW, "latest_cross_section_fullA.csv"), dtype={"ts_code": str}).set_index("ts_code")
P = pd.DataFrame(index=pool)
for f, d in FACTORS:
    v = pd.to_numeric(xs[f], errors="coerce").reindex(pool) * d
    P[f] = v.rank(pct=True, ascending=True, method="average")

mf = pd.read_csv(os.path.join(RAW, "moneyflow_daily.csv"),
                 usecols=["trade_date", "ts_code", "net_mf_amount"], dtype={"trade_date": str, "ts_code": str})
mf["net_mf_amount"] = pd.to_numeric(mf["net_mf_amount"], errors="coerce")
piv = mf.pivot_table(index="trade_date", columns="ts_code", values="net_mf_amount", aggfunc="last").sort_index()
for w in (5, 20):
    ratio = (piv.iloc[-w:].sum(min_count=1) * 10.0) / amt.iloc[-w:].sum(min_count=1).replace(0, np.nan)
    P[f"资金流{w}"] = ratio.reindex(pool).rank(pct=True, ascending=True, method="average")

P = P[[f for f, _ in FACTORS] + ["资金流5", "资金流20"]]
P.to_csv(os.path.join(RAW, "factor_layer_stock_pct.csv"))
NAMES = list(P.columns)
print(f"[stock_pct] {P.shape}  非空率 {P.notna().mean().mean():.3f}")

# ---------- 概念层聚合 ----------
tm = pd.concat([pd.read_csv(os.path.join(RAW, "theme_member_all.csv"), dtype=str),
                pd.read_csv(os.path.join(RAW, "theme_member_ai.csv"), dtype=str)],
               ignore_index=True).drop_duplicates(["board_code", "con_code"])
con_members = {bc: sorted(set(g["con_code"])) for bc, g in tm.groupby("board_code")}
con_names = tm.drop_duplicates("board_code").set_index("board_code")["board_name"].to_dict()
con_members = {bc: m for bc, m in con_members.items() if len([c for c in m if c in amt.columns]) >= 5}
BCS = sorted(con_members)
X = np.column_stack([P.reindex(con_members[bc]).mean().values for bc in BCS])  # 28 x 133
X = np.nan_to_num(X, nan=np.nan)
print(f"[concept] {len(BCS)} 个概念；矩阵 {X.shape}  NaN={int(np.isnan(X).sum())}")

TIDX = {t: BCS.index(next(bc for bc in BCS if con_names.get(bc) == t)) for t in TARGETS}
print(f"[target] {[(t, BCS[i], con_names[BCS[i]]) for t, i in TIDX.items()]}")

# 一致性自检：单因子名次应与 factor_layer_diag.csv 一致
diag = pd.read_csv(os.path.join(OUT, "factor_layer_diag.csv"))
chk = 0
for i, f in enumerate(NAMES):
    v = X[i]
    rk = pd.Series(v, index=BCS).rank(ascending=False, method="min")
    if f in set(diag["factor"]):
        row = diag[diag["factor"] == f].iloc[0]
        if int(rk[BCS[TIDX["电机"]]]) == int(row["rk_电机"]) and \
           int(rk[BCS[TIDX["AI视频"]]]) == int(row["rk_AI视频"]):
            chk += 1
print(f"[consistency] 与 diag 一致的单因子数 {chk}/{len(set(diag['factor']))}")


def simplex(k, step):
    n = int(round(1 / step))
    out = []
    for comb in itertools.combinations_with_replacement(range(n + 1), k - 1):
        last = n - sum(comb)
        if last < 0:
            continue
        out.append([c / n for c in comb] + [last / n])
    return np.array(out)


def signed(k, step):
    gs = np.arange(-1.0, 1.0 + 1e-9, step)
    out = []
    for comb in itertools.product(gs, repeat=k - 1):
        last = 1.0 - sum(comb)
        if abs(last) > 1.0 + 1e-9:
            continue
        out.append(list(comb) + [last])
    return np.array(out)


plans = []
for k in (1, 2, 3, 4, 5):
    plans.append((k, "nonneg", simplex(k, {1: 1.0, 2: 0.01, 3: 0.02, 4: 0.05, 5: 0.10}[k])))
for k in (2, 3):
    plans.append((k, "signed", signed(k, {2: 0.05, 3: 0.10}[k])))

recs = []
for k, mode, G in plans:
    cnt = nsat = 0
    for comb in itertools.combinations(range(len(NAMES)), k):
        Xc = X[list(comb), :]          # k x 133
        S = G @ Xc                     # G x 133
        rk_e = 1 + (S > S[:, [TIDX["电机"]]]).sum(axis=1)
        rk_a = 1 + (S > S[:, [TIDX["AI视频"]]]).sum(axis=1)
        mx = np.maximum(rk_e, rk_a)
        s = mx <= 10
        cnt += 1
        nsat += int(s.sum())
        for j in np.nonzero(s)[0]:
            w = np.zeros(len(NAMES))
            w[list(comb)] = G[j]
            rkall = pd.Series(S[j], index=BCS).rank(ascending=False, method="min")
            # margin：两个目标中较弱者高出第 11 名的幅度
            sc = pd.Series(S[j], index=BCS)
            srt = rkall.sort_values(kind="mergesort")
            top10 = srt.head(10)
            r11_bc = srt.index[10]
            r11sc = sc[r11_bc]
            targets_sc = [sc[BCS[TIDX[t]]] for t in TARGETS]
            margin = float(min(targets_sc) - r11sc)
            recs.append({"mode": mode, "size": k, "max_named_rank": int(mx[j]),
                         "rk_电机": int(rk_e[j]), "rk_AI视频": int(rk_a[j]),
                         "margin": round(margin, 4),
                         "weights": "+".join(f"{NAMES[i]}:{round(w[i],4)}" for i in range(len(NAMES)) if abs(w[i]) > 1e-9),
                         "n_neg": int((w < -1e-9).sum()),
                         "_w": w, "_S": S[j].copy()})
    print(f"[scan] {mode} k={k} 组合 {cnt} 满足 {nsat}")
print(f"[satisfied] {len(recs)}")

# 去重（保留 margin 最大者），按 max_rk → margin 降序
best_by_w = {}
for r in recs:
    key = tuple(np.round(r["_w"], 4).tolist())
    if key not in best_by_w or r["margin"] > best_by_w[key]["margin"]:
        best_by_w[key] = r
uniq = sorted(best_by_w.values(), key=lambda r: (r["max_named_rank"], -r["margin"]))

rows = []
for r in uniq:
    rk = pd.Series(r["_S"], index=BCS).rank(ascending=False, method="min")
    top = rk.sort_values(kind="mergesort").head(10)
    rows.append({"mode": r["mode"], "size": r["size"], "max_named_rank": r["max_named_rank"],
                 "rk_电机": r["rk_电机"], "rk_AI视频": r["rk_AI视频"], "margin": r["margin"],
                 "n_neg": r["n_neg"], "weights": r["weights"],
                 "top10": "、".join(f"{con_names.get(bc, bc)}(#{int(v)})" for bc, v in top.items())})
pd.DataFrame(rows).to_csv(os.path.join(OUT, "factor_layer_final.csv"), index=False)
print(f"[final.csv] {len(rows)} 行")

nn = [r for r in uniq if r["n_neg"] == 0]
print(f"[nonneg 满足数] {len(nn)}")

# ---- 推荐组合 ----
def find(pred, key):
    cand = [r for r in uniq if pred(r)]
    if not cand:
        return None
    return sorted(cand, key=key)[0]

rec_nn = find(lambda r: r["n_neg"] == 0, lambda r: (r["max_named_rank"], r["size"], -r["margin"]))
rec_nn_simple = find(lambda r: r["n_neg"] == 0 and r["size"] == 2, lambda r: (r["max_named_rank"], -r["margin"]))
rec_signed = find(lambda r: r["n_neg"] > 0, lambda r: (r["max_named_rank"], r["size"], -r["margin"]))

# 语义最干净的非负三因子：不含反转类（反转类=一个月反转/三个月反转），且含资金流
REV = {"一个月反转", "三个月反转"}
def has_rev(r):
    return any(n in REV for n in r["weights"].split("+")[0:0]) or any(
        w.split(":")[0] in REV for w in r["weights"].split("+"))
rec_nn_norev = find(lambda r: r["n_neg"] == 0 and not has_rev(r),
                    lambda r: (r["max_named_rank"], r["size"], -r["margin"]))


def pack(r, tag):
    if r is None:
        return None
    sc = pd.Series(r["_S"], index=BCS)
    rk = sc.rank(ascending=False, method="min")
    top10 = rk.sort_values(kind="mergesort").head(10)
    # 个股名单：概念内按组合得分排序
    w = r["_w"]
    stk_sc = P[NAMES].fillna(0.5) @ w          # 池内个股组合分
    out = {"tag": tag, "mode": r["mode"], "size": r["size"],
           "max_named_rank": r["max_named_rank"], "rk_电机": r["rk_电机"], "rk_AI视频": r["rk_AI视频"],
           "margin": r["margin"], "n_neg": r["n_neg"], "weights": r["weights"],
           "top10": [{"rank": int(v), "code": bc, "name": con_names.get(bc, bc),
                      "score": round(float(sc[bc]), 4)} for bc, v in top10.items()]}
    hits = {"targets_in_top10": [con_names[BCS[TIDX[t]]] for t in TARGETS
                                 if rk[BCS[TIDX[t]]] <= 10]}
    tgt_stocks = {}
    for t in TARGETS:
        bc = BCS[TIDX[t]]
        mem = [c for c in con_members[bc] if c in stk_sc.index]
        s = stk_sc.reindex(mem).sort_values(ascending=False, kind="mergesort").head(10)
        tgt_stocks[t] = [{"rank": i + 1, "ts_code": c, "name": NAME.get(c, c),
                          "score": round(float(v), 4)} for i, (c, v) in enumerate(s.items())]
    out["hits"] = hits
    out["target_top_stocks"] = tgt_stocks
    return out


payload = {
    "matrix": {"n_concept": len(BCS), "n_factor": len(NAMES), "date": dates[UPTO],
               "consistency_with_diag": f"{chk}/{len(set(diag['factor']))}"},
    "n_satisfied_uniq": len(uniq),
    "n_nonneg_satisfied": len(nn),
    "recommended": {
        "nonneg_best": pack(rec_nn, "非负·最优"),
        "nonneg_simple_k2": pack(rec_nn_simple, "非负·最简(2因子)"),
        "nonneg_norev": pack(rec_nn_norev, "非负·剔除反转类"),
        "signed_best": pack(rec_signed, "含负权(反向使用反转)"),
    },
}
with open(os.path.join(OUT, "factor_layer_final_best.json"), "w", encoding="utf-8") as fh:
    json.dump(payload, fh, ensure_ascii=False, indent=2)

for key in ["nonneg_best", "nonneg_simple_k2", "nonneg_norev", "signed_best"]:
    r = payload["recommended"][key]
    if r is None:
        print(f"\n[{key}] 无")
        continue
    print(f"\n===== {key} =====\n{r['weights']}  → 电机#{r['rk_电机']} AI视频#{r['rk_AI视频']} "
          f"max#{r['max_named_rank']} margin={r['margin']} 负权数={r['n_neg']}")
    for it in r["top10"]:
        print(f"   #{it['rank']:<3d} {it['name']:<16s} {it['score']:.4f}")
    for t, lst in r["target_top_stocks"].items():
        print(f"   [{t}] 概念内 Top10 个股： " + "、".join(x["name"] for x in lst))
print("\n[done] -> output/factor_layer_final.csv / _best.json / raw/factor_layer_stock_pct.csv")