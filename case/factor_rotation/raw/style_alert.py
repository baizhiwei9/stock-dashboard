# -*- coding: utf-8 -*-
"""风格因子旋转两级预警（风格变化 / 风格变化+暴跌预警）

口径（与 raw/rrg_plot.py、raw/replay_style_signals.py 一致）：
- 因子周收益: factor_weekly_raw.csv weekly_long_short_return
- 逐周 RRG: 每个 week_end 取最近 min(8, 可用) 周窗口，
  rs_raw=窗口累计多空收益, rs=100+(rs_raw-mean)/std*12；
  mom_raw=近3周累计-更早3周累计(窗口≤8时), mom=(mom_raw-mean)/std*12
- 象限: lead(rs>100,mom>0) / impr(rs<100,mom>0) / weak(rs>100,mom<0) / lag(rs<100,mom<0)
- 角度: atan2(mom, rs-100) 转度数（0°=右, 逆时针为正；lead≈0~90°, impr≈90~180°,
  lag≈-180~-90°, weak≈-90~0°）

两项核心信号（均用比例，跨因子池大小可比）：
- rot_rate（旋转强度）: 本周相对上周发生象限迁移的因子占"两周均存在因子"比
- rs_wide_rate（弱势广度）: 相对强度 rs<100 的因子占可用因子比

两级触发（按用户确认口径）：
- LEVEL 1「风格变化」: rot_rate >= 0.40
- LEVEL 2「风格变化+暴跌预警」: rot_rate >= 0.40 且 rs_wide_rate >= 0.55

输出：
- style_state.csv: 每周汇总状态（周、因子数、迁移数、rot_rate、rs_wide_rate、触发级别）
- style_angle_state.csv: 每周每因子象限/角度/rs/mom 长表（供"上周→本周"对照）
- 打印最新期对照与触发结论

运行: python3 style_alert.py
"""
import os
import math
import numpy as np
import pandas as pd

RAW = os.path.dirname(os.path.abspath(__file__))
FACTOR_CSV = os.path.join(RAW, "factor_weekly_raw.csv")
STATE_CSV = os.path.join(RAW, "style_state.csv")
ANGLE_CSV = os.path.join(RAW, "style_angle_state.csv")

ROT_TH = 0.40       # 旋转强度阈值（≥40% 因子迁移 = 风格变化）
WIDE_TH = 0.55      # 弱势广度阈值（≥55% 因子 rs<100 = 市场风格偏弱）

QUAD_CN = {"lead": "领先 Lead", "impr": "改善 Improving", "weak": "转弱 Weakening", "lag": "落后 Lagging"}


def quadrant(rs, mom):
    if rs > 100 and mom > 0:
        return "lead"
    if rs < 100 and mom > 0:
        return "impr"
    if rs > 100 and mom < 0:
        return "weak"
    return "lag"


def angle_deg(rs, mom):
    """RRG 方位角：0°=右，逆时针为正，范围 (-180, 180]；rs-100 为 x、mom 为 y"""
    return math.degrees(math.atan2(mom, rs - 100))


def alert_text(level, we, rot_rate, rs_wide_rate):
    if level == 0:
        return "未触发（无预警）"
    lines = []
    if level >= 1:
        lines.append(
            f"【风格变化提醒】截至 {we}：本周 {rot_rate * 100:.0f}% 的因子发生象限迁移（阈值 40%），"
            f"因子旋转加速，风格结构正在变化，请关注风格切换风险。"
        )
    if level >= 2:
        lines.append(
            f"【暴跌预警 · 风格变化叠加】截至 {we}：旋转强度 {rot_rate * 100:.0f}%≥40% 且 "
            f"弱势广度 {rs_wide_rate * 100:.0f}%≥55%，因子大面积转弱叠加剧烈旋转，"
            f"市场风格恶化概率上升，警惕短期下跌（参考历史：此类组合信号平均提前约 2 周出现）。"
        )
    return "\n".join(lines)


def alert_subject(level, we, rot_rate, rs_wide_rate):
    """两级预警标题（用户要求两档标题不同）"""
    if level <= 0:
        return None
    rot_s = f"{rot_rate * 100:.0f}%" if pd.notna(rot_rate) else "--"
    wide_s = f"{rs_wide_rate * 100:.0f}%" if pd.notna(rs_wide_rate) else "--"
    if level >= 2:
        return f"【暴跌预警·风格变化】{we} 旋转{rot_s}/弱势{wide_s}"
    return f"【风格变化提醒】{we} 旋转{rot_s}"


