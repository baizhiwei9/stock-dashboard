# -*- coding: utf-8 -*-
"""重绘 v3：区间带 + 上下沿价格 + 买点触发价位，标注排布做防重叠处理。"""
import os
import sys

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
C_SMC, C_CHAN = "#00796b", "#b3261e"
BB = dict(facecolor="white", alpha=0.78, edgecolor="none", pad=0.6)

man = pd.read_csv(os.path.join(WORK, "annotate_manifest.csv"), dtype={"ts_code": str})
merged = pd.read_csv(os.path.join(WORK, "buypoint_merged.csv"), dtype={"ts_code": str})
hits = merged[merged["命中来源"] != "未命中"].copy()
hits["p"] = hits["命中来源"].map({"缠论+SMC": 0, "缠论": 1, "SMC": 2})
hits = hits.sort_values(["p", "ts_code"]).reset_index(drop=True)

ncol, nrow = 5, 6
fig, axes = plt.subplots(nrow, ncol, figsize=(4.9 * ncol, 3.7 * nrow), dpi=100)
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
    ax.vlines(x, seg.low, seg.high, color=[UP if u else DN for u in up], linewidth=0.55)
    for i, (_, b) in enumerate(seg.iterrows()):
        c = UP if b.close >= b.open else DN
        lo, hi = min(b.open, b.close), max(b.open, b.close)
        ax.add_patch(Rectangle((i - 0.32, lo), 0.64, max(hi - lo, 1e-9), facecolor=c, edgecolor=c, linewidth=0))
    ax.plot(x, seg.close.rolling(20).mean(), color="#f39c12", linewidth=0.85)
    ax.plot(x, seg.close.rolling(40).mean(), color="#2c7fb8", linewidth=0.85, linestyle="--")

    sub = man[man.ts_code == code]
    ylo, yhi = seg.low.min(), seg.high.max()
    bands, trig, foot = [], [], []
    for _, z in sub.iterrows():
        zlo, zhi = float(z.下沿), float(z.上沿)
        ylo, yhi = min(ylo, zlo), max(yhi, zhi)
        if z.标注类型 == "SMC":
            x0 = max(0, int(z.区间起点K线) - off)
            col = C_SMC
            bands.append([x0, zlo, zhi, "%s %.2f~%.2f" % (z.子类, zlo, zhi), col, "dotted"])
            bi = int(z.买点行号) - off
            trig.append([bi, float(d.low[int(z.买点行号)]), float(z.买点价格), col, "v", "SMC"])
            foot.append("SMC %s 区间 %.2f~%.2f｜跌到 %.2f 触发，买点价 %.2f（%s）" % (
                z.子类, zlo, zhi, float(d.low[int(z.买点行号)]), float(z.买点价格), str(z.买点日期)[5:]))
        else:
            x0 = max(0, int(z.区间起点K线) - off) if pd.notna(z.区间起点K线) else 0
            x1 = min(WIN - 1, int(z.中枢结束K线) - off) if pd.notna(z.中枢结束K线) else WIN - 1
            if x1 <= x0:
                x1 = min(WIN - 1, x0 + 6)
            bands.append([x0, zlo, zhi, "缠论中枢 %.2f~%.2f" % (zlo, zhi), C_CHAN, "dashdot", x1])
            trig.append([int(z.买点行号) - off, float(d.low[int(z.买点行号)]), float(z.买点价格), C_CHAN, "^", "缠论"])
            foot.append("缠论 %s 中枢 %.2f~%.2f｜买点价 %.2f（%s）" % (z.子类, zlo, zhi, float(z.买点价格),
                                                                  str(z.买点日期)[5:]))
    # 区间带 + 上下沿线 + 右端价格标签（左移错位，避免纵向压字）
    bands = sorted(bands, key=lambda b: -b[2])
    for i, b in enumerate(bands):
        x0, zlo, zhi, txt, col = b[0], b[1], b[2], b[3], b[4]
        x1 = b[6] if len(b) > 6 else WIN - 1
        ax.add_patch(Rectangle((x0, zlo), x1 - x0, zhi - zlo, facecolor=col, alpha=0.12,
                               linewidth=0, zorder=1))
        for yv in (zlo, zhi):
            ax.hlines(yv, x0, x1, color=col, linewidth=0.8, linestyle=b[5], zorder=2)
        lx = WIN - 1.5 - i * 27
        ax.text(max(lx, x0), zhi, txt, fontsize=6.0, color=col, ha="right", va="top", bbox=BB, zorder=6)
    # 买点标记（同一根K线同一价位去重）+ 价位横线
    seen = set()
    xoff = [-9, 9, -19, 19]
    ti = 0
    for bi, dip, px, col, mk, tag in trig:
        if not (0 <= bi < WIN):
            continue
        keyed = (bi, round(px, 2))
        ax.plot([bi], [dip], marker=mk, color=col, markersize=5.5, zorder=7)
        ax.hlines(px, bi - 2.5, bi + 2.5, color=col, linewidth=1.2, zorder=7)
        if keyed not in seen:
            seen.add(keyed)
            ax.text(bi + xoff[ti % 4], dip, "%s买点 %.2f" % (tag, px), fontsize=6.0, color=col,
                    ha="center", va="bottom", bbox=BB, zorder=8)
            ti += 1
    pad = (yhi - ylo) * 0.08
    ax.set_ylim(ylo - pad, yhi + pad)
    ax.set_xlim(-2, WIN + 1)
    ax.set_title("%s（%s）  %s" % (name, code, row["命中来源"]), fontsize=9, pad=3)
    ax.text(0.0, -0.085, "\n".join(dict.fromkeys(foot)), transform=ax.transAxes, fontsize=5.6,
            color="#222222", va="top", linespacing=1.4)
    ax.text(1.0, -0.085, "末日收盘 %.2f" % row["末日收盘"], transform=ax.transAxes, fontsize=5.8,
            color="#222222", ha="right", va="top")
    ax.set_xticks([])
    ax.tick_params(axis="y", labelsize=6)
    ax.grid(alpha=0.16, linewidth=0.4)
    for s in ax.spines.values():
        s.set_linewidth(0.5)

