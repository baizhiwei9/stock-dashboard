# -*- coding: utf-8 -*-
"""近一月（4周）因子周度多空收益排名迁移桑基图
输入：raw/factor_weekly_raw.csv（周度多空收益+方向+分组）
输出：output/factor_rank_sankey.png、raw/sankey_flow.csv
方法：
- 取最近4个周截面，每周按 weekly_long_short_return 排名（1=最强）
- 每列一个周，节点按排名从上到下排列，流线连接同一因子相邻两周的排名位置
- 第一列节点标注因子名+方向（↑正向/↓反向），第2-4列标注该周排名
- 最右列（最新截面）方框右侧另列「因子名」，使最终排名可直接读出，无需顺流线回看
- 节点颜色按因子分组，右侧标注最新排名与4周排名变化（正=排名上升）
"""
import os
import sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Rectangle, FancyBboxPatch
from matplotlib.path import Path
import matplotlib.patches as mpatches

RAW = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output")
os.makedirs(OUT, exist_ok=True)
FONT = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/skills/user/optimal-transport-ot/SimHei.ttf"
font_manager.fontManager.addfont(FONT)
_font = font_manager.FontProperties(fname=FONT).get_name()
plt.rcParams["font.family"] = _font
plt.rcParams["axes.unicode_minus"] = False
SUFFIX = sys.argv[1] if len(sys.argv) > 1 else ""   # 例如传入 "-最右排标名"


