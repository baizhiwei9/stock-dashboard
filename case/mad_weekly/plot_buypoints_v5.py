# -*- coding: utf-8 -*-
"""修正口径版买点标注图：缠论标笔底/向上笔确认状态，SMC 只标新鲜未填补区间。"""
import os
import sys
import json
import glob

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

WIN, GUT = 120, 62
UP, DN = "#d33a3a", "#2e9b4f"
C_SMC, C_CHAN, C_UP = "#00796b", "#b3261e", "#1565c0"

m = pd.read_csv(os.path.join(WORK, "buypoint_merged_v2.csv"), dtype={"代码": str}).fillna("—")
hits = m[m["命中"] != "未命中"].copy()
hits["p"] = hits["命中"].map({"缠论+SMC": 0, "缠论": 1, "SMC": 2})
hits = hits.sort_values(["p", "代码"]).reset_index(drop=True)
chan = pd.read_csv(os.path.join(WORK, "chan_live_v2.csv"), dtype={"ts_code": str}).fillna("—")
smc = pd.read_csv(os.path.join(WORK, "smc_valid_live.csv"), dtype={"ts_code": str})

fig, axes = plt.subplots(6, 5, figsize=(5.0 * 5, 3.8 * 6), dpi=100)
axes = axes.ravel()

for k, row in hits.iterrows():
    code, name = row.代码, row["名称"]
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
    ax.plot(x, seg.close.rolling(20).mean(), color="#f39c12", linewidth=0.8)
    ax.plot(x, seg.close.rolling(40).mean(), color="#2c7fb8", linewidth=0.8, linestyle="--")

    ylo, yhi = seg.low.min(), seg.high.max()
    gutter, foot = [], []

    # ---- SMC 新鲜区间（仅有效买点）----
    for _, z in smc[smc.ts_code == code].iterrows():
        zlo, zhi = float(z.下沿ZD), float(z.上沿ZG)
        ylo, yhi = min(ylo, zlo), max(yhi, zhi)
        x0 = max(0, int(z.区间形成行号) - off)
        x1 = min(WIN - 1, int(z.买点行号) - off)
        ax.add_patch(Rectangle((x0, zlo), max(x1 - x0, 0.8), zhi - zlo, facecolor=C_SMC, alpha=0.16,
                               linewidth=0, zorder=1))
        for yv in (zlo, zhi):
            ax.hlines(yv, x0, x1, color=C_SMC, linewidth=0.9, linestyle=":", zorder=2)
        bi = int(z.买点行号) - off
        dip = float(z["跌到价(触发日最低)"]); px = float(z["买点价(收盘)"])
        ax.plot([bi], [dip], marker="v", color=C_SMC, markersize=6, zorder=7)
        ax.hlines(px, bi - 2.5, bi + 2.5, color=C_SMC, linewidth=1.3, zorder=7)
        gutter.append((zhi, x1, "%s 区间 %.2f~%.2f" % (z.类型, zlo, zhi), C_SMC))
        gutter.append((px, bi, "SMC 跌到 %.2f→买点 %.2f（%s）" % (dip, px, str(z.买点日期)[5:]), C_SMC))
        foot.append("SMC %s 区间 %.2f~%.2f｜形成 %s → 首次触碰 %s，跌到 %.2f 触发，买点价 %.2f"
                    % (z.类型, zlo, zhi, z.区间形成日, z.首次触碰日, dip, px))

    # ---- 缠论：笔 + 笔底买点 + 向上笔确认状态 ----
    ch = chan[chan.ts_code == code].iloc[0]
    if str(ch.缠论有效) in ("True", "1"):   # numpy.bool_ 与 Python True 不相等，统一转字符串判断
        r = json.load(open(os.path.join(WORK, "chan_out", "%s.json" % code), encoding="utf-8"))
        pen = [e for e in r["结构"]["笔端点"] if int(e["行号"]) >= off]
        if len(pen) >= 2:
            xs = [int(e["行号"]) - off for e in pen]
            ys = [float(e["价格"]) for e in pen]
            ax.plot(xs, ys, color="#777777", linewidth=0.8, zorder=4)
        bi = int(d.index[d.date.astype(str).str[:10] == str(ch.缠论日期)][0])
        bx = bi - off
        bpx, bdip = float(ch.缠论价格), float(ch.笔底价)
        ylo, yhi = min(ylo, bdip), max(yhi, bdip)
        ax.plot([bx], [bdip], marker="^", color=C_CHAN, markersize=7.5, zorder=8)
        ax.hlines(bpx, bx - 2.5, bx + 2.5, color=C_CHAN, linewidth=1.4, zorder=8)
        if str(ch.向上笔已确认) == "True":
            conf = chan[chan.ts_code == code].iloc[0]
            cd = str(ch.向上笔确认日)
            ci = d.index[d.date.astype(str).str[:10] == cd]
            if len(ci):
                cix = int(ci[0]) - off
                top = float(r["结构"]["笔端点"][[i for i, e in enumerate(r["结构"]["笔端点"])
                                               if str(e["时间"])[:10] == cd][0]]["价格"])
                ax.plot([bx, cix], [bdip, top], color=C_UP, linewidth=1.6, zorder=6)
                up_lbl = "向上笔已确认(→%.2f)" % top
            else:
                up_lbl = "向上笔已确认"
        else:
            ax.plot([bx, WIN - 1], [bdip, bdip * 1.0], color=C_UP, linewidth=0.9, linestyle=(0, (4, 3)),
                    alpha=0.8, zorder=6)
            up_lbl = "向上笔待确认"
        gutter.append((bpx, bx, "缠论%s %s 买点 %.2f（笔底 %.2f，%s）" % (ch.缠论买点, ch.缠论日期, bpx, bdip, up_lbl), C_CHAN))
        foot.append("缠论 %s 买点 %.2f（%s）｜位于向下笔终点=笔底 %.2f，%s"
                    % (ch.缠论买点, bpx, ch.缠论日期, bdip, up_lbl))

    # ---- 右侧标签栏（防重叠排布）----
    rng = yhi - ylo
    pad = rng * 0.09
    top, bot = yhi + pad - 0.02 * rng, ylo - pad + 0.02 * rng
    gutter.sort(key=lambda t: -t[0])
    placed = []
    for yv, x_end, txt, col in gutter:
        yy = yv if not placed else min(yv, placed[-1] - 0.062 * rng)
        placed.append(yy)
    if placed and placed[-1] < bot:
        nn = len(placed)
        placed = [top - i * (top - bot) / max(nn - 1, 1) for i in range(nn)]
    for (yv, x_end, txt, col), yy in zip(gutter, placed):
        ax.plot([x_end, WIN + GUT - 8], [yv, yy], color=col, linewidth=0.45, alpha=0.55, zorder=3)
    for (yv, x_end, txt, col), yy in zip(gutter, placed):
        ax.text(WIN + GUT - 2, yy, txt, fontsize=6.2, color=col, va="center", ha="right", zorder=9)

    ax.set_ylim(bot, top)
    ax.set_xlim(-2, WIN + GUT)
    ax.axvline(WIN + 1.2, color="#bbbbbb", linewidth=0.6)
    ax.set_title("%s（%s）  %s" % (name, code, row["命中"]), fontsize=9.5, pad=3)
    ax.text(0.0, -0.075, "\n".join(dict.fromkeys(foot)), transform=ax.transAxes, fontsize=5.6,
            color="#222222", va="top", linespacing=1.4)
    ax.text(1.0, -0.075, "末日收盘 %.2f" % float(d.close.iloc[-1]), transform=ax.transAxes, fontsize=5.8,
            ha="right", va="top")
    ax.set_xticks([])
    ax.tick_params(axis="y", labelsize=6)
    ax.grid(alpha=0.15, linewidth=0.4)
    for s in ax.spines.values():
        s.set_linewidth(0.5)