for j in range(len(hits), len(axes)):
    axes[j].axis("off")

fig.suptitle(
    "出现买点的标的：每只一个子图（近 %d 个交易日，前复权，截至 2026-10-30）\n"
    "浅绿带+点虚线 = SMC 区间（OB 订单块／FVG 失衡），带内标出区间上下沿价格；"
    "浅红带+-.线 = 缠论中枢（上沿ZG／下沿ZD）；▲红=缠论买点，▼绿=SMC 买点，价位横线=买点成交价"
    % WIN, fontsize=12.5, y=0.995)
fig.subplots_adjust(left=0.022, right=0.995, top=0.972, bottom=0.012, hspace=0.46, wspace=0.16)

fig.canvas.draw()
_rend = fig.canvas.get_renderer()
issues = []
for k, row in hits.iterrows():
    ax = axes[k]
    ab = ax.get_window_extent(_rend)
    lab = [(t.get_text()[:18], t.get_window_extent(_rend)) for t in ax.texts if t.get_bbox_patch() is not None]
    for txt, b in lab:
        if b.x0 < ab.x0 - 1 or b.x1 > ab.x1 + 1 or b.y0 < ab.y0 - 1 or b.y1 > ab.y1 + 1:
            issues.append(("越界", row["name"], txt))
    for i in range(len(lab)):
        for j in range(i + 1, len(lab)):
            if lab[i][1].overlaps(lab[j][1]):
                issues.append(("重叠", row["name"], lab[i][0], lab[j][0]))
print("标注框问题: 越界 %d / 重叠 %d" % (sum(1 for i in issues if i[0] == "越界"),
                                       sum(1 for i in issues if i[0] == "重叠")))
for it in issues[:8]:
    print("   ", it)
out = os.path.join(WORK, "买点区间标注_缠论中枢与SMC区间_截至20261030.png")
fig.savefig(out, facecolor="white")
print("saved", out, "%.0f KB" % (os.path.getsize(out) / 1024), "| 子图:", len(hits), "| 标注对象:", len(man))