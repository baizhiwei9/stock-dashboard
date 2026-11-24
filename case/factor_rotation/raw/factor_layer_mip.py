# -*- coding: utf-8 -*-
"""因子层 MIP 全局求解（纯因子口径，28 因子含 资金流5/20）。

求：是否存在权重 w，使「电机」与「AI视频」在 133 个概念层同时进入前 10。
模型（每目标 t、每个其它概念 c 一个二元 y_{t,c} 表示 c 排在 t 之前）：
    (X[c]-X[t])·w  -  Mb*y_{t,c}            <= 0
    (X[t]-X[c])·w  +  Mb*y_{t,c}            <=  Mb - eps
    名次_t = 1 + Σ_c y_{t,c}
目标：最小化 Σ y（等价于最小化两目标名次之和）。
F1：w>=0（只正向组合，符合因子 direction 语义）；F2：w 自由（允许反向使用因子）。
输出：output/factor_layer_mip.json
"""
import os, json, numpy as np, pandas as pd
from scipy.optimize import milp, LinearConstraint, Bounds
from scipy.sparse import lil_matrix

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
NP = len(NAMES); NC = len(BCS)
EPS = 1e-3; MB = 2.0

pairs = [(t, c) for t in T for c in range(NC) if c != TIDX[t]]
NY = len(pairs)
NV = NP + NY
print(f"[setup] 因子 {NP}  概念 {NC}  二元 {NY}  变量 {NV}")


def solve(nonneg):
    rows, lo, hi = [], [], []
    A = lil_matrix((2 * NY + 1, NV))
    for i, (t, c) in enumerate(pairs):
        ti = TIDX[t]
        a, b = i, i + 1
        # (X[c]-X[t])·w - MB*y <= 0
        A[2 * i, :NP] = X[c] - X[ti]
        A[2 * i, NP + i] = -MB
        lo.append(-np.inf); hi.append(0.0)
        # (X[t]-X[c])·w + MB*y <= MB - eps
        A[2 * i + 1, :NP] = X[ti] - X[c]
        A[2 * i + 1, NP + i] = MB
        lo.append(-np.inf); hi.append(MB - EPS)
    # Σ w = 1
    A[2 * NY, :NP] = 1.0
    lo.append(1.0); hi.append(1.0)
    c_obj = np.zeros(NV); c_obj[NP:] = 1.0              # 最小化 Σ y
    integ = np.zeros(NV); integ[NP:] = 1
    if nonneg:
        bnd = Bounds(np.r_[np.zeros(NP), np.zeros(NY)], np.r_[np.ones(NP), np.ones(NY)])
    else:
        bnd = Bounds(np.r_[-np.ones(NP), np.zeros(NY)], np.r_[np.ones(NP), np.ones(NY)])
    res = milp(c=c_obj, constraints=LinearConstraint(A.tocsr(), np.array(lo), np.array(hi)),
               integrality=integ, bounds=bnd, options={"time_limit": 300})
    if not res.success:
        return None
    w = res.x[:NP]
    S = X @ w
    rk = 1 + (S > S[TIDX["电机"]]).sum(), 1 + (S > S[TIDX["AI视频"]]).sum()
    order = pd.Series(S, index=BCS).rank(ascending=False, method="min").sort_values(kind="mergesort").head(15)
    return {"objective_sum_y": float(res.fun),
            "rk_电机": int(rk[0]), "rk_AI视频": int(rk[1]),
            "weights_str": "+".join(f"{NAMES[i]}:{round(float(w[i]),4)}" for i in range(NP) if abs(w[i]) > 1e-6),
            "weights": {NAMES[i]: round(float(w[i]), 4) for i in range(NP) if abs(w[i]) > 1e-6},
            "top15": [{"rank": int(v), "code": bc, "name": nm.get(bc, bc),
                       "score": round(float(S[BCS.index(bc)]), 4)} for bc, v in order.items()]}


out = {}
for tag, nn in (("F1_nonneg", True), ("F2_signed", False)):
    r = solve(nn)
    if r is None:
        out[tag] = {"solved": False}
        print(f"[{tag}] milp 无解/超时")
        continue
    r["solved"] = True
    r["satisfiable"] = bool(r["rk_电机"] <= 10 and r["rk_AI视频"] <= 10)
    out[tag] = r
    print(f"[{tag}] 电机#{r['rk_电机']} AI视频#{r['rk_AI视频']} 满足={r['satisfiable']} "
          f"obj={r['objective_sum_y']:.1f}")
    print(f"    w = {r['weights_str'][:200]}")
    for it in r["top15"][:10]:
        print(f"     #{it['rank']:<3d} {it['name']:<16s} {it['score']:.4f}")

out["targets"] = {t: BCS[TIDX[t]] for t in T}
with open(os.path.join(OUT, "factor_layer_mip.json"), "w", encoding="utf-8") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=2)
print("\n[done] -> output/factor_layer_mip.json")