def main():
    df = pd.read_csv(os.path.join(RAW, "factor_weekly_raw.csv"))
    weeks = sorted(df["week_end"].unique())
    last4 = weeks[-4:]
    if len(last4) < 4:
        last4 = weeks[-len(last4):]
    sub = df[df["week_end"].isin(last4)][
        ["week_end", "factor", "group", "direction", "weekly_long_short_return"]]

    # 每周排名（1=最强）
    p = sub.pivot_table(index="factor", columns="week_end", values="weekly_long_short_return")
    rank = p.rank(ascending=False, method="min").astype(int)

    # 方向映射
    direc = sub.drop_duplicates("factor").set_index("factor")["direction"].to_dict()
    grp = sub.drop_duplicates("factor").set_index("factor")["group"].to_dict()

    colors = {
        "估值": "#c0392b", "反转动量": "#e67e22", "成长": "#27ae60",
        "盈利": "#16a085", "流动": "#2980b9", "波动": "#34495e",
        "治理": "#7f8c8d", "预期": "#8e44ad", "机构": "#d35400",
    }

    factors = list(rank.index)
    n = len(factors)  # 26
    ncol = len(last4)
    x_cols = np.arange(ncol) * 4.0          # 列中心
    bw = 1.25                                # 节点条宽
    y_top = n + 0.5                          # 顶部起点
    row_h = 1.0                              # 每行高度

    fig, ax = plt.subplots(figsize=(17.5, 11.0))

    # 各列节点的 y 中心（排名1在顶部）
    node_y = {}
    for ci, w in enumerate(last4):
        r = rank[w]
        ys = {}
        for f in factors:
            ys[f] = y_top - (r[f] - 0.5) * row_h
        node_y[w] = ys

    # ---- 流线（贝塞尔曲线）----
    for i in range(ncol - 1):
        wa, wb = last4[i], last4[i + 1]
        x0 = x_cols[i] + bw / 2
        x1 = x_cols[i + 1] - bw / 2
        for f in factors:
            y0 = node_y[wa][f]
            y1 = node_y[wb][f]
            c = colors.get(grp.get(f, "其他"), "#555555")
            # 三次贝塞尔：水平控制点
            verts = [(x0, y0), (x0 + (x1 - x0) * 0.45, y0),
                     (x1 - (x1 - x0) * 0.45, y1), (x1, y1)]
            codes = [Path.MOVETO, Path.CURVE4, Path.CURVE4, Path.CURVE4]
            path = Path(verts, codes)
            patch = mpatches.PathPatch(path, facecolor="none", edgecolor=c,
                                       lw=1.9, alpha=0.62, zorder=2)
            ax.add_patch(patch)

    # ---- 节点条 ----
    for ci, w in enumerate(last4):
        x = x_cols[ci]
        r = rank[w]
        for f in factors:
            y = node_y[w][f]
            c = colors.get(grp.get(f, "其他"), "#555555")
            rect = FancyBboxPatch((x, y - row_h / 2 + 0.03), bw, row_h - 0.06,
                                  boxstyle="round,pad=0.02,rounding_size=0.06",
                                  facecolor=c, edgecolor="white", lw=1.0,
                                  alpha=0.92, zorder=4)
            ax.add_patch(rect)
            if ci == 0:
                dmap = {1: "↑", -1: "↓"}
                label = f"{f}{dmap.get(direc.get(f, 1), '')}  {r[f]}"
                ax.text(x + bw / 2, y, label, ha="center", va="center",
                        fontsize=9.0, color="white", fontweight="bold", zorder=5)
            else:
                ax.text(x + bw / 2, y, str(r[f]), ha="center", va="center",
                        fontsize=10.0, color="white", fontweight="bold", zorder=5)

    # ---- 列标题 ----
    for ci, w in enumerate(last4):
        s = str(w)
        label = f"{s[:4]}-{s[4:6]}-{s[6:]}"
        title = f"W-{ncol - 1 - ci}\n{label}"
        if ci == ncol - 1:
            title += "\n（最新）"
        ax.text(x_cols[ci], y_top + 0.6, title, ha="center", va="bottom",
                fontsize=12.5, fontweight="bold", color="#222222", zorder=6)

    # ---- 最右列：方框右侧列出因子名（最终排名可直接读出）----
    w0, wlast = last4[0], last4[-1]
    r0, rl = rank[w0], rank[wlast]
    ax.set_xlim(-0.6, 16.8)   # 先定坐标范围，后面实测最长因子名宽度用的是同一套变换
    ax.set_ylim(0, y_top + 2.2)
    probe = ax.text(0, 0, max(factors, key=len), fontsize=9.5, fontweight="bold")
    fig.canvas.draw()
    bb = probe.get_window_extent(renderer=fig.canvas.get_renderer())
    inv = ax.transData.inverted()
    name_w = abs(inv.transform((bb.x1, 0))[0] - inv.transform((bb.x0, 0))[0])
    probe.remove()
    x_name = x_cols[-1] + bw / 2 + 0.18 + name_w   # 因子名右对齐，最长名离方框 0.18
    x_stat = x_name + 0.12                          # 最终排名 + 4周变化（左对齐）
    print(f"[sankey] 因子名列 x={x_name:.2f}（最长名宽 {name_w:.2f} 单位）", flush=True)
    for f in factors:
        y = node_y[wlast][f]
        ax.text(x_name, y, f, ha="right", va="center", fontsize=9.5,
                color=colors.get(grp.get(f, "其他"), "#555555"),
                fontweight="bold", zorder=5)
    ax.text(x_name, y_top + 0.6, "因子名", ha="right", va="bottom",
            fontsize=10.5, color="#333333", zorder=6)

    # ---- 最右列：最新排名 + 4周排名变化 ----
    ax.annotate("", xy=(x_stat - 0.1, y_top),
                xytext=(x_stat - 0.1, y_top - n),
                arrowprops=dict(arrowstyle="-", color="#bbbbbb", lw=1.0))
    for f in factors:
        y = node_y[wlast][f]
        chg = rl[f] - r0[f]  # 正=排名下降（数值变差）；负=排名上升
        if chg == 0:
            arrow = "＝"
            col = "#555555"
        elif chg < 0:
            arrow = f"↑{-chg}"
            col = "#c0392b"
        else:
            arrow = f"↓{chg}"
            col = "#2980b9"
        ax.text(x_stat, y, f"最终{rl[f]:>2}  {arrow}", ha="left", va="center",
                fontsize=9.5, color=col, fontweight="bold", zorder=5)
    ax.text(x_stat, y_top + 0.6, "最终排名 / 4周变化\n（红↑=排名上升 蓝↓=排名下降）",
            ha="left", va="bottom", fontsize=10.5, color="#333333", zorder=6)

    # ---- 图例 ----
    handles = [mpatches.Patch(facecolor=c, edgecolor="none", label=k) for k, c in colors.items()]
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.005),
              framealpha=0.95, fontsize=10, ncol=5,
              title="因子分组", title_fontsize=10.5)

    # ---- 标题 ----
    ax.set_title(f"近一月因子排名迁移桑基图（周度多空收益排名，1=最强）\n"
                 f"数据截至 {str(wlast)[:4]}-{str(wlast)[4:6]}-{str(wlast)[6:]} · 因子方向已标注（↑正向 / ↓反向）\n"
                 f"最右列方框数字＝最新排名，其右标注因子名，可直接读出最终排名",
                 fontsize=14.5, fontweight="bold")

    ax.axis("off")
    fig.tight_layout()
    out_png = os.path.join(OUT, f"factor_rank_sankey{SUFFIX}.png")
    fig.savefig(out_png, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[sankey] saved {out_png}", flush=True)

    # ---- 保存排名流数据供周报引用 ----
    flow = pd.DataFrame({
        "factor": factors,
        "group": [grp.get(f, "") for f in factors],
        "direction": [direc.get(f, 1) for f in factors],
        **{f"rank_{w}": rank[w].reindex(factors).values for w in last4},
    })
    # rank_change_4w：正=排名上升（改善），负=排名下降（变差）
    flow["rank_change_4w"] = (flow[f"rank_{last4[0]}"] - flow[f"rank_{last4[-1]}"]).astype(int)
    flow = flow.sort_values(f"rank_{last4[-1]}")
    flow.to_csv(os.path.join(RAW, "sankey_flow.csv"), index=False, encoding="utf-8-sig")
    print("[sankey] flow csv saved", flush=True)
    print(flow.to_string(index=False), flush=True)


if __name__ == "__main__":
    main()