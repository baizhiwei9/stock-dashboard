# -*- coding: utf-8 -*-
"""重绘 v4：区间带 + 右侧价格标签栏（带内不再压字），标出买点价位。"""
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

WIN, GUT = 120, 58
UP, DN = "#d33a3a", "#2e9b4f"
C_SMC, C_CHAN = "#00796b", "#b3261e"

man = pd.read_csv(os.path.join(WORK, "annotate_manifest.csv"), dtype={"ts_code": str})
merged = pd.read_csv(os.path.join(WORK, "buypoint_merged.csv"), dtype={"ts_code": str})
hits = merged[merged["命中来源"] != "未命中"].copy()
hits["p"] = hits["命中来源"].map({"缠论+SMC": 0, "缠论": 1, "SMC": 2})
hits = hits.sort_values(["p", "ts_code"]).reset_index(drop=True)

fig, axes = plt.subplots(6, 5, figsize=(4.9 * 5, 3.75 * 6), dpi=100)
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
    gutter, foot, trig = [], [], []
    for _, z in sub.iterrows():
        zlo, zhi = float(z.下沿), float(z.上沿)
        ylo, yhi = min(ylo, zlo), max(yhi, zhi)
        if z.标注类型 == "SMC":
            x0 = max(0, int(z.区间起点K线) - off)
            x1 = WIN - 1
            ax.add_patch(Rectangle((x0, zlo), x1 - x0, zhi - zlo, facecolor=C_SMC, alpha=0.13, linewidth=0, zorder=1))
            for yv in (zlo, zhi):
                ax.hlines(yv, x0, x1, color=C_SMC, linewidth=0.8, linestyle=":", zorder=2)
            gutter.append((zhi, x1, "%s上沿 %.2f" % (z.子类, zhi), C_SMC))
            gutter.append((zlo, x1, "%s下沿 %.2f" % (z.子类, zlo), C_SMC))
            bi = int(z.买点行号) - off
            trig.append((bi, float(d.low[int(z.买点行号)]), float(z.买点价格), C_SMC, "v", "SMC"))
            foot.append("SMC %s 区间 %.2f~%.2f｜跌到 %.2f 触发，买点价 %.2f（%s）" % (
                z.子类, zlo, zhi, float(d.low[int(z.买点行号)]), float(z.买点价格), str(z.买点日期)[5:]))
        else:
            x0 = max(0, int(z.区间起点K线) - off) if pd.notna(z.区间起点K线) else 0
            x1 = min(WIN - 1, int(z.中枢结束K线) - off) if pd.notna(z.中枢结束K线) else WIN - 1
            if x1 <= x0:
                x1 = min(WIN - 1, x0 + 6)
            ax.add_patch(Rectangle((x0, zlo), x1 - x0, zhi - zlo, facecolor=C_CHAN, alpha=0.11, linewidth=0, zorder=1))
            for yv in (zlo, zhi):
                ax.hlines(yv, x0, x1, color=C_CHAN, linewidth=0.8, linestyle="-.", zorder=2)
            gutter.append((zhi, x1, "中枢上沿 %.2f" % zhi, C_CHAN))
            gutter.append((zlo, x1, "中枢下沿 %.2f" % zlo, C_CHAN))
            trig.append((int(z.买点行号) - off, float(d.low[int(z.买点行号)]), float(z.买点价格), C_CHAN, "^", "缠论"))
            foot.append("缠论 %s 中枢 %.2f~%.2f｜买点价 %.2f（%s）" % (z.子类, zlo, zhi, float(z.买点价格),
                                                                  str(z.买点日期)[5:]))
    # 买点标记与买点价标签（进右侧标签栏）
    seen = set()
    for bi, dip, px, col, mk, tag in trig:
        if not (0 <= bi < WIN):
            continue
        ax.plot([bi], [dip], marker=mk, color=col, markersize=5.5, zorder=7)
        ax.hlines(px, bi - 2.5, bi + 2.5, color=col, linewidth=1.2, zorder=7)
        if (bi, round(px, 2)) not in seen:
            seen.add((bi, round(px, 2)))
            gutter.append((px, bi, ("%s买点 %.2f｜跌到 %.2f" % (tag, px, dip)) if tag == "SMC"
                            else ("缠论%s买点 %.2f" % (mk, px)), col))
    # 右侧标签栏：按价格降序排布并强制最小间距，避免压字
    rng = yhi - ylo
    min_gap = 0.062 * rng
    pad = rng * 0.09
    top, bot = yhi + pad - 0.02 * rng, ylo - pad + 0.02 * rng
    gutter.sort(key=lambda t: -t[0])
    placed = []
    for yv, x_end, txt, col in gutter:          # 自上而下贪心排布，强制最小间距
        yy = yv if not placed else min(yv, placed[-1] - min_gap)
        placed.append(yy)
    if placed and placed[-1] < bot:              # 触底则整体重排为等距分布，避免贴底压字
        n = len(placed)
        placed = [top - i * (top - bot) / max(n - 1, 1) for i in range(n)]
    for (yv, x_end, txt, col), yy in zip(gutter, placed):
        ax.plot([x_end, WIN + GUT - 8], [yv, yy], color=col, linewidth=0.45, alpha=0.55, zorder=3)
        ax.text(WIN + GUT - 2, yy, txt, fontsize=6.2, color=col, va="center", ha="right", zorder=9)
    ax.set_ylim(bot, top)
    ax.set_xlim(-2, WIN + GUT)
    ax.axvline(WIN + 1.2, color="#bbbbbb", linewidth=0.6)
    ax.set_title("%s（%s）  %s" % (name, code, row["命中来源"]), fontsize=9.5, pad=3)
    ax.text(0.0, -0.075, "\n".join(dict.fromkeys(foot)), transform=ax.transAxes, fontsize=5.7,
            color="#222222", va="top", linespacing=1.4)
    ax.text(1.0, -0.075, "末日收盘 %.2f" % row["末日收盘"], transform=ax.transAxes, fontsize=5.9,
            color="#222222", ha="right", va="top")
    ax.set_xticks([])
    ax.tick_params(axis="y", labelsize=6)
    ax.grid(alpha=0.15, linewidth=0.4)
    for s in ax.spines.values():
        s.set_linewidth(0.5)

