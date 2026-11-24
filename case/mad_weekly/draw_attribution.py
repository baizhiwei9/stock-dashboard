# -*- coding: utf-8 -*-
"""三标签概念 × 入选个股 的涨跌归因二分关系图（数据精确版）。"""
import os
import sys

WORK = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5e9bb55354ffe9Rg1AoYf1k3lwu")
sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ea8aaca93ffeHyamlhKpZfCuW2/py_deps")
import numpy as np
import pandas as pd
from ts_http import fetch
import matplotlib
matplotlib.use("Agg")
from matplotlib import font_manager as fm
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Ellipse, FancyBboxPatch, FancyArrowPatch
from matplotlib.lines import Line2D

for p in ("/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/.fonts/NotoSansSC-Regular.ttf",
          "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"):
    if os.path.exists(p):
        fm.fontManager.addfont(p)
plt.rcParams["font.sans-serif"] = ["Noto Sans SC", "Droid Sans Fallback", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

DATE = "20261030"
TAGS = {"国企改革": "886021.TI", "人工智能": "885728.TI", "机器人概念": "885517.TI"}

# ---------- 数据 ----------
m = pd.read_csv(os.path.join(WORK, "buypoint_merged_v2.csv"), dtype={"代码": str})
sel = m[m["命中"] != "未命中"].copy()
z = np.load(os.path.join(WORK, "cache_ma", "D_%s.npz" % DATE), allow_pickle=True)
cod2i = {c: i for i, c in enumerate(z["codes"])}
pct_all = pd.Series(z["pct"], index=z["codes"])
db = pd.read_csv(os.path.join(WORK, "daily_basic_%s.csv" % DATE), dtype={"ts_code": str})
mv_all = db.set_index("ts_code")["total_mv"]

# 概念指数当日涨跌幅（同花顺指数，权威口径）
idx_pct = {}
for name, code in TAGS.items():
    f, items = fetch("ths_daily", {"ts_code": code, "trade_date": DATE})
    idx_pct[name] = float(items[0][f.index("pct_change")])
print("概念指数当日涨跌幅:", {k: round(v, 2) for k, v in idx_pct.items()})

mem = pd.read_csv(os.path.join(WORK, "concept_members.csv"), dtype={"股票代码": str})
sel["涨跌幅"] = sel.代码.map(pct_all)
sel["总市值"] = sel.代码.map(mv_all)

# 每个概念：全成分股市值加权贡献（百分点）= 市值权重 × 成分股涨跌幅
pp = {}          # (概念, 股票) -> 贡献百分点
for name in TAGS:
    sub = mem[mem.概念名称 == name].drop_duplicates("股票代码").copy()
    sub["mv"] = sub.股票代码.map(mv_all); sub["pc"] = sub.股票代码.map(pct_all)
    sub = sub.dropna(subset=["mv", "pc"])
    w = sub.mv / sub.mv.sum()
    pp[name] = pd.Series((w * sub.pc).values, index=sub.股票代码.values)   # 指数=股票代码
    print("%s 成分股 %d 只（有效 %d），市值加权贡献合计 %.4f pp"
          % (name, len(mem[mem.概念名称 == name].drop_duplicates("股票代码")), len(sub), pp[name].sum()))

# 连线口径：样本股在「该概念样本内成分」中的市值权重 × 涨跌幅 = 贡献 pp；粗细=贡献占比（绝对值）
edges = []
for name in TAGS:
    mems = [c for c in sel.代码 if c in pp[name].index]
    sub = sel[sel.代码.isin(mems)]
    w = sub.总市值 / sub.总市值.sum()
    cpp = (w * sub.涨跌幅).values
    denom = np.abs(cpp).sum()
    for c, v in zip(sub.代码.values, cpp):
        edges.append({"概念": name, "代码": c, "贡献pp": float(v),
                      "占比": float(abs(v) / denom) if denom else 0.0})
E = pd.DataFrame(edges)
print("\n样本股与三概念的连线条数:", len(E), "| 每概念:", E.groupby("概念").size().to_dict())
print("贡献占比区间: %.1f%%~%.1f%%" % (E.占比.min() * 100, E.占比.max() * 100))
sel = sel.merge(m[["代码"]].assign(序号=range(len(m))), on="代码", how="left")

# ---------- 绘图 ----------
W, H = 3200, 1800
fig = plt.figure(figsize=(32, 18), dpi=100)
ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
ax.add_patch(plt.Rectangle((0, 0), 1, 1, facecolor="white", edgecolor="none"))

C_UP, C_DN, C_CON = "#d33a3a", "#2e9b4f", "#ef8c22"
ax.text(0.5, 0.955, "谁在带谁？概念与个股的涨跌归因图", ha="center", va="center",
        fontsize=40, fontweight="bold", color=C_CON)
ax.text(0.5, 0.912, "数据日期 2026-10-30（最新交易日）｜圆与方块的大小按当日涨跌幅，方块再乘市值权重；连线粗细=该股对该板块当日涨跌的贡献（百分点）",
        ha="center", va="center", fontsize=15, color="#666666")

# 图例
lg = [("橙色圆=概念板块（大小=板块涨跌幅）", "circle"), ("红绿方块=个股（红涨绿跌，大小=涨跌幅×市值权重）", "square"),
      ("红色实线=正向拉动（个股上涨）", "red"), ("绿色虚线=负向拖累（个股下跌）", "dash"),
      ("线越粗=贡献百分点越大", "thick")]
x0, y0 = 0.022, 0.905
ax.add_patch(FancyBboxPatch((x0, y0 - 0.175), 0.245, 0.215, boxstyle="round,pad=0.006",
                            facecolor="#fafafa", edgecolor="#cfcfcf", linewidth=1.2))
for i, (txt, kind) in enumerate(lg):
    yy = y0 - i * 0.035 - 0.012
    if kind == "circle":
        ax.add_patch(Ellipse((x0 + 0.016, yy), 0.018, 0.018 * W / H, facecolor=C_CON, edgecolor="none"))
    elif kind == "square":
        ax.add_patch(FancyBboxPatch((x0 + 0.008, yy - 0.009), 0.017, 0.018, boxstyle="round,pad=0.002",
                                    facecolor=C_UP, edgecolor="none"))
    elif kind == "red":
        ax.add_line(Line2D([x0 + 0.006, x0 + 0.026], [yy, yy], color=C_UP, linewidth=2.6))
    elif kind == "dash":
        ax.add_line(Line2D([x0 + 0.006, x0 + 0.026], [yy, yy], color=C_DN, linewidth=2.0, linestyle=(0, (4, 3))))
    else:
        ax.add_line(Line2D([x0 + 0.006, x0 + 0.026], [yy, yy], color="#888888", linewidth=5.0))
    ax.text(x0 + 0.032, yy, txt, fontsize=13, va="center", color="#333333")

# 概念节点
con_y = {"国企改革": 0.700, "人工智能": 0.470, "机器人概念": 0.240}
rng = max(abs(v) for v in idx_pct.values())
con_xy = {}
for name in TAGS:
    y = con_y[name]; r = 0.026 + 0.024 * abs(idx_pct[name]) / rng   # 缩小半径，保证三个圆互不重叠
    con_xy[name] = (0.10, y, r)
    ax.add_patch(Ellipse((0.10, y), 2 * r, 2 * r * W / H, facecolor=C_CON, edgecolor="none", alpha=0.95))
    ax.text(0.10, y, name, ha="center", va="center",
            fontsize=20, fontweight="bold", color="white")
    ax.text(0.10, y - r - 0.022, "%+.2f%%" % idx_pct[name], ha="center", va="center",
            fontsize=17, fontweight="bold", color=(C_UP if idx_pct[name] > 0 else C_DN))

# 个股节点（两列）
order = sel.sort_values("涨跌幅", ascending=False).reset_index(drop=True)
ncol, per = 2, int(np.ceil(len(order) / 2))
colx = [0.60, 0.80]
wnorm = (order.总市值 / order.总市值.max())
strength = (order.涨跌幅.abs() / order.涨跌幅.abs().max()) * wnorm * 4 + 0.35
pos = {}
for i, r in order.iterrows():
    col, row = divmod(i, per)
    y = 0.855 - row * (0.755 / max(per - 1, 1))
    x = colx[col]
    side = 0.012 + 0.030 * min(strength[i], 1.6) / 1.6
    pos[r.代码] = (x, y, side)
    color = C_UP if r.涨跌幅 > 0 else C_DN
    ax.add_patch(FancyBboxPatch((x - side / 2, y - side / 2), side, side,
                                boxstyle="round,pad=0.002,rounding_size=0.006", facecolor=color,
                                edgecolor="none", alpha=0.95))
    ax.text(x, y, r.名称, ha="center", va="center", fontsize=11.5 if len(r.名称) <= 4 else 10,
            fontweight="bold", color="white")
    ax.text(x, y - side / 2 - 0.016, "%+.2f%%" % r.涨跌幅, ha="center", va="center",
            fontsize=11, color=("#b3261e" if r.涨跌幅 > 0 else "#1b7a3d"))

# 连线
for _, e in E.iterrows():
    cx, cy, cr = con_xy[e.概念]
    sx, sy, ss = pos[e.代码]
    lw = 0.8 + 7.5 * e.占比 / max(E.占比.max(), 1e-9)
    col = C_UP if e.贡献pp > 0 else C_DN
    ax.add_patch(FancyArrowPatch((cx + cr * 0.98, cy), (sx - ss / 2 - 0.004, sy),
                                 connectionstyle="arc3,rad=%.3f" % (0.07 * (1 if cy > sy else -1)),
                                 arrowstyle="-", linewidth=lw, color=col, alpha=0.5,
                                 linestyle="-" if e.贡献pp > 0 else (0, (5, 4)), zorder=2))

# 重点边标注（贡献绝对值最大的 5 条）
top = E.sort_values("占比", ascending=False).head(6)
for _, e in top.iterrows():
    cx, cy, cr = con_xy[e.概念]
    sx, sy, ss = pos[e.代码]
    tx = (cx + cr + sx - ss / 2) / 2
    ty = (cy + sy) / 2 + (0.02 if cy > sy else -0.02)
    ax.text(tx, ty, "%s %.0f%%" % ("贡献" if e.贡献pp > 0 else "拖累", e.占比 * 100),
            ha="center", va="center", fontsize=12, fontweight="bold",
            color=("#b3261e" if e.贡献pp > 0 else "#1b7a3d"),
            bbox=dict(facecolor="white", alpha=0.85, edgecolor="none", pad=1.2))

# 底部说明
ax.text(0.5, 0.028, "样本：上一轮「三标签系列」中出现买点的 %d 只个股（SMC 或缠论）；圆=同花顺概念指数当日涨跌幅；方块=个股当日涨跌幅×市值权重；连线粗细=该股在样本内该概念成分股市值加权涨跌中的占比" % len(sel),
        ha="center", va="center", fontsize=13, color="#777777")

# ---- 核验：图上文字是否覆盖全部标的/概念/图例，且标注框不重叠 ----
fig.canvas.draw(); rend = fig.canvas.get_renderer()
txts = [t.get_text() for t in ax.texts]
miss = []
for _, r in sel.iterrows():
    if r.名称 not in txts: miss.append(("名称", r.名称))
    if ("%+.2f%%" % r.涨跌幅) not in txts: miss.append(("个股涨幅", r.名称))
for k, v in idx_pct.items():
    if k not in txts: miss.append(("概念名", k))
    if ("%+.2f%%" % v) not in txts: miss.append(("概念涨幅", k))
for t, _ in lg:
    if t not in txts: miss.append(("图例", t))
boxes = [(t.get_text()[:14], t.get_window_extent(rend)) for t in ax.texts]
ov = []
for i in range(len(boxes)):
    for j in range(i + 1, len(boxes)):
        if boxes[i][1].overlaps(boxes[j][1]):
            ov.append((boxes[i][0], boxes[j][0]))
print("图上文字缺失项: %d %s" % (len(miss), miss[:6]))
print("文字框重叠对数: %d %s" % (len(ov), ov[:6]))
print("标的数=%d 连线数=%d 概念数=%d" % (len(sel), len(E), len(TAGS)))

out = os.path.join(WORK, "三标签概念个股归因图_截至20261030.png")
fig.savefig(out, facecolor="white")
print("saved", out, "%.0f KB" % (os.path.getsize(out) / 1024))
print("个股涨幅排序（前 6 与后 4）:")
print(order[["代码", "名称", "涨跌幅", "总市值"]].head(6).to_string(index=False))
print(order[["代码", "名称", "涨跌幅", "总市值"]].tail(4).to_string(index=False))
print("重点边:", [(r.概念, r.代码, round(r.贡献pp, 3)) for _, r in top.iterrows()])