def build_state():
    """计算全历史逐周状态与角度明细，落盘并返回 (st, ang)。"""
    F = pd.read_csv(FACTOR_CSV, dtype={"week_end": np.int64})
    WEEKS = sorted(F["week_end"].unique())
    piv = F.pivot(index="factor", columns="week_end", values="weekly_long_short_return").sort_index(axis=1)
    piv = piv[WEEKS]

    prev_quad = None
    prev_angle = None
    state_rows = []
    angle_rows = []
    for i, we in enumerate(WEEKS):
        window = WEEKS[max(0, i - 7): i + 1]
        recent = piv[window]
        valid = recent.dropna(how="all").index.tolist()
        recent = recent.loc[valid]
        if recent.shape[1] < 4 or len(valid) < 3:
            prev_quad, prev_angle = None, None
            state_rows.append({"week": int(we), "n_factor": len(valid), "n_migrate": np.nan,
                               "rot_rate": np.nan, "rs_wide_rate": np.nan, "level": 0})
            continue
        rs_raw = recent.sum(axis=1)
        rs = 100 + (rs_raw - rs_raw.mean()) / (rs_raw.std() + 1e-12) * 12
        k = min(3, recent.shape[1])
        mom_raw = (recent.iloc[:, -k:].sum(axis=1) - recent.iloc[:, :-k].sum(axis=1)
                   if recent.shape[1] > k else recent.iloc[:, -1])
        mom = (mom_raw - mom_raw.mean()) / (mom_raw.std() + 1e-12) * 12

        qs = {f: quadrant(rs[f], mom[f]) for f in valid}
        ang = {f: angle_deg(rs[f], mom[f]) for f in valid}
        n_migrate = np.nan
        if prev_quad is not None:
            common = [f for f in valid if f in prev_quad]
            if len(common) >= 3:
                n_migrate = sum(1 for f in common if qs[f] != prev_quad[f])
        rot_rate = np.nan
        if prev_quad is not None and pd.notna(n_migrate):
            rot_rate = n_migrate / len(common)
        rs_wide_rate = float((rs < 100).mean())
        level = 0
        if pd.notna(rot_rate):
            if rot_rate >= ROT_TH and rs_wide_rate >= WIDE_TH:
                level = 2
            elif rot_rate >= ROT_TH:
                level = 1

        state_rows.append({"week": int(we), "n_factor": len(valid), "n_migrate": n_migrate,
                           "rot_rate": rot_rate, "rs_wide_rate": rs_wide_rate, "level": level})
        for f in valid:
            angle_rows.append({
                "week": int(we), "factor": f,
                "rs": float(rs[f]), "mom": float(mom[f]),
                "quadrant": qs[f], "angle": ang[f],
            })
        prev_quad = qs
        prev_angle = ang

    st = pd.DataFrame(state_rows)
    st.to_csv(STATE_CSV, index=False)
    ang = pd.DataFrame(angle_rows)
    ang.to_csv(ANGLE_CSV, index=False)
    return st, ang


def comparison_table(ang, prev_week, we):
    """生成 上周→本周 各因子象限/角度 对照的 markdown 表文本。"""
    a_now = ang[ang["week"] == we].set_index("factor")
    a_prev = ang[ang["week"] == int(prev_week)].set_index("factor")
    common = [f for f in a_now.index if f in a_prev.index]
    common.sort(key=lambda x: (a_now.loc[x, "quadrant"], a_now.loc[x, "angle"]))
    lines = ["因子|上周象限|上周角°|本周象限|本周角°|迁移",
             "---|---|---|---|---|---"]
    for f in common:
        p_q, p_a = QUAD_CN[a_prev.loc[f, "quadrant"]], a_prev.loc[f, "angle"]
        n_q, n_a = QUAD_CN[a_now.loc[f, "quadrant"]], a_now.loc[f, "angle"]
        m = "是" if a_prev.loc[f, "quadrant"] != a_now.loc[f, "quadrant"] else "否"
        lines.append(f"{f}|{p_q}|{p_a:.1f}|{n_q}|{n_a:.1f}|{m}")
    return "\n".join(lines), common


def main():
    st, ang = build_state()

    # ---------- 最新期展示 ----------
    latest = st.iloc[-1]
    we = int(latest["week"])
    lvl = int(latest["level"])
    rot = latest["rot_rate"]
    wide = latest["rs_wide_rate"]

    print("=" * 78)
    print(f"风格因子旋转预警 · 最新截面 {we}")
    print("=" * 78)
    print(f"可用因子数      : {int(latest['n_factor'])}")
    print(f"旋转强度rot_rate: {rot * 100:.1f}%  (阈值 40%)" if pd.notna(rot) else "旋转强度rot_rate: --")
    print(f"弱势广度rs_wide : {wide * 100:.1f}%  (暴跌阈值 55%)" if pd.notna(wide) else "弱势广度rs_wide : --")
    print(f"触发级别        : LEVEL {lvl}（0=无 1=风格变化 2=风格变化+暴跌预警）")
    print("-" * 78)
    print(alert_text(lvl, we, rot if pd.notna(rot) else 0.0, wide if pd.notna(wide) else 0.0))
    print("-" * 78)

    # 上周 → 本周 角度对照
    prev_week = st.iloc[-2]["week"] if len(st) >= 2 else None
    if prev_week is not None:
        tbl, _ = comparison_table(ang, prev_week, we)
        print(f"\n上周({int(prev_week)}) → 本周({we}) 各因子象限/角度对照：")
        print(tbl)

    # 历史触发回顾（仅打印近 14 周触发摘要）
    print("\n近 14 周信号摘要（* 为触发周）：")
    print(f"{'周':<10} {'n_factor':>7} {'rot_rate':>8} {'rs_wide':>8} {'LEVEL':>5}")
    for _, r in st.tail(14).iterrows():
        star = " *" if int(r["level"]) > 0 else "  "
        rr = f"{r['rot_rate'] * 100:5.1f}%" if pd.notna(r["rot_rate"]) else "   -- "
        ww = f"{r['rs_wide_rate'] * 100:5.1f}%" if pd.notna(r["rs_wide_rate"]) else "   -- "
        print(f"{int(r['week']):<10} {int(r['n_factor']):>7} {rr:>8} {ww:>8} {int(r['level']):>5}{star}")

    print(f"\n状态文件已保存: {STATE_CSV}")
    print(f"角度明细已保存: {ANGLE_CSV}")


if __name__ == "__main__":
    main()