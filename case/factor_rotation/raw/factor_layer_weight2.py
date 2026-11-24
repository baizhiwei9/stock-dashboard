# -*- coding: utf-8 -*-
"""因子层权重搜索 v2（纯因子口径 + 新追加的资金流因子；不含任何板块价格动量）。

输入：output/factor_layer_matrix_ext.csv（133 概念 × 28 因子，含 资金流5/资金流20）
做法：
  A) 穷举小支撑 + 步长网格（size 1~5），求得"可达的最优 max(名次)"
  B) 400k 随机权重（稀疏+稠密混合）作补充
  C) 记录满足约束（概念层前 10 同时含 电机 & AI视频）的权重族
输出：
  output/factor_layer_weight2.csv        满足/接近约束的权重组
  output/factor_layer_weight2_best.json  最优解（含前 15 名）
"""
import os
import sys
import json
import itertools
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW, OUT = os.path.join(ROOT, "raw"), os.path.join(ROOT, "output")

MAT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(OUT, "factor_layer_matrix_ext.csv")
TARGETS = ["电机", "AI视频"]

M = pd.read_csv(MAT, index_col=0)
FNAMES = [str(c) for c in M.columns]
BCS = list(M.index)
NP = len(FNAMES)
mask = M.notna().values.astype(np.float64)
M0 = M.fillna(0.0).values.astype(np.float64)

tm = pd.concat([pd.read_csv(os.path.join(RAW, "theme_member_all.csv"), dtype=str),
                pd.read_csv(os.path.join(RAW, "theme_member_ai.csv"), dtype=str)],
               ignore_index=True).drop_duplicates(["board_code", "con_code"])
con_names = tm.drop_duplicates("board_code").set_index("board_code")["board_name"].to_dict()
tgt_idx = {t: [i for i, bc in enumerate(BCS) if con_names.get(bc) == t][0] for t in TARGETS}
print(f"[matrix] {M.shape[0]}x{NP}  目标 {[(t, BCS[i]) for t, i in tgt_idx.items()]}")


def ranks_of(S):
    """S: 133 x G -> dict target -> 名次数组(G,)"""
    out = {}
    for t, i in tgt_idx.items():
        out[t] = 1.0 + (S > S[i]).sum(axis=0)
    return out


def wgrid(k, step):
    n = int(round(1.0 / step))
    pts = []
    for comb in itertools.combinations_with_replacement(range(n + 1), k - 1):
        last = n - sum(comb)
        if last < 0:
            continue
        v = np.array([c / n for c in comb] + [last / n])
        if abs(v.sum() - 1) < 1e-9:
            pts.append(v)
    return np.array(pts)


best = {"max_rk": 10 ** 9}
sols = []

STEP = {1: 1.0, 2: 0.02, 3: 0.05, 4: 0.10, 5: 0.20}
for k in (1, 2, 3, 4, 5):
    G = wgrid(k, STEP[k])
    nsat = 0
    cnt = 0
    for sup in itertools.combinations(range(NP), k):
        W = G  # G x k
        num = M0[:, sup].dot(W.T)
        den = mask[:, sup].dot(W.T)
        with np.errstate(invalid="ignore", divide="ignore"):
            S = num / den
        S[~np.isfinite(S)] = -np.inf
        R = ranks_of(S)
        mx = np.maximum(R["电机"], R["AI视频"])
        mi = int(np.argmin(mx))
        if mx[mi] < best["max_rk"]:
            vb = np.zeros(NP); vb[list(sup)] = G[mi]
            best = {"max_rk": int(mx[mi]), "k": k, "support": [FNAMES[j] for j in sup],
                    "rk_电机": int(R["电机"][mi]), "rk_AI视频": int(R["AI视频"][mi]),
                    "w": vb, "S": S[:, mi].copy()}
            print(f"    new best: max_rk={best['max_rk']} (电机 #{best['rk_电机']}, AI视频 #{best['rk_AI视频']})  sup={best['support']}")
        s = mx <= 10
        cnts = int(s.sum())
        nsat += cnts
        if cnts:
            for j in np.nonzero(s)[0][:3]:
                vb = np.zeros(NP); vb[list(sup)] = G[j]
                sols.append({"k": k, "max_rk": int(mx[j]), "rk_电机": int(R["电机"][j]),
                             "rk_AI视频": int(R["AI视频"][j]), "w": vb, "S": S[:, j].copy()})
        cnt += 1
    print(f"[grid] size={k} 支撑数 {cnt} 网格点/支撑 {len(G)} 满足枚举 {nsat}  best max_rk={best['max_rk']}")

