# -*- coding: utf-8 -*-
"""板块/概念风格冲击监控器 —— 把「因子旋转」翻译成「谁受益/谁受损」

Part A（原有，保持行为不变）：四个权益板块 A/B/C/D 的相对风格冲击
    Erel[s,f] = E[s,f] - mean_s(E[s,f])                  （四板块内部去均值）
    SIrel[s,t] = Σ_f Erel[s,f] * ΔRS_z(f,t)

Part B（新增）：机器人链(R1-R4)与特斯拉线(T1-T3)七个主题概念
    为什么必须单独处理：这七个概念在 26 因子上的最大绝对暴露只有 0.48，
    近乎零暴露 —— 因子旋转冲击 SIrel 对它们几乎无用。所以：
      · si_mkt  = Σ_f E[s,f] * ΔRS_z   （相对全市场平均，0=与市场同步）
      · si_rel  = Σ_f Erel[s,f] * ΔRS_z（七个概念内部去均值，谁相对受益）
    并把概念层热度分（theme_monitor.csv）与冲击值并列输出：
      热度分高 + si_* 显著 → 主题有因子层面的支撑
      热度分高 + si_* ≈ 0  → 纯事件/情绪驱动，因子体系看不到（机器人现状）

用法（每周因子旋转周报完成后跑一次）：
    export PYTHONPATH=<workspace>/pylibs
    python3 raw/style_sector_monitor.py

输出：
    output/sector_style_monitor.csv（A/B/C/D 各周 SIrel，原有）
    output/sector_exposure_matrix_local.csv（原有）
    output/theme_style_monitor.csv（7 概念各周 si_rel，新增）
    output/theme_style_monitor_mkt.csv（7 概念各周 si_mkt，新增）
    output/theme_style_impulse.csv（最新周：冲击 + 暴露 + 热度合表，新增）
    output/theme_exposure_matrix.csv（7 概念 × 26 因子暴露矩阵，新增）
"""
import os, sys
import numpy as np
import pandas as pd
from scipy.stats import norm


def robust_drs(dRS, clip=2.0):
    """ΔRS 的稳健化：按 26 因子的截面排序归一化为 z（±clip 截断）。
    避免个别周 rs 标准差极小导致 ΔRS 出现 ±100 级爆值。"""
    d = pd.to_numeric(dRS, errors="coerce").dropna()
    z = pd.Series(norm.ppf(d.rank(pct=True).values), index=d.index)
    return z.clip(-clip, clip)


RAW = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.abspath(os.path.join(RAW, "..", "output"))
os.makedirs(OUT, exist_ok=True)

MAIN = {
    "A_AI光模块CPO": ["ths_member_A_CPO.csv"],
    "B_机器人具身": ["ths_member_B_ROBOT.csv"],
    "C_存储芯片": ["ths_member_C_STOR.csv"],
    "D_创新药CXO": ["ths_member_D_INNO.csv", "ths_member_D_CRO.csv"],
}
THEMES = {
    "R1_人形机器人": ["ths_member_R1_人形机器人.csv"],
    "R2_机器人概念": ["ths_member_R2_机器人概念.csv"],
    "R3_减速器": ["ths_member_R3_减速器.csv"],
    "R4_机器视觉": ["ths_member_R4_机器视觉.csv"],
    "T1_特斯拉概念": ["ths_member_T1_特斯拉概念.csv"],
    "T2_一体化压铸": ["ths_member_T2_一体化压铸.csv"],
    "T3_无人驾驶": ["ths_member_T3_无人驾驶.csv"],
}
SIGNAL = 1.0  # |SIrel| >= 1.0 视为"明显风格冲击"

xs = pd.read_csv(os.path.join(RAW, "latest_cross_section.csv"), dtype={"ts_code": str})
fw = pd.read_csv(os.path.join(RAW, "factor_weekly_raw.csv"))
last_week = fw["week_end"].max()
fdef = (fw[fw["week_end"] == last_week].drop_duplicates("factor")[["factor", "group", "direction"]])
FACTORS = list(fdef["factor"])
dir_map = dict(zip(fdef["factor"], fdef["direction"]))
grp_map = dict(zip(fdef["factor"], fdef["group"]))


def zscore(s):
    s = pd.to_numeric(s, errors="coerce").astype(float)
    lo, hi = s.quantile(0.01), s.quantile(0.99)
    s = s.clip(lo, hi)
    return (s - s.mean()) / s.std()


Zadv = {f: zscore(xs[f]) * float(dir_map[f]) for f in FACTORS}
uni = set(xs["ts_code"])


def load_groups(group_map):
    """返回 {名称: 样本内代码列表}"""
    out = {}
    for s, files in group_map.items():
        codes = set()
        for f in files:
            codes |= set(pd.read_csv(os.path.join(RAW, f), dtype={"con_code": str})["con_code"])
        out[s] = sorted(codes & uni)
    return out


def exposure_matrix(groups):
    E = pd.DataFrame(index=FACTORS, columns=list(groups), dtype=float)
    for s, codes in groups.items():
        idx = xs.index[xs["ts_code"].isin(codes)]
        for f in FACTORS:
            v = Zadv[f].loc[idx].dropna()
            E.loc[f, s] = float(v.mean()) if len(v) else np.nan
    return E


# RRG 各周的相对强度 rs
sas = pd.read_csv(os.path.join(RAW, "style_angle_state.csv"))
piv = sas.pivot(index="factor", columns="week", values="rs").sort_index(axis=1)
weeks = [w for w in piv.columns if str(w).isdigit()]


