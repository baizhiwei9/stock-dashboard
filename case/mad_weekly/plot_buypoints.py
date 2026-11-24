# -*- coding: utf-8 -*-
"""把出现买点（缠论/SMC）的标的画成一张多子图：每只标的一个子图。"""
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

WIN = 120            # 每个子图显示的K线根数
UP, DN = "#d33a3a", "#2e9b4f"

merged = pd.read_csv(os.path.join(WORK, "buypoint_merged.csv"), dtype={"ts_code": str})
hits = merged[merged["命中来源"] != "未命中"].copy()
prio = {"缠论+SMC": 0, "缠论": 1, "SMC": 2}
hits["p"] = hits["命中来源"].map(prio)
hits = hits.sort_values(["p", "ts_code"]).reset_index(drop=True)

chan_live = pd.read_csv(os.path.join(WORK, "chan_latest_buypoint.csv"), dtype={"ts_code": str})
chan_live = chan_live[chan_live.当前有效]
smc_live = pd.read_csv(os.path.join(WORK, "smc_buypoints.csv"), dtype={"ts_code": str})
smc_live = smc_live[smc_live.距末日K线 <= 30]

ncol, nrow = 5, 6
fig, axes = plt.subplots(nrow, ncol, figsize=(4.1 * ncol, 2.75 * nrow), dpi=110)
axes = axes.ravel()

for k, row in hits.iterrows():
    code, name = row.ts_code, row["name"]
    ax = axes[k]
    d = pd.read_csv(os.path.join(WORK, "chan_in", "%s.csv" % code), parse_dates=["date"]).reset_index(drop=True)
    n = len(d)
    seg = d.iloc[-WIN:].reset_index(drop=True)
    x = np.arange(len(seg))
    up = seg.close.values >= seg.open.values
    ax.vlines(x, seg.low, seg.high, color=[UP if u else DN for u in up], linewidth=0.6)
    for i, (_, b) in enumerate(seg.iterrows()):
        c = UP if b.close >= b.open else DN
        lo, hi = min(b.open, b.close), max(b.open, b.close)
        ax.add_patch(Rectangle((i - 0.32, lo), 0.64, max(hi - lo, 1e-6), facecolor=c, edgecolor=c, linewidth=0))
    ma20 = seg.close.rolling(20).mean()
    ma40 = seg.close.rolling(40).mean()
    ax.plot(x, ma20, color="#f39c12", linewidth=0.9, label="MA20")
    ax.plot(x, ma40, color="#2c7fb8", linewidth=0.9, linestyle="--", label="MA40")
    # 缠论笔（落在窗口内的端点连线）
    try:
        st = json.load(open(os.path.join(WORK, "chan_out", "%s.json" % code), encoding="utf-8"))["结构"]["笔端点"]
        pts = [(int(p["行号"]), float(p["价格"])) for p in st if int(p["行号"]) >= n - WIN]
        if len(pts) >= 2:
            ax.plot([p[0] - (n - WIN) for p in pts], [p[1] for p in pts], color="#555555", linewidth=0.7, alpha=0.75)
    except Exception:
        pass
    # 买点标注
    txt = []
    for _, b in chan_live[chan_live.ts_code == code].iterrows():
        bi = b.买点日期
        m = seg.index[seg.date.astype(str).str[:10] == str(bi)]
        if len(m):
            i0 = int(m[0])
            ax.annotate(b.最新买点类型 if "最新买点类型" in b else b.类型, (i0, seg.low[i0]),
                        textcoords="offset points", xytext=(0, -12), ha="center", fontsize=6.5,
                        color="#b3261e", fontweight="bold",
                        arrowprops=dict(arrowstyle="-", color="#b3261e", lw=0.6))
            txt.append("缠论 %s %s@%.2f" % (b.最新买点类型 if "最新买点类型" in b else b.类型, str(bi)[5:], b.买点价格))
    for _, b in smc_live[smc_live.ts_code == code].iterrows():
        bi = str(b.买点日期)
        m = seg.index[seg.date.astype(str).str[:10] == bi]
        if len(m):
            i0 = int(m[0])
            ax.annotate(b.SMC类型, (i0, seg.low[i0]), textcoords="offset points", xytext=(10, -6),
                        ha="center", fontsize=6.5, color="#00796b", fontweight="bold",
                        arrowprops=dict(arrowstyle="-", color="#00796b", lw=0.6))
            txt.append("SMC %s %s@%.2f" % (b.SMC类型, bi[5:], b.买点价格))
    ax.set_title("%s（%s）  %s" % (name, code, row["命中来源"]), fontsize=8.5, pad=3)
    ax.text(0.01, 0.02, " | ".join(dict.fromkeys(txt)), transform=ax.transAxes, fontsize=6.2, color="#333333")
    ax.text(0.99, 0.02, "末日收盘 %.2f" % row["末日收盘"], transform=ax.transAxes, fontsize=6.2,
            color="#333333", ha="right")
    ax.set_xticks([])
    ax.tick_params(axis="y", labelsize=6)
    ax.grid(alpha=0.18, linewidth=0.4)
    for s in ax.spines.values():
        s.set_linewidth(0.5)

for j in range(len(hits), len(axes)):
    axes[j].axis("off")

fig.suptitle("Top50 三标签系列中出现买点的标的（每只一个子图，近 %d 个交易日，前复权，截至 2026-10-30）\n"
             "红字=缠论买点（B1一买/B2二买/B3三买）　绿字=SMC买点（OB订单块/FVG失衡）　灰线=缠论笔　橙线=MA20　蓝虚线=MA40"
             % WIN, fontsize=12, y=0.995)
fig.tight_layout(rect=[0, 0, 1, 0.975])
out = os.path.join(WORK, "买点分布_缠论与SMC_截至20261030.png")
fig.savefig(out, facecolor="white")
print("saved", out, os.path.getsize(out) // 1024, "KB", "| 子图数:", len(hits))