# ---- 随机补充 ----
rng = np.random.default_rng(20261029)
BATCH, TOTAL, done, nsat_r = 5000, 400_000, 0, 0
while done < TOTAL:
    n = min(BATCH, TOTAL - done)
    W = np.zeros((n, NP))
    half = n // 2
    ks = rng.integers(1, 7, size=half)
    for j in range(half):
        kk = int(ks[j]); sup = rng.choice(NP, size=kk, replace=False)
        W[j, sup] = rng.dirichlet(np.ones(kk))
    W[half:] = rng.dirichlet(np.full(NP, 0.5), size=n - half)
    num = M0.dot(W.T); den = mask.dot(W.T)
    with np.errstate(invalid="ignore", divide="ignore"):
        S = num / den
    S[~np.isfinite(S)] = -np.inf
    R = ranks_of(S)
    mx = np.maximum(R["电机"], R["AI视频"])
    nsat_r += int((mx <= 10).sum())
    mi = int(np.argmin(mx))
    if mx[mi] < best["max_rk"]:
        best = {"max_rk": int(mx[mi]), "k": None, "support": "random",
                "rk_电机": int(R["电机"][mi]), "rk_AI视频": int(R["AI视频"][mi]),
                "w": W[mi].copy(), "S": S[:, mi].copy()}
        print(f"    new best(random): max_rk={best['max_rk']} (电机 #{best['rk_电机']}, AI视频 #{best['rk_AI视频']})")
    done += n
print(f"[random] {TOTAL} 样本 满足 {nsat_r}")

# ---- 输出 ----
rk_best = pd.Series(best["S"], index=BCS).rank(ascending=False, method="min")
top15 = rk_best.sort_values(kind="mergesort").head(15)
wstr = "+".join(f"{FNAMES[i]}:{best['w'][i]:.3f}" for i in range(NP) if best["w"][i] > 1e-6)
payload = {
    "matrix": os.path.basename(MAT),
    "satisfiable": bool(best["max_rk"] <= 10),
    "n_solutions_grid": len(sols),
    "n_satisfied_random": nsat_r,
    "best": {
        "max_named_rank": best["max_rk"], "rk_电机": best["rk_电机"], "rk_AI视频": best["rk_AI视频"],
        "support_k": best["k"], "support": best["support"], "weights_str": wstr,
        "weights": {FNAMES[i]: round(float(best["w"][i]), 4) for i in range(NP) if best["w"][i] > 1e-6},
        "top15": [{"rank": int(v), "code": bc, "name": con_names.get(bc, bc),
                   "score": round(float(best["S"][BCS.index(bc)]), 4)} for bc, v in top15.items()],
    },
}
with open(os.path.join(OUT, "factor_layer_weight2_best.json"), "w", encoding="utf-8") as fh:
    json.dump(payload, fh, ensure_ascii=False, indent=2)

if sols:
    sols.sort(key=lambda r: r["max_rk"])
    rows = []
    seen = set()
    for r in sols:
        s = "+".join(f"{FNAMES[i]}:{r['w'][i]:.2f}" for i in range(NP) if r["w"][i] > 1e-6)
        if s in seen:
            continue
        seen.add(s)
        rk = pd.Series(r["S"], index=BCS).rank(ascending=False, method="min")
        top10 = rk.sort_values(kind="mergesort").head(10)
        rows.append({"max_named_rank": r["max_rk"], "rk_电机": r["rk_电机"], "rk_AI视频": r["rk_AI视频"],
                     "size": r["k"], "weights": s,
                     "top10": "、".join(f"{con_names.get(bc, bc)}(#{int(v)})" for bc, v in top10.items())})
        if len(rows) >= 100:
            break
    pd.DataFrame(rows).to_csv(os.path.join(OUT, "factor_layer_weight2.csv"), index=False)

print(f"\n[satisfiable] {payload['satisfiable']}  grid满足解 {len(sols)}  random满足 {nsat_r}")
print(f"[best] #{best['max_rk']} 型 电机 #{best['rk_电机']}  AI视频 #{best['rk_AI视频']}")
print(f"[weights] {wstr}")
print("[top15]")
for it in payload["best"]["top15"]:
    print(f"  #{it['rank']:<3d} {it['name']:<14s} {it['score']:.4f}")
print("\n[done] -> output/factor_layer_weight2.csv / _best.json")