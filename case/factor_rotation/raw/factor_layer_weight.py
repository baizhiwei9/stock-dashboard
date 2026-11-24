# -*- coding: utf-8 -*-
"""因子层权重搜索（纯因子口径，不含任何板块价格动量）。

输入：output/factor_layer_matrix.csv —— 概念(133) × 因子(26) 的得分矩阵，
      单元格 = 该概念成分股（全 A 可交易池内、有该因子值）direction 调整后
      池内分位(pct rank 0~1)的等权平均，来自 raw/factor_layer_diag.py 口径。

搜索：
  在非负、Σw=1 的权重空间随机搜索（稀疏支撑 + 稠密 Dirichlet 混合），
  目标 = 概念层名次前 10 同时含「电机」与「AI视频」。
  若可达则输出满足约束的权重族；若不可达则给出可达的最优 max(名次)。

输出：
  output/factor_layer_weight.csv       搜索结果（前若干条，含权重串、两目标名次、前10名单）
  output/factor_layer_weight_best.json 最优权重与完整前 15 名单
"""
import os
import json
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW, OUT = os.path.join(ROOT, "raw"), os.path.join(ROOT, "output")

TARGETS = ["电机", "AI视频"]

M = pd.read_csv(os.path.join(OUT, "factor_layer_matrix.csv"), index_col=0)
M.columns = [str(c) for c in M.columns]
FNAMES = list(M.columns)
BCS = list(M.index)

tm = pd.concat([pd.read_csv(os.path.join(RAW, "theme_member_all.csv"), dtype=str),
                pd.read_csv(os.path.join(RAW, "theme_member_ai.csv"), dtype=str)],
               ignore_index=True).drop_duplicates(["board_code", "con_code"])
con_names = tm.drop_duplicates("board_code").set_index("board_code")["board_name"].to_dict()

mask = M.notna().values.astype(np.float64)          # 133 x 26
M0 = M.fillna(0.0).values.astype(np.float64)        # 133 x 26
assert mask.shape == M0.shape

tgt_idx = {}
for t in TARGETS:
    hits = [i for i, bc in enumerate(BCS) if con_names.get(bc) == t]
    if not hits:
        raise SystemExit(f"概念层找不到 {t}")
    tgt_idx[t] = hits[0]
print(f"[matrix] {M.shape[0]} 概念 x {M.shape[1]} 因子；目标 {[(t, BCS[i]) for t, i in tgt_idx.items()]}")

rng = np.random.default_rng(20261029)
NP = len(FNAMES)
SAMPLE_TOTAL = 400_000
BATCH = 5000


def sample_weights(n):
    """混合采样：一半稀疏支撑（1~6 个因子），一半稠密 Dirichlet(0.5)。"""
    W = np.zeros((n, NP))
    half = n // 2
    # 稀疏
    ks = rng.integers(1, 7, size=half)
    for j in range(half):
        k = int(ks[j])
        sup = rng.choice(NP, size=k, replace=False)
        w = rng.dirichlet(np.ones(k))
        W[j, sup] = w
    # 稠密
    W[half:] = rng.dirichlet(np.full(NP, 0.5), size=n - half)
    return W


def evaluate(W):
    num = M0.dot(W.T)        # 133 x n
    den = mask.dot(W.T)
    with np.errstate(invalid="ignore", divide="ignore"):
        S = num / den
    S[~np.isfinite(S)] = -np.inf
    out = {}
    for t, i in tgt_idx.items():
        out[t] = 1.0 + (S > S[i]).sum(axis=0)
    return S, out


best = None
n_sat = 0
recs = []
done = 0
while done < SAMPLE_TOTAL:
    n = min(BATCH, SAMPLE_TOTAL - done)
    W = sample_weights(n)
    S, R = evaluate(W)
    jd, av = R["电机"], R["AI视频"]
    mx = np.maximum(jd, av)
    sat = mx <= 10
    n_sat += int(sat.sum())
    order = np.argsort(mx, kind="mergesort")[:5]
    for idx in order:
        recs.append({
            "max_rk": int(mx[idx]),
            "rk_电机": int(jd[idx]),
            "rk_AI视频": int(av[idx]),
            "w": W[idx],
            "S": S[:, idx],
        })
    # 记录全局最优
    mi = int(np.argmin(mx))
    if best is None or mx[mi] < best["max_rk"]:
        best = {"max_rk": int(mx[mi]), "rk_电机": int(jd[mi]), "rk_AI视频": int(av[mi]),
                "w": W[mi].copy(), "S": S[:, mi].copy()}
    done += n
    if done % 50000 == 0:
        print(f"[scan] {done}/{SAMPLE_TOTAL}  满足约束累计 {n_sat}  best max_rk={best['max_rk']}")

recs.sort(key=lambda r: r["max_rk"])
# 去重：以权重四舍五入后的字符串为键
seen, dedup = set(), []
names = FNAMES


def wstr(w):
    return "+".join(f"{names[i]}:{w[i]:.2f}" for i in range(NP) if w[i] > 1e-6)


for r in recs:
    s = wstr(r["w"])
    if s in seen:
        continue
    seen.add(s)
    rk = pd.Series(r["S"], index=BCS).rank(ascending=False, method="min")
    top = rk.sort_values(kind="mergesort").head(10)
    r["top10"] = "、".join(f"{con_names.get(bc, bc)}(#{int(v)})" for bc, v in top.items())
    r["wstr"] = s
    dedup.append(r)
    if len(dedup) >= 200:
        break

rows = [{"max_named_rank": r["max_rk"], "rk_电机": r["rk_电机"], "rk_AI视频": r["rk_AI视频"],
         "weights": r["wstr"], "top10": r["top10"]} for r in dedup]
pd.DataFrame(rows).to_csv(os.path.join(OUT, "factor_layer_weight.csv"), index=False)

rk_best = pd.Series(best["S"], index=BCS).rank(ascending=False, method="min")
top15 = rk_best.sort_values(kind="mergesort").head(15)
payload = {
    "satisfiable": bool(best["max_rk"] <= 10),
    "n_satisfied_samples": n_sat,
    "n_samples": SAMPLE_TOTAL,
    "best": {
        "max_named_rank": best["max_rk"],
        "rk_电机": best["rk_电机"],
        "rk_AI视频": best["rk_AI视频"],
        "weights": {names[i]: float(best["w"][i]) for i in range(NP) if best["w"][i] > 1e-9},
        "weights_str": wstr(best["w"]),
        "top15": [{"rank": int(v), "code": bc, "name": con_names.get(bc, bc),
                   "score": round(float(best["S"][BCS.index(bc)]), 4)} for bc, v in top15.items()],
    },
}
with open(os.path.join(OUT, "factor_layer_weight_best.json"), "w", encoding="utf-8") as fh:
    json.dump(payload, fh, ensure_ascii=False, indent=2)

print(f"\n[satisfiable] {payload['satisfiable']}  满足样本 {n_sat}/{SAMPLE_TOTAL}")
print(f"[best] max_named_rank={best['max_rk']}  电机 #{best['rk_电机']}  AI视频 #{best['rk_AI视频']}")
print(f"[best weights] {payload['best']['weights_str']}")
print("\n[best top15]")
for it in payload["best"]["top15"]:
    print(f"  #{it['rank']:<3d} {it['name']:<14s} {it['score']:.4f}")
print("\n[done] -> output/factor_layer_weight.csv / _best.json")