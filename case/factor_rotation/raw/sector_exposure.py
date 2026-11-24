# -*- coding: utf-8 -*-
"""四板块（AI光模块/CPO、机器人/人形、存储芯片、创新药+CXO）因子暴露画像 v2
新增：机器人核心链收窄口径对照、板块内离散度、去均值后的相对风格冲击。
输入：raw/latest_cross_section.csv、raw/factor_weekly_raw.csv、raw/ths_member_*.csv、raw/style_angle_state.csv
输出：output/sector_exposure_matrix.csv / _raw.csv / _dispersion.csv
      output/sector_distinctness.csv / sector_style_impulse.csv / _detail.csv
      output/sector_exposure_heatmap.png / sector_exposure_summary.json
"""
import os, json
import numpy as np
import pandas as pd
from scipy.stats import norm
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

RAW = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output")
os.makedirs(OUT, exist_ok=True)
FONT = os.path.abspath(os.path.join(RAW, "..", "..", "..", "workspace", "skills", "user",
                                    "optimal-transport-ot", "SimHei.ttf"))
fp = font_manager.FontProperties(fname=FONT)
plt.rcParams["axes.unicode_minus"] = False

# ---------------- 1. 数据 ----------------
xs = pd.read_csv(os.path.join(RAW, "latest_cross_section.csv"), dtype={"ts_code": str})
fw = pd.read_csv(os.path.join(RAW, "factor_weekly_raw.csv"))
last_week = fw["week_end"].max()
fdef = (fw[fw["week_end"] == last_week].drop_duplicates("factor")[["factor", "group", "direction"]]
        .reset_index(drop=True))
FACTORS = list(fdef["factor"])
dir_map = dict(zip(fdef["factor"], fdef["direction"]))
grp_map = dict(zip(fdef["factor"], fdef["group"]))
print(f"[xsec] rows={len(xs)} | [factors] {len(FACTORS)} (正{(fdef.direction>0).sum()}/反{(fdef.direction<0).sum()}) week={last_week}")

# 主口径 4 板块 + 1 个收窄对照口径
MAIN = {
    "A_AI光模块CPO": ["ths_member_A_CPO.csv"],
    "B_机器人具身": ["ths_member_B_ROBOT.csv"],
    "C_存储芯片": ["ths_member_C_STOR.csv"],
    "D_创新药CXO": ["ths_member_D_INNO.csv", "ths_member_D_CRO.csv"],
}
ALT = {"B2_机器人核心链": ["ths_member_B2_REDUCER.csv"]}

def load_sector(files):
    codes = set()
    for f in files:
        d = pd.read_csv(os.path.join(RAW, f), dtype={"con_code": str})
        codes |= set(d["con_code"])
    return codes

uni = set(xs["ts_code"])
sec_codes, sec_meta = {}, {}
for sname, files in {**MAIN, **ALT}.items():
    codes = load_sector(files)
    inun = sorted(codes & uni)
    sec_codes[sname] = inun
    sec_meta[sname] = {"概念成分": len(codes), "样本空间内": len(inun),
                       "覆盖率": round(len(inun) / max(len(codes), 1), 4),
                       "中位总市值万元": round(float(pd.to_numeric(
                           xs.set_index("ts_code").loc[inun, "总市值"], errors="coerce").median()), 0)}
    print(f"[sector] {sname}: 概念{len(codes)} 样本内{len(inun)}")

# ---------------- 2. 标准化（1%/99%截尾 + z-score） ----------------
def zscore(s):
    s = pd.to_numeric(s, errors="coerce").astype(float)
    lo, hi = s.quantile(0.01), s.quantile(0.99)
    s = s.clip(lo, hi)
    return (s - s.mean()) / s.std()

Zadv = {f: zscore(xs[f]) * float(dir_map[f]) for f in FACTORS}
Zraw = {f: zscore(xs[f]) for f in FACTORS}

def sec_stats(zs, codes, disp=False):
    idx = xs.index[xs["ts_code"].isin(codes)]
    v = zs.loc[idx].dropna()
    if not len(v):
        return (np.nan, np.nan, 0)
    return (float(v.mean()), float(v.std()), int(len(v)))

