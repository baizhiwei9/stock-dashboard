# -*- coding: utf-8 -*-
"""因子层小支撑穷举（纯因子口径，28 因子含 资金流5/20）。

目标：概念层（133）前 10 同时含「电机」与「AI视频」。
做法：枚举 k=1..4 的因子子集 × 权重网格（非负 F1 / 允许反向 F2 两版），
      得分 = Σ w_i × 概念层因子分位（成分股等权平均，池内 pct rank）。
输出：output/factor_layer_small.csv、output/factor_layer_small_best.json
"""
import os, itertools, json, numpy as np, pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW, OUT = os.path.join(ROOT, "raw"), os.path.join(ROOT, "output")

M = pd.read_csv(os.path.join(OUT, "factor_layer_matrix_ext.csv"), index_col=0)
NAMES = [str(c) for c in M.columns]
BCS = list(M.index)
X = M.values.astype(float)
tm = pd.concat([pd.read_csv(os.path.join(RAW, "theme_member_all.csv"), dtype=str),
                pd.read_csv(os.path.join(RAW, "theme_member_ai.csv"), dtype=str)],
               ignore_index=True).drop_duplicates(["board_code", "con_code"])
nm = tm.drop_duplicates("board_code").set_index("board_code")["board_name"].to_dict()
T = ("电机", "AI视频")
TIDX = {t: BCS.index(next(bc for bc in BCS if nm.get(bc) == t)) for t in T}
NP = len(NAMES)
print(f"[matrix] {M.shape}  targets {[(t, BCS[i]) for t, i in TIDX.items()]}")


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


def ranks(S):
    return {t: 1 + (S > S[TIDX[t]]).sum(axis=0) for t in T}


plans = []
for k in (1, 2, 3, 4):
    plans.append((k, "nonneg", simplex(k, {1: 1.0, 2: 0.02, 3: 0.05, 4: 0.10}[k])))
    if k <= 3:
        plans.append((k, "signed", signed(k, {1: 1.0, 2: 0.05, 3: 0.10}[k])))

recs = []
best = {"max_rk": 10 ** 9}
for k, mode, G in plans:
    cnt = nsat = 0
    for comb in itertools.combinations(range(NP), k):
        Xc = X[:, comb]
        S = Xc @ G.T                       # 133 x G
        R = ranks(S)
        mx = np.maximum(R["电机"], R["AI视频"])
        mi = int(np.argmin(mx))
        if mx[mi] < best["max_rk"]:
            w = np.zeros(NP); w[list(comb)] = G[mi]
            best = {"max_rk": int(mx[mi]), "mode": mode, "k": k,
                    "support": [NAMES[j] for j in comb],
                    "rk_电机": int(R["电机"][mi]), "rk_AI视频": int(R["AI视频"][mi]),
                    "w": w, "S": S[:, mi].copy()}
            print(f"  new best {mode} k={k}: max_rk={best['max_rk']} "
                  f"电机#{best['rk_电机']} AI#{best['rk_AI视频']} sup={best['support']}")
        s = mx <= 10
        c = int(s.sum()); nsat += c; cnt += 1
        for j in np.nonzero(s)[0][:5]:
            w = np.zeros(NP); w[list(comb)] = G[j]
            recs.append({"mode": mode, "k": k, "max_rk": int(mx[j]),
                         "rk_电机": int(R["电机"][j]), "rk_AI视频": int(R["AI视频"][j]),
                         "weights": "+".join(f"{NAMES[i]}:{w[i]:.3f}" for i in range(NP) if abs(w[i]) > 1e-9),
                         "w": w, "S": S[:, j].copy()})
    print(f"[scan] {mode} k={k} 组合 {cnt} 满足 {nsat}  best={best['max_rk']}")

print(f"\n[满足约束样本] {len(recs)}")
rows, seen = [], set()
for r in sorted(recs, key=lambda r: r["max_rk"]):
    if r["weights"] in seen:
        continue
    seen.add(r["weights"])
    rk = pd.Series(r["S"], index=BCS).rank(ascending=False, method="min")
    top = rk.sort_values(kind="mergesort").head(10)
    rows.append({"mode": r["mode"], "size": r["k"], "max_named_rank": r["max_rk"],
                 "rk_电机": r["rk_电机"], "rk_AI视频": r["rk_AI视频"], "weights": r["weights"],
                 "top10": "、".join(f"{nm.get(bc, bc)}(#{int(v)})" for bc, v in top.items())})
    if len(rows) >= 200:
        break
pd.DataFrame(rows).to_csv(os.path.join(OUT, "factor_layer_small.csv"), index=False)

rk_best = pd.Series(best["S"], index=BCS).rank(ascending=False, method="min")
top15 = rk_best.sort_values(kind="mergesort").head(15)
payload = {
    "satisfiable": bool(best["max_rk"] <= 10),
    "n_satisfied": len(recs),
    "best": {
        "max_named_rank": best["max_rk"], "mode": best["mode"], "size": best["k"],
        "support": best["support"],
        "rk_电机": best["rk_电机"], "rk_AI视频": best["rk_AI视频"],
        "weights_str": "+".join(f"{NAMES[i]}:{round(float(best['w'][i]),4)}"
                               for i in range(NP) if abs(best["w"][i]) > 1e-9),
        "top15": [{"rank": int(v), "code": bc, "name": nm.get(bc, bc),
                   "score": round(float(best["S"][BCS.index(bc)]), 4)} for bc, v in top15.items()],
    },
}
with open(os.path.join(OUT, "factor_layer_small_best.json"), "w", encoding="utf-8") as fh:
    json.dump(payload, fh, ensure_ascii=False, indent=2)

print(f"[satisfiable] {payload['satisfiable']}  满足样本 {len(recs)}")
print(f"[best] max={best['max_rk']} 电机#{best['rk_电机']} AI#{best['rk_AI视频']} "
      f"mode={best['mode']} sup={best['support']}")
print(f"[weights] {payload['best']['weights_str']}")
for it in payload["best"]["top15"]:
    print(f"  #{it['rank']:<3d} {it['name']:<16s} {it['score']:.4f}")
print("\n[done] -> output/factor_layer_small.csv / _best.json")