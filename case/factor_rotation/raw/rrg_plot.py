# -*- coding: utf-8 -*-
"""RRG 因子旋转图绘制
输入：raw/factor_weekly_raw.csv（周度多空收益）
输出：output/rrg_rotation[<后缀>].png（默认 rrg_rotation.png，可命令行传入后缀）
方法：
- score_t = 因子周度多空收益（累计/平滑）作为相对强度 RS
- mom = RS 相对近期变化作为相对动量
- 归一化到均值100的RS（RRG惯例）与以0为界的动量
四象限：Leading(RS>100,Mom>0) / Improving(RS<100,Mom>0) /
        Weakening(RS>100,Mom<0) / Lagging(RS<100,Mom<0)
旋转要素（本次新增）：
- 旋转轴心：中心 (RS=100, MOM=0)；四象限绕轴心顺时针轮动
  Leading 主线 → Weakening 退潮 → Lagging 回避 → Improving 改善 → Leading
- 旋转轨迹：每个因子“上一周位置（空心圆）→ 本周位置（箭头指向）”，
  发生象限迁移的因子用加粗箭头突出，回答“从哪旋到哪”
"""
import os, sys, json
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

RAW = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output")
os.makedirs(OUT, exist_ok=True)
FONT = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/skills/user/optimal-transport-ot/SimHei.ttf"
font_manager.fontManager.addfont(FONT)
_font = font_manager.FontProperties(fname=FONT).get_name()
plt.rcParams["font.family"] = _font
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["font.size"] = 9
SUFFIX = sys.argv[1] if len(sys.argv) > 1 else ""   # 例如传入 "-旋转轴"


def calc_rr(win):
    """对给定周窗口计算 (rs, mom)，口径与风格预警 build_state 完全一致。"""
    rs_raw = win.sum(axis=1)
    rs = 100 + (rs_raw - rs_raw.mean()) / (rs_raw.std() + 1e-12) * 12
    k = min(3, win.shape[1])
    if win.shape[1] > k:
        mom_raw = win.iloc[:, -k:].sum(axis=1) - win.iloc[:, :-k].sum(axis=1)
    else:
        mom_raw = win.iloc[:, -1]
    mom = (mom_raw - mom_raw.mean()) / (mom_raw.std() + 1e-12) * 12
    return rs, mom


def quadrant(rs, mom):
    if rs > 100 and mom > 0:
        return "lead"
    if rs < 100 and mom > 0:
        return "impr"
    if rs > 100 and mom < 0:
        return "weak"
    return "lag"