def weekly_impulse(Eref):
    """按 RRG 相邻两周算 SIrel 序列；Eref 为 26×M 暴露矩阵。"""
    rows = []
    for k in range(1, len(weeks)):
        w0, w1 = int(weeks[k - 1]), int(weeks[k])
        dz = robust_drs(piv[w1] - piv[w0])
        common = [f for f in Eref.index if f in dz.index]
        rows.append({"week": w1, "from": w0,
                     **{s: round(float((Eref.loc[common, s] * dz.loc[common]).sum()), 3)
                        for s in Eref.columns}})
    return pd.DataFrame(rows)


# ============ Part A：四个权益板块（保持原有口径与输出） ============
main_groups = load_groups(MAIN)
E = exposure_matrix(main_groups)
Erel = E.sub(E.mean(axis=1), axis=0)          # 去均值 → 相对暴露
E.to_csv(os.path.join(OUT, "sector_exposure_matrix_local.csv"))
mon = weekly_impulse(Erel)
mon.to_csv(os.path.join(OUT, "sector_style_monitor.csv"), index=False)

print("=== [Part A] 板块相对风格冲击 SIrel（正=风格受益 / 负=风格受损） ===")
print(mon.to_string(index=False))
latest = mon.iloc[-1]
print(f"\n=== 最新周 {int(latest['from'])}→{int(latest['week'])} ===")
for s, v in latest[list(MAIN)].astype(float).sort_values(ascending=False).items():
    tag = "★明显受益" if v >= SIGNAL else ("⚠明显受损" if v <= -SIGNAL else "·中性")
    print(f"  {s}: {v:+.2f}  {tag}")
dz = robust_drs(piv[int(latest['week'])] - piv[int(latest['from'])])
print("\n--- 贡献分解：ΔRS 转强（稳健 z）前 5 因子 ---")
for f in dz.sort_values(ascending=False).head(5).index:
    print(f"  {f}({grp_map.get(f,'')}): ΔRSz={dz[f]:+.2f} | "
          + " ".join(f"{s.split('_')[0]}:{Erel.loc[f,s]*dz[f]:+.2f}" for s in MAIN))

# ============ Part B：机器人链 + 特斯拉线七个概念 ============
theme_groups = load_groups(THEMES)
Et = exposure_matrix(theme_groups)
Erel_t = Et.sub(Et.mean(axis=1), axis=0)      # 七概念内部去均值
mon_t = weekly_impulse(Erel_t)                # si_rel
mon_m = weekly_impulse(Et)                    # si_mkt（相对全市场，0=与市场同步）
mon_t.to_csv(os.path.join(OUT, "theme_style_monitor.csv"), index=False)
mon_m.round(3).to_csv(os.path.join(OUT, "theme_style_monitor_mkt.csv"), index=False)
Et.round(3).to_csv(os.path.join(OUT, "theme_exposure_matrix.csv"))

lastw, lastf = int(latest['week']), int(latest['from'])
dzl = robust_drs(piv[lastw] - piv[lastf])
common = [f for f in FACTORS if f in dzl.index]
contrib = {s: (Erel_t.loc[common, s] * dzl.loc[common]).sort_values(key=np.abs, ascending=False)
           for s in THEMES}

heat = pd.read_csv(os.path.join(RAW, "theme_monitor.csv"))
heat = heat.set_index("key")

rows = []
for s in THEMES:
    top3 = ", ".join(f"{f}({grp_map.get(f,'')}){v:+.2f}" for f, v in contrib[s].head(3).items())
    rows.append({
        "key": s, "概念": s.split("_", 1)[-1],
        "概念成分": int(heat.loc[s, "概念成分"]), "样本内": int(heat.loc[s, "样本内"]),
        "热度分": float(heat.loc[s, "热度分"]), "判定": heat.loc[s, "判定"],
        "hot最新排名": int(heat.loc[s, "hot_最新排名"]),
        "maxabs_26因子暴露": round(float(Et[s].abs().max()), 3),
        "si_rel": round(float(mon_t.iloc[-1][s]), 3),
        "si_mkt": round(float(mon_m.iloc[-1][s]), 3),
        "贡献Top3（差异于七概念均值）": top3,
    })
imp = pd.DataFrame(rows).sort_values("热度分", ascending=False)
imp.to_csv(os.path.join(OUT, "theme_style_impulse.csv"), index=False)

pd.set_option("display.width", 320)
print("\n\n=== [Part B] 机器人链 + 特斯拉线：热度 × 因子冲击 ===")
print(f"最新周 {lastf}→{lastw}")
print(imp[["key", "概念成分", "样本内", "hot最新排名", "maxabs_26因子暴露",
           "si_rel", "si_mkt", "热度分", "判定"]].to_string(index=False))
print("\n--- 各概念相对七概念均值，ΔRSz 贡献绝对值 Top3 ---")
for _, r in imp.iterrows():
    print(f"  {r['key']}: {r['贡献Top3（差异于七概念均值）']}")
print("\n判读：si_rel≈0 且 热度分高 → 该概念的热度来自事件/情绪，26 因子体系无法解释，"
      "必须靠概念层指标（热榜注意力/相对强弱/量能拥挤/涨停情绪）监控；"
      "maxabs_26因子暴露 越小，说明用因子暴露去监控它越无效。")
print("向量 [ΔRS] 是『因子层的旋转』，矩阵 Erel 是『板块对这些因子的相对暴露』，"
      "两者相乘即『旋转的受益方』。同一因子连续 2 周同向 = 趋势确认。")