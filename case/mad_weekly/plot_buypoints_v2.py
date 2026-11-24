# -*- coding: utf-8 -*-
"""重绘：每只标的一个子图，明确标出 SMC 区间带（FVG/订单块）、缠论中枢与买点触发价位。"""
import os
import sys
import json

WORK = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ea8aaca93ffeHyamlhKpZfCuW2/py_deps")
import matplotlib
matplotlib.use("Agg")
from matplotlib import font_manager as fm
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd

for p in ("/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/.fonts/NotoSansSC-Regular.ttf",
          "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"):
    if os.path.exists(p):
        fm.fontManager.addfont(p)
plt.rcParams["font.sans-serif"] = ["Noto Sans SC", "Droid Sans Fallback", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

WIN = 120
UP, DN = "#d33a3a", "#2e9b4f"
C_SMC = "#00796b"
C_CHAN = "#b3261e"
BB = dict(facecolor="white", alpha=0.72, edgecolor="none", pad=0.6)

man = pd.read_csv(os.path.join(WORK, "annotate_manifest.csv"), dtype={"ts_code": str})
merged = pd.read_csv(os.path.join(WORK, "buypoint_merged.csv"), dtype={"ts_code": str})
hits = merged[merged["命中来源"] != "未命中"].copy()
prio = {"缠论+SMC": 0, "缠论": 1, "SMC": 2}
hits["p"] = hits["命中来源"].map(prio)
hits = hits.sort_values(["p", "ts_code"]).reset_index(drop=True)

ncol, nrow = 5, 6
fig, axes = plt.subplots(nrow, ncol, figsize=(4.9 * ncol, 3.45 * nrow), dpi=100)
axes = axes.ravel()

for k, row in hits.iterrows():
    code, name = row.ts_code, row["name"]
    ax = axes[k]
    d = pd.read_csv(os.path.join(WORK, "chan_in", "%s.csv" % code), parse_dates=["date"]).reset_index(drop=True)
    n = len(d)
    off = n - WIN
    seg = d.iloc[-WIN:].reset_index(drop=True)
    x = np.arange(len(seg))
    up = seg.close.values >= seg.open.values
    ax.vlines(x, seg.low, seg.high, color=[UP if u else DN for u in up], linewidth=0.6)
    for i, (_, b) in enumerate(seg.iterrows()):
        c = UP if b.close >= b.open else DN
        lo, hi = min(b.open, b.close), max(b.open, b.close)
        ax.add_patch(Rectangle((i - 0.32, lo), 0.64, max(hi - lo, 1e-9), facecolor=c, edgecolor=c, linewidth=0))
    ax.plot(x, seg.close.rolling(20).mean(), color="#f39c12", linewidth=0.9)
    ax.plot(x, seg.close.rolling(40).mean(), color="#2c7fb8", linewidth=0.9, linestyle="--")

    sub = man[man.ts_code == code]
    ylo = seg.low.min()
    yhi = seg.high.max()
    foot = []
    for _, z in sub.iterrows():
        zlo, zhi = float(z.下沿), float(z.上沿)
        ylo, yhi = min(ylo, zlo), max(yhi, zhi)
        if z.标注类型 == "SMC":
            x0 = max(0, int(z.区间起点K线) - off)
            col, tag = C_SMC, ("订单块" if z.子类 == "OB" else "失衡FVG")
            ax.add_patch(Rectangle((x0, zlo), WIN - x0, zhi - zlo, facecolor=col, alpha=0.12, linewidth=0))
            ax.hlines([zlo, zhi], x0, WIN - 1, color=col, linewidth=0.8, linestyle=":")
            ax.text(x0 + 0.8, (zlo + zhi) / 2, "%s %.2f~%.2f" % (z.子类, zlo, zhi), fontsize=6.2,
                    color=col, ha="left", va="center", bbox=BB, zorder=6)
            bi = int(z.买点行号) - off
            if 0 <= bi < WIN:
                dip = float(d.low[int(z.买点行号)])
                ax.plot([bi], [dip], marker="v", color=col, markersize=5.5, zorder=5)
                ax.hlines(float(z.买点价格), bi - 3, bi + 3, color=col, linewidth=1.1, linestyle="-")
                ax.text(bi, dip, "跌到 %.2f 触发→买点 %.2f" % (dip, float(z.买点价格)), fontsize=5.6, color=col,
                        ha="center", va="top", bbox=BB)
            foot.append("SMC %s 区间 %.2f~%.2f｜跌到 %.2f 触发，买点价 %.2f（%s）" % (
                z.子类, zlo, zhi, float(d.low[int(z.买点行号)]), float(z.买点价格), str(z.买点日期)[5:]))
        else:
            x0 = max(0, int(z.区间起点K线) - off) if pd.notna(z.区间起点K线) else 0
            x1 = min(WIN - 1, int(z.中枢结束K线) - off) if pd.notna(z.中枢结束K线) else WIN - 1
            if x1 <= x0:
                x1 = min(WIN - 1, x0 + 6)
            ax.add_patch(Rectangle((x0, zlo), x1 - x0, zhi - zlo, facecolor=C_CHAN, alpha=0.10, linewidth=0))
            ax.hlines([zlo, zhi], x0, x1, color=C_CHAN, linewidth=0.8, linestyle="-.")
            ax.text(x0 + 0.8, (zlo + zhi) / 2, "缠论中枢 %.2f~%.2f" % (zlo, zhi), fontsize=6.2,
                    color=C_CHAN, ha="left", va="center", bbox=BB, zorder=6)
            bi = int(z.买点行号) - off
            if 0 <= bi < WIN:
                ax.plot([bi], [d.low[int(z.买点行号)]], marker="^", color=C_CHAN, markersize=6, zorder=5)
                ax.hlines(float(z.买点价格), bi - 3, bi + 3, color=C_CHAN, linewidth=1.2)
                ax.text(bi, d.low[int(z.买点行号)], "%s 买点 %.2f" % (z.子类, float(z.买点价格)), fontsize=5.8,
                        color=C_CHAN, ha="center", va="top", bbox=BB)
            foot.append("缠论 %s 中枢 %.2f~%.2f｜买点价 %.2f（%s）" % (z.子类, zlo, zhi, float(z.买点价格),
                                                                  str(z.买点日期)[5:]))
    pad = (yhi - ylo) * 0.10
    ax.set_ylim(ylo - pad, yhi + pad * 1.15)
    ax.set_title("%s（%s）  %s" % (name, code, row["命中来源"]), fontsize=9, pad=3)
    ax.text(0.005, 0.015, "\n".join(dict.fromkeys(foot)), transform=ax.transAxes, fontsize=5.4,
            color="#222222", va="bottom", linespacing=1.35)
    ax.text(0.995, 0.015, "末日收盘 %.2f" % row["末日收盘"], transform=ax.transAxes, fontsize=5.6,
            color="#222222", ha="right")
    ax.set_xticks([])
    ax.tick_params(axis="y", labelsize=6)
    ax.grid(alpha=0.16, linewidth=0.4)
    for s in ax.spines.values():
        s.set_linewidth(0.5)

for j in range(len(hits), len(axes)):
    axes[j].axis("off")

fig.suptitle(
    "出现买点的标的：每只一个子图（近 %d 个交易日，前复权，截至 2026-10-30）\n"
    "浅绿带+虚线 = SMC 区间（订单块 OB／失衡 FVG）并标出上下沿价格与“跌到多少触发”；"
    "浅红带+-.虚线 = 缠论中枢（上沿ZG／下沿ZD）；红色▲=缠论买点，绿色▼=SMC 买点，横线=买点价位"
    % WIN, fontsize=12.5, y=0.995)
fig.tight_layout(rect=[0, 0, 1, 0.972])

# ---- 标注框诊断：是否越界 / 是否相互重叠 ----
fig.canvas.draw()
_rend = fig.canvas.get_renderer()
issues = []
for k, row in hits.iterrows():
    ax = axes[k]
    ax_box = ax.get_window_extent(_rend)
    lab = [(t.get_text()[:16], t.get_window_extent(_rend))
           for t in ax.texts if t.get_bbox_patch() is not None]
    for txt, b in lab:
        if b.x0 < ax_box.x0 - 1 or b.x1 > ax_box.x1 + 1 or b.y0 < ax_box.y0 - 1 or b.y1 > ax_box.y1 + 1:
            issues.append(("越界", row["name"], txt))
    for i in range(len(lab)):
        for j in range(i + 1, len(lab)):
            if lab[i][1].overlaps(lab[j][1]):
                issues.append(("重叠", row["name"], lab[i][0], lab[j][0]))
print("标注框问题: 越界 %d / 重叠 %d" % (sum(1 for x in issues if x[0] == "越界"),
                                       sum(1 for x in issues if x[0] == "重叠")))
for x in issues[:10]:
    print("   ", x)

out = os.path.join(WORK, "买点区间标注_缠论中枢与SMC区间_截至20261030.png")
fig.savefig(out, facecolor="white")
print("saved", out, "%.0f KB" % (os.path.getsize(out) / 1024), "| 子图:", len(hits), "| 标注对象:", len(man))