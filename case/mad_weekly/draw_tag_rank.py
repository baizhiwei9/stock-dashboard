# -*- coding: utf-8 -*-
"""绘制两张标签排序图：按 MAD20 与按今年以来涨幅（同为同花顺概念指数口径）。"""
import os
import sys

WORK = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ea8aaca93ffeHyamlhKpZfCuW2/py_deps")
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
from matplotlib import font_manager as fm
import matplotlib.pyplot as plt
from matplotlib.patches import Ellipse, FancyBboxPatch
from matplotlib.lines import Line2D

for p in ("/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/.fonts/NotoSansSC-Regular.ttf",
          "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"):
    if os.path.exists(p):
        fm.fontManager.addfont(p)
plt.rcParams["font.sans-serif"] = ["Noto Sans SC", "Droid Sans Fallback", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

W, H = 3200, 2000
C_UP, C_DN, C_TAG, C_BAR = "#d33a3a", "#2e9b4f", "#ef8c22", "#d9d9d9"
t = pd.read_csv(os.path.join(WORK, "tag_metrics.csv")).sort_values("Top50成员数", ascending=False)
t = t.sort_values("标签").reset_index(drop=True)

FIG = [
    ("指数MAD20", "标签 MAD20 排序（MA20/MA40−1，同花顺概念指数口径）",
     "标签MAD20排序_截至20261030.png",
     "圆与横条长度按 |MAD20|；红=MAD20 为正、绿=为负；括号内为该标签在 Top50 中的成分股数"),
    ("指数今年涨幅", "标签今年以来涨幅排序（2026-10-30 对 2025-12-31）",
     "标签今年以来涨幅排序_截至20261030.png",
     "圆与横条长度按 |今年以来涨幅|；红=上涨、绿=下跌；括号内为该标签在 Top50 中的成分股数"),
]
for col, title, fname, note in FIG:
    d = t.dropna(subset=[col]).sort_values(col, ascending=False).reset_index(drop=True)
    d.insert(0, "排名", range(1, len(d) + 1))
    fig = plt.figure(figsize=(32, 20), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    ax.add_patch(plt.Rectangle((0, 0), 1, 1, facecolor="white", edgecolor="none"))
    ax.text(0.5, 0.962, title, ha="center", va="center", fontsize=40, fontweight="bold", color=C_TAG)
    ax.text(0.5, 0.925, "样本：MAD20 降序前 50 只个股覆盖的标签，剔除改革／红利／两融等宽口径后保留 %d 个细分题材；数据日期 2026-10-30" % len(d),
            ha="center", va="center", fontsize=15, color="#666666")
    ax.text(0.5, 0.897, note, ha="center", va="center", fontsize=14, color="#8a8a8a")

    vmax = d[col].abs().max()
    ncol, per = 2, int(np.ceil(len(d) / 2))
    colx = [0.030, 0.515]
    ytop, ybot = 0.845, 0.098
    for i, r in d.iterrows():
        cidx, row = divmod(i, per)
        x0 = colx[cidx]
        y = ytop - row * ((ytop - ybot) / max(per - 1, 1))
        val = r[col]
        color = C_UP if val > 0 else C_DN
        barw = 0.255 * abs(val) / vmax + 0.004
        ax.add_patch(FancyBboxPatch((x0 + 0.115, y - 0.0062), barw, 0.0124,
                                    boxstyle="round,pad=0.001,rounding_size=0.004",
                                    facecolor=color, edgecolor="none", alpha=0.85))
        rr = 0.0042 + 0.0115 * abs(val) / vmax
        ax.add_patch(Ellipse((x0 + 0.032, y), 2 * rr, 2 * rr * W / H, facecolor=C_TAG, edgecolor="none"))
        ax.text(x0 + 0.004, y, "%d" % r.排名, ha="left", va="center", fontsize=11.5, color="#9a9a9a")
        ax.text(x0 + 0.050, y, r.标签, ha="left", va="center", fontsize=13.5, color="#333333")
        ax.text(x0 + 0.115 + barw + 0.010, y, "%+.2f%%（%d）" % (val * 100, r.Top50成员数),
                ha="left", va="center", fontsize=12.5, color=color)
    # 图例
    ax.add_patch(FancyBboxPatch((0.030, 0.010), 0.325, 0.045, boxstyle="round,pad=0.005",
                                facecolor="#fafafa", edgecolor="#cfcfcf", linewidth=1.2))
    ax.add_patch(Ellipse((0.048, 0.0325), 0.010, 0.010 * W / H, facecolor=C_TAG, edgecolor="none"))
    ax.text(0.060, 0.0325, "橙色圆=标签（大小=|涨幅|）", fontsize=12.5, va="center", color="#333333")
    ax.add_line(Line2D([0.128, 0.146], [0.0325, 0.0325], color=C_UP, linewidth=4))
    ax.text(0.152, 0.0325, "红=为正/上涨", fontsize=12.5, va="center", color="#333333")
    ax.add_line(Line2D([0.205, 0.223], [0.0325, 0.0325], color=C_DN, linewidth=4))
    ax.text(0.229, 0.0325, "绿=为负/下跌", fontsize=12.5, va="center", color="#333333")
    ax.text(0.030, 0.072, "括号内数字=该标签在 Top50 中的成分股数；口径：同花顺概念指数（指数 MA20/MA40−1 与指数今年以来涨幅）",
            fontsize=12.5, va="center", color="#8a8a8a")
    ax.text(0.030, 0.876, "【按数值降序：左列 1–%d，右列 %d–%d】" % (per, per + 1, len(d)),
            fontsize=13, va="center", color="#8a8a8a")
    # ---- 核验：文字覆盖与重叠 ----
    fig.canvas.draw(); rend = fig.canvas.get_renderer()
    txt = [x.get_text() for x in ax.texts]
    miss = [r.标签 for _, r in d.iterrows() if r.标签 not in txt]
    miss_v = [r.标签 for _, r in d.iterrows() if ("%+.2f%%（%d）" % (r[col] * 100, r.Top50成员数)) not in txt]
    boxes = [(x.get_text(), x.get_window_extent(rend)) for x in ax.texts]
    ov = [(a, b) for i in range(len(boxes)) for j in range(i + 1, len(boxes))
          if boxes[i][1].overlaps(boxes[j][1]) for a, b in [(boxes[i][0], boxes[j][0])]]
    print("[%s] 标签缺失 %d / 数值缺失 %d / 文字重叠 %d 对" % (col, len(miss), len(miss_v), len(ov)))
    for a, b in ov[:8]: print("    重叠:", a[:16], "×", b[:16])
    out = os.path.join(WORK, fname)
    fig.savefig(out, facecolor="white"); plt.close(fig)
    print("saved", out, "%.0f KB | 行数 %d" % (os.path.getsize(out) / 1024, len(d)))