def main():
    df = pd.read_csv(os.path.join(RAW, "factor_weekly_raw.csv"))
    pivot = df.pivot(index="factor", columns="week_end", values="weekly_long_short_return")
    pivot = pivot.sort_index(axis=1)
    weeks = pivot.columns.tolist()
    # 用最近 N 周（至少4周）做RRG
    n = min(8, len(weeks))
    recent = pivot[weeks[-n:]]
    # 相对强度：因子周收益的多周累计（平滑），再做横截面归一化 RRG 风格 (100 均值)
    rs_raw = recent.sum(axis=1)  # 累计多空收益
    rs = 100 + (rs_raw - rs_raw.mean()) / (rs_raw.std() + 1e-12) * 12  # 尺度伸缩便于展示
    # 相对动量：最近3周累计 - 更早3周累计（趋势加速度）
    k = min(3, len(weeks))
    mom_raw = recent.iloc[:, -k:].sum(axis=1) - recent.iloc[:, :-k].sum(axis=1) if recent.shape[1] > k else recent.iloc[:, -1]
    mom = (mom_raw - mom_raw.mean()) / (mom_raw.std() + 1e-12) * 12

    # 可直接读取的存储
    pd.DataFrame({"factor": rs.index, "rs": rs.values, "mom": mom.values}).to_csv(
        os.path.join(RAW, "rrg_coords.csv"), index=False)

    # —— 上一周（同口径、同长度窗口）坐标，用于画本周的旋转轨迹 ——
    prev_we = weeks[-2] if len(weeks) >= n + 1 else None
    rs_p = mom_p = None
    if prev_we is not None:
        win_prev = pivot.iloc[:, len(weeks) - n - 1: len(weeks) - 1]
        if win_prev.shape[1] >= 4:
            rs_p, mom_p = calc_rr(win_prev)
            rs_p = rs_p.reindex(rs.index)
            mom_p = mom_p.reindex(mom.index)

    fig, ax = plt.subplots(figsize=(12.5, 10.5))
    # 四象限底色
    ax.axvspan(100, 130, ymin=0, ymax=0.5, color="#fdebea", alpha=0.6, zorder=0)
    ax.axvline(100, color="#888888", lw=1.2, zorder=2)
    ax.axhline(0, color="#888888", lw=1.2, zorder=2)
    ax.fill_between([100, 100], 0, 0, alpha=0)

    # 手工画象限色块
    from matplotlib.patches import Rectangle
    ax.add_patch(Rectangle((100, 0), 40, np.max(mom) * 1.35 if np.max(mom) > 0 else 12, facecolor="#e74c3c", alpha=0.10, zorder=0))
    ax.add_patch(Rectangle((100, -40), 40, -np.min(mom) * 1.35 if np.min(mom) < 0 else -12, facecolor="#f5b041", alpha=0.10, zorder=0))
    ax.add_patch(Rectangle((60, 0), -40, 12, facecolor="#2ecc71", alpha=0.10, zorder=0))
    ax.add_patch(Rectangle((60, -40), -40, -12, facecolor="#95a5a6", alpha=0.10, zorder=0))

    colors = {
        "估值": "#c0392b", "反转动量": "#e67e22", "成长": "#27ae60",
        "盈利": "#16a085", "流动": "#2980b9", "波动": "#34495e",
        "治理": "#7f8c8d", "预期": "#8e44ad", "机构": "#d35400",
    }
    # 方向映射（因子 -> 符号）↑=正向(越大越好) ↓=反向(越小越好)
    dir_map = {}
    for f in rs.index:
        sub = df[df["factor"] == f]
        d = sub["direction"].iloc[-1] if len(sub) else 1
        dir_map[f] = "↑" if d == 1 else "↓"
    # 标点（贪心防标签重叠）
    placed = []  # (x0,y0,x1,y1)
    order = sorted(rs.index, key=lambda f: (rs[f], mom[f]))
    dx_unit = 0.62   # 每个字符约占比特宽度（数据坐标估计）
    dy_unit = 1.05   # 单行文本高度

    # —— 旋转轨迹：上一周位置(空心圆) → 本周位置(箭头指向本周) ——
    moved_factors = []
    if rs_p is not None:
        for f in order:
            grp = df[df["factor"] == f]["group"].iloc[0] if len(df[df["factor"] == f]) else "其他"
            c = colors.get(grp, "#555555")
            x0, y0 = float(rs_p[f]), float(mom_p[f])
            x1, y1 = float(rs[f]), float(mom[f])
            d = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
            # 上周位置：空心圆
            ax.scatter(x0, y0, s=62, facecolors="none", edgecolors=c,
                       linewidth=1.0, alpha=0.55, zorder=3.6)
            if d < 1.5:
                continue
            moved = quadrant(x1, y1) != quadrant(x0, y0)
            if moved:
                moved_factors.append(f)
            # 箭头由上周指向本周，箭头方向即该因子本周的旋转方向
            ax.annotate("", xy=(x1, y1), xytext=(x0, y0),
                        arrowprops=dict(arrowstyle="-|>" if moved else "->",
                                        color=c,
                                        lw=2.2 if moved else 1.0,
                                        alpha=0.95 if moved else 0.45,
                                        shrinkA=2, shrinkB=6,
                                        mutation_scale=16 if moved else 10),
                        zorder=4.0 if moved else 3.8)

    # 本周位置：实心圆
    for f in order:
        grp = df[df["factor"] == f]["group"].iloc[0] if len(df[df["factor"] == f]) else "其他"
        c = colors.get(grp, "#555555")
        ax.scatter(rs[f], mom[f], s=190, c=c, alpha=0.85, edgecolors="white", linewidth=1.5, zorder=5)
    for f in order:
        x, y = rs[f], mom[f]
        label = f"{f}{dir_map[f]}"
        tw = len(label) * dx_unit
        th = dy_unit
        # 候选偏移（数据坐标，中心右/上/下/左/左斜等）
        cands = [
            (4 + tw / 2, 2 + th / 2), (-4 - tw / 2, 2 + th / 2),
            (4 + tw / 2, -2 - th / 2), (-4 - tw / 2, -2 - th / 2),
            (0, 3 + th / 2), (0, -3 - th / 2), (4 + tw / 2, 0), (-4 - tw / 2, 0),
        ]
        chosen = None
        best = None
        for ox, oy in cands:
            bx0, by0 = x + ox - tw / 2, y + oy - th / 2
            bx1, by1 = x + ox + tw / 2, y + oy + th / 2
            overlap = any(not (bx1 < px0 - 0.4 or bx0 > px1 + 0.4 or by1 < py0 - 0.4 or by0 > py1 + 0.4)
                          for (px0, py0, px1, py1) in placed)
            dist = (ox ** 2 + oy ** 2) ** 0.5
            if not overlap and (best is None or dist < best):
                best = dist
                chosen = (bx0, by0, bx1, by1, ox, oy)
        if chosen is None:
            chosen = (x + 4 - tw / 2, y + 2 - th / 2, x + 4 + tw / 2, y + 2 + th / 2, 4, 2)
        placed.append((chosen[0], chosen[1], chosen[2], chosen[3]))
        ax.annotate(label, (x, y), xytext=(chosen[4], chosen[5]), textcoords="offset points",
                    fontsize=8.5, zorder=6, color="#222222")

    # 象限标签
    ax.text(102, np.max(mom) * 0.95, "Leading  主线", fontsize=13, fontweight="bold", color="#c0392b", zorder=6)
    ax.text(62, np.max(mom) * 0.95, "Improving  改善中", fontsize=13, fontweight="bold", color="#27ae60", zorder=6)
    ax.text(102, np.min(mom) * 0.9, "Weakening  退潮", fontsize=13, fontweight="bold", color="#f39c12", zorder=6)
    ax.text(62, np.min(mom) * 0.9, "Lagging  回避", fontsize=13, fontweight="bold", color="#95a5a6", zorder=6)

    # —— 旋转轴心 + 整体旋转方向（绕轴心顺时针：Lead→Weak→Lag→Impr） ——
    ax.plot(100, 0, marker="*", ms=21, mfc="#111111", mec="white", mew=1.2, zorder=9)
    ax.annotate("旋转轴心 (RS=100, MOM=0)", (100, 0), xytext=(9, -17),
                textcoords="offset points", fontsize=8.5, color="#111111", zorder=9)
    th = np.linspace(np.deg2rad(118), np.deg2rad(28), 80)   # 由左上经正上到右上 = 顺时针
    r_x, r_y = 0.10, 0.080
    ax.plot(0.5 + r_x * np.cos(th), 0.5 + r_y * np.sin(th), transform=ax.transAxes,
            color="#d62728", lw=1.9, alpha=0.85, zorder=8, clip_on=False, solid_capstyle="round")
    ax.annotate("", xy=(0.5 + r_x * np.cos(th[-1]), 0.5 + r_y * np.sin(th[-1])),
                xytext=(0.5 + r_x * np.cos(th[-4]), 0.5 + r_y * np.sin(th[-4])),
                xycoords="axes fraction", textcoords="axes fraction",
                arrowprops=dict(arrowstyle="-|>", color="#d62728", lw=1.9, mutation_scale=15),
                annotation_clip=False, zorder=9)
    ax.text(0.5, 0.5 + r_y + 0.022, "旋转方向（顺时针）：主线 → 退潮 → 回避 → 改善",
            transform=ax.transAxes, ha="center", va="bottom",
            fontsize=9.5, color="#d62728", zorder=9)

    ax.set_xlim(55, 145)
    ylim = max(12, abs(np.min(mom)) * 1.5, abs(np.max(mom)) * 1.4)
    ax.set_ylim(-ylim, ylim)
    ax.set_xlabel("相对强度 RS（因子累计多空收益，横截面归一化）", fontsize=11)
    ax.set_ylabel("相对动量 MOM（近3周 vs 前3周 收益差，加速度）", fontsize=11)
    win_txt = f"｜旋转轨迹 {prev_we} → {weeks[-1]}" if prev_we is not None else ""
    ax.set_title(f"A股因子旋转 RRG（国信33因子框架）\n截至 {weeks[-1]}（最近{n}周截面）{win_txt}", fontsize=14, fontweight="bold")
    ax.grid(alpha=0.25, linestyle="--", zorder=1)

    # 图例（只含面板实际出现的分组，去掉无因子的"治理"等）
    grps = df["group"].drop_duplicates().tolist()
    handles = [plt.Line2D([0], [0], marker="o", color="w", markerfacecolor=colors.get(k, "#555555"), markersize=9, label=k) for k in grps]
    ax.legend(handles=handles, loc="upper left", framealpha=0.9, fontsize=9, ncol=2)

    fig.tight_layout(rect=(0, 0.155, 1, 1))

    # —— 图片下方备注：说明“起点”含义与箭头方向读法 ——
    # 示例箭头小图例：起点(空心) → 终点(实心)
    iax = fig.add_axes([0.045, 0.045, 0.155, 0.085])
    iax.set_xlim(0, 10); iax.set_ylim(0, 10); iax.axis("off")
    iax.scatter(2.0, 5.0, s=110, facecolors="none", edgecolors="#2c7fb8", linewidth=1.6, zorder=3)
    iax.annotate("", xy=(7.6, 5.0), xytext=(2.4, 5.0),
                 arrowprops=dict(arrowstyle="-|>", color="#2c7fb8", lw=2.2, shrinkA=1, shrinkB=4, mutation_scale=14), zorder=2)
    iax.scatter(7.6, 5.0, s=150, c="#2c7fb8", edgecolors="white", linewidth=1.4, zorder=4)
    iax.text(2.0, 1.0, "起点＝上周", ha="center", va="bottom", fontsize=8.5, color="#2c7fb8")
    iax.text(7.6, 1.0, "终点＝本周", ha="center", va="bottom", fontsize=8.5, color="#2c7fb8")
    iax.text(4.8, 7.2, "箭头＝一周位移", ha="center", va="bottom", fontsize=8.5, color="#2c7fb8")

    w0 = prev_we if prev_we is not None else "上一周"
    w1 = weeks[-1]
    note = (
        f"起点（空心圆）是什么：它就是该因子上一周（{w0}）的位置——用截至那一周的最近 8 周窗口、按与本周完全相同的口径算出；\n"
        f"终点（实心圆）就是本周（{w1}）的位置。\n"
        "所以箭头＝该因子这一周在「横轴 RS × 纵轴 MOM」平面上的位移，回答“从哪旋到哪”；箭头不是该因子的动量方向，也不是价格涨跌方向。\n"
        "箭头方向怎么读：长度＝一周位移幅度；角度＝位移方向，向右＝相对强度增强，向左＝相对强度减弱；向上＝动量转强，向下＝动量转弱。\n"
        "斜向＝强度与动量同时变化：右上＝强度与动量双升（趋于「主线」），右下＝强度仍在、动量转弱（趋于「退潮」）；\n"
        "左下＝强度与动量双降（趋于「回避」），左上＝强度仍弱、动量转强（趋于「改善」）。\n"
        f"★＝旋转轴心 (RS=100, MOM=0)；红色圆弧＝整体旋转方向（顺时针：主线 → 退潮 → 回避 → 改善）；加粗箭头＝本周发生象限迁移 {len(moved_factors)}/{len(rs.index)} 个。"
    )
    fig.text(0.225, 0.145, note, ha="left", va="top", fontsize=8.8, color="#333333", linespacing=1.5)

    out_path = os.path.join(OUT, f"rrg_rotation{SUFFIX}.png")
    fig.savefig(out_path, dpi=150)
    print(f"[rrg] saved {out_path}", flush=True)
    print(pd.DataFrame({"factor": rs.index, "rs": rs.round(2), "mom": mom.round(2)}).to_string(index=False), flush=True)


if __name__ == "__main__":
    main()