for j in range(len(hits), len(axes)):
    axes[j].axis("off")

fig.suptitle(
    "出现买点的标的：每只一个子图（近 %d 个交易日，前复权，截至 2026-10-30），右侧为价格标签栏\n"
    "浅绿带 = SMC 区间（OB 订单块／FVG 失衡）｜浅红带 = 缠论中枢（ZG 上沿／ZD 下沿）｜"
    "▲红=缠论买点，▼绿=SMC 买点，短横线=买点成交价" % WIN, fontsize=12.5, y=0.995)
fig.subplots_adjust(left=0.02, right=0.998, top=0.972, bottom=0.012, hspace=0.42, wspace=0.30)

fig.canvas.draw()
rend = fig.canvas.get_renderer()
iss = []
for k, row in hits.iterrows():
    ax = axes[k]
    ab = ax.get_window_extent(rend)
    lab = [(t.get_text()[:20], t.get_window_extent(rend)) for t in ax.texts
           if t.get_transform() is ax.transData]
    for t in ax.texts:
        if t.get_transform() is not ax.transData:
            continue
        b = t.get_window_extent(rend)
        if b.x0 < ab.x0 - 1 or b.x1 > ab.x1 + 1 or b.y0 < ab.y0 - 1 or b.y1 > ab.y1 + 1:
            iss.append(("越界", row["name"], t.get_text()[:20]))
    for i in range(len(lab)):
        for j in range(i + 1, len(lab)):
            if lab[i][1].overlaps(lab[j][1]):
                iss.append(("重叠", row["name"], lab[i][0], lab[j][0],
                            lab[i][1].y0, lab[j][1].y0, lab[i][1].x0, lab[i][1].x1))
print("标注框问题: 越界 %d / 重叠 %d" % (sum(1 for i in iss if i[0] == "越界"),
                                       sum(1 for i in iss if i[0] == "重叠")))
for it in iss[:8]:
    print("   ", it)

# ---- 数值一致性核验：图上文字是否包含清单中的区间上下沿与买点价 ----
miss = []
for k, row in hits.iterrows():
    ax = axes[k]
    drawn = " ".join(t.get_text() for t in ax.texts if t.get_transform() is ax.transData)
    for _, z in man[man.ts_code == row.ts_code].iterrows():
        need = ["%.2f" % float(z.下沿), "%.2f" % float(z.上沿), "%.2f" % float(z.买点价格)]
        if z.标注类型 == "SMC":
            nd = pd.read_csv(os.path.join(WORK, "chan_in", "%s.csv" % row.ts_code))
            need.append("%.2f" % float(nd.low[int(z.买点行号)]))
        for t in need:
            if t not in drawn:
                miss.append((row["name"], z.标注类型, z.子类, t))
print("图上文字缺失关键数值的条数:", len(miss), miss[:5])

out = os.path.join(WORK, "买点区间标注_缠论中枢与SMC区间_截至20261030.png")
fig.savefig(out, facecolor="white")
print("saved", out, "%.0f KB" % (os.path.getsize(out) / 1024), "| 子图:", len(hits), "| 标注对象:", len(man))