for j in range(len(hits), len(axes)):
    axes[j].axis("off")

fig.suptitle(
    "修正口径后的买点（每只一个子图，近 %d 个交易日，前复权，截至 2026-10-30）\n"
    "浅绿带=新鲜 SMC 区间（OB 订单块／FVG 失衡，未被填补）｜▼SMC买点：跌到区间价→触发收盘价｜"
    "▲缠论买点：位于向下笔终点(笔底)，蓝实线=已确认向上笔、蓝虚线=向上笔待确认｜灰线=缠论笔" % WIN,
    fontsize=12, y=0.995)
fig.subplots_adjust(left=0.02, right=0.998, top=0.972, bottom=0.012, hspace=0.42, wspace=0.30)

fig.canvas.draw()
rend = fig.canvas.get_renderer()
iss = []
for k, row in hits.iterrows():
    ax = axes[k]
    ab = ax.get_window_extent(rend)
    for t in ax.texts:
        if t.get_transform() is not ax.transData:
            continue
        b = t.get_window_extent(rend)
        if b.x0 < ab.x0 - 1 or b.x1 > ab.x1 + 1 or b.y0 < ab.y0 - 1 or b.y1 > ab.y1 + 1:
            iss.append(("越界", row["名称"], t.get_text()[:18]))
    lab = [(t.get_text()[:18], t.get_window_extent(rend)) for t in ax.texts if t.get_transform() is ax.transData]
    for i in range(len(lab)):
        for j in range(i + 1, len(lab)):
            if lab[i][1].overlaps(lab[j][1]):
                iss.append(("重叠", row["名称"], lab[i][0], lab[j][0]))
print("标注框问题: 越界 %d / 重叠 %d" % (sum(1 for i in iss if i[0] == "越界"),
                                       sum(1 for i in iss if i[0] == "重叠")))
for it in iss[:8]:
    print("   ", it)

# 与表格逐只一致性：每只标的是否都含表格中的买点日期与价格
miss = []
for k, row in hits.iterrows():
    ax = axes[k]
    drawn = " ".join(t.get_text() for t in ax.texts)
    if row["命中"] in ("缠论", "缠论+SMC") and (str(row.缠论日期) not in drawn or "%.2f" % float(row.缠论价) not in drawn):
        miss.append((row["名称"], "缠论", row.缠论日期, row.缠论价))
    if row["命中"] in ("SMC", "缠论+SMC") and ("%.2f" % float(row.SMC买点价) not in drawn):
        miss.append((row["名称"], "SMC", row.SMC买点价))
print("与清单不一致的标注条数:", len(miss), miss[:5])

out = os.path.join(WORK, "买点标注_修正口径_缠论笔底与SMC新鲜区间_截至20261030.png")
fig.savefig(out, facecolor="white")
print("saved", out, "%.0f KB" % (os.path.getsize(out) / 1024), "| 子图:", len(hits))