names = list(MAIN)
rows_adv, rows_raw, disp_rows, meta_rows = [], [], [], []
for f in FACTORS:
    rec = {"factor": f, "group": grp_map[f], "direction": int(dir_map[f])}
    for s in list(MAIN) + list(ALT):
        m, sd, n = sec_stats(Zadv[f], sec_codes[s], True)
        rec[s] = round(m, 3)
        disp_rows.append({"factor": f, "sector": s, "within_std": round(sd, 3), "n": n})
        if s in MAIN:
            mr, _, _ = sec_stats(Zraw[f], sec_codes[s])
            rows_raw.append({"factor": f, "sector": s, "raw_exposure": round(mr, 3),
                             "adv_exposure": round(m, 3), "system_direction": int(dir_map[f]),
                             "within_std": round(sd, 3), "n_used": n})
    vals = [rec[s] for s in MAIN]
    rec["cross_range"] = round(max(vals) - min(vals), 3)
    meta_rows.append({"factor": f, "group": rec["group"], "direction": rec["direction"],
                      "cross_range": rec["cross_range"]})
    rows_adv.append(rec)

mat = pd.DataFrame(rows_adv)
mat.to_csv(os.path.join(OUT, "sector_exposure_matrix.csv"), index=False)
pd.DataFrame(rows_raw).to_csv(os.path.join(OUT, "sector_exposure_raw.csv"), index=False)
pd.DataFrame(disp_rows).to_csv(os.path.join(OUT, "sector_exposure_dispersion.csv"), index=False)
print(mat.to_string(index=False))

# 各板块平均"内离散度"（26 因子 within_std 均值）
disp = pd.DataFrame(disp_rows)
print("\n[板块内离散度均值] (越大=成分越杂、画像越不集中)")
for s in disp["sector"].unique():
    print(f"  {s}: within_std均值={disp[disp.sector==s].within_std.mean():.3f}")

# ---------------- 3. 区分度 ----------------
E = mat[names].values
print("\n[区分度] 两两欧氏距离 / 相关系数")
dist_rows = []
for i in range(len(names)):
    for j in range(i + 1, len(names)):
        d = float(np.linalg.norm(E[:, i] - E[:, j]))
        c = float(np.corrcoef(E[:, i], E[:, j])[0, 1])
        dist_rows.append({"sector_i": names[i], "sector_j": names[j],
                          "euclid": round(d, 3), "corr": round(c, 3)})
        print(f"  {names[i]} vs {names[j]}: 距离={d:.3f} 相关={c:.3f}")
dd = pd.DataFrame(dist_rows)
dd.to_csv(os.path.join(OUT, "sector_distinctness.csv"), index=False)
print(f"[汇总] 最小欧氏距离={dd.euclid.min():.3f}({dd.loc[dd.euclid.idxmin(),'sector_i']} vs "
      f"{dd.loc[dd.euclid.idxmin(),'sector_j']}) 最大相关={dd['corr'].max():.3f} "
      f"| 单因子极差均值={mat.cross_range.mean():.3f} 最大={mat.cross_range.max():.3f}")

topbot = {}
print("\n[Top/Bottom 因子]")
for s in names:
    sub = mat[["factor", s]].sort_values(s, ascending=False)
    topbot[s] = {"top": [[r[0], round(r[1], 3)] for r in sub.head(5).values],
                 "bot": [[r[0], round(r[1], 3)] for r in sub.tail(5).values]}
    print(f"  {s}\n    优势: {[(r[0], round(r[1],2)) for r in sub.head(5).values]}\n"
          f"    劣势: {[(r[0], round(r[1],2)) for r in sub.tail(5).values]}")

# ---------------- 4. 风格冲击（原始 + 去均值相对） ----------------
sas = pd.read_csv(os.path.join(RAW, "style_angle_state.csv"))
wks = sorted(sas["week"].astype(int).unique())
w1, w0 = wks[-1], wks[-2]
dRS = (sas[sas["week"].astype(int) == w1].set_index("factor")["rs"]
       - sas[sas["week"].astype(int) == w0].set_index("factor")["rs"])
mI = mat.set_index("factor")
common = [f for f in FACTORS if f in dRS.index]
# ΔRS 稳健化：按 26 因子截面排序归一化为 z（±2 截断），避免个别周 rs 标准差极小导致爆值
_d = pd.to_numeric(dRS.loc[common], errors="coerce").dropna()
dz = pd.Series(norm.ppf(_d.rank(pct=True).values), index=_d.index).clip(-2, 2)
# 去均值暴露（每因子减去 4 板块均值）→ 纯相对口径
Erel = (mI[names].sub(mI[names].mean(axis=1), axis=0))
imp, imp_rel = {}, {}
for s in names:
    imp[s] = float((mI.loc[common, s] * dz.loc[common]).sum())
    imp_rel[s] = float((Erel.loc[common, s] * dz.loc[common]).sum())
print(f"\n[风格冲击示范] {w0}→{w1}  (全口径SI / 去均值相对SI，ΔRS 已稳健化)")
for s in names:
    print(f"  {s}: 全口径={imp[s]:+.2f}  相对={imp_rel[s]:+.2f}")
pd.DataFrame([{"sector": s, "style_impulse": round(imp[s], 3),
               "style_impulse_rel": round(imp_rel[s], 3)} for s in names]
             ).to_csv(os.path.join(OUT, "sector_style_impulse.csv"), index=False)
det = []
for f in common:
    det.append({"factor": f, "dRS": round(float(dRS.loc[f]), 3), "dRSz": round(float(dz.loc[f]), 3),
                **{f"E_{s}": round(float(mI.loc[f, s]), 3) for s in names},
                **{f"C_{s}": round(float(Erel.loc[f, s] * dz.loc[f]), 3) for s in names}})
pd.DataFrame(det).to_csv(os.path.join(OUT, "sector_style_impulse_detail.csv"), index=False)

# ---------------- 5. 热力图 ----------------
order = mat.copy()
order["absmax"] = order[names].abs().max(axis=1)
order = order.sort_values(["group", "absmax"], ascending=[True, False]).reset_index(drop=True)
labels = [f"{r.factor}  {'↑' if r.direction>0 else '↓'}" for r in order.itertuples()]
V = order[names].values
fig, ax = plt.subplots(figsize=(9.6, 11.4), dpi=150)
im = ax.imshow(V, cmap="RdBu_r", vmin=-2.5, vmax=2.5, aspect="auto")
ax.set_xticks(range(len(names)))
ax.set_xticklabels(names, fontproperties=fp, fontsize=11)
ax.set_yticks(range(len(labels)))
ax.set_yticklabels(labels, fontproperties=fp, fontsize=9)
for i in range(V.shape[0]):
    for j in range(V.shape[1]):
        v = V[i, j]
        ax.text(j, i, ("-" if np.isnan(v) else f"{v:+.2f}"), ha="center", va="center",
                fontsize=7.8, color=("white" if (not np.isnan(v) and abs(v) > 1.4) else "#222222"))
ax.set_title("四板块 × 26因子 暴露画像（优势方向标准化暴露 | 20261023 截面）",
             fontproperties=fp, fontsize=13, pad=12)
cb = fig.colorbar(im, ax=ax, fraction=0.035, pad=0.02)
cb.set_label("标准化暴露值（z）", fontproperties=fp, fontsize=9)
ax.set_xlabel("板块", fontproperties=fp, fontsize=10)
fig.tight_layout()
fig.savefig(os.path.join(OUT, "sector_exposure_heatmap.png"))
print("\n[saved] sector_exposure_heatmap.png")

json.dump({"week_end": int(last_week), "week_pair": [int(w0), int(w1)],
           "sector_meta": sec_meta, "distinctness": dist_rows,
           "impulse": {k: round(v, 3) for k, v in imp.items()},
           "impulse_rel": {k: round(v, 3) for k, v in imp_rel.items()},
           "topbot": topbot,
           "within_std_mean": {s: round(float(disp[disp.sector == s].within_std.mean()), 3)
                               for s in disp["sector"].unique()},
           "cross_range": {"mean": round(float(mat.cross_range.mean()), 3),
                           "max": round(float(mat.cross_range.max()), 3)},
           "min_pair_dist": round(float(dd.euclid.min()), 3),
           "max_pair_corr": round(float(dd["corr"].max()), 3)},
          open(os.path.join(OUT, "sector_exposure_summary.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
print("[saved] sector_exposure_summary.json")