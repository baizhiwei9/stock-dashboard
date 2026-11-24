# -*- coding: utf-8 -*-
"""周度报告出图：左=PEG 升序的合格股票条形图；右=概念聚类只数条形图（白底扁平、中文字体）。"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

WS = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5e6ceeb364ffeNZ3DY2SCHDLO20"
FONT = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/.fonts/NotoSansSC-Regular.ttf"
OUT = WS + "/weekly_out"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT + "/weekly_chart.png")
    ap.add_argument("--as-of", default="")
    a = ap.parse_args()
    as_of = a.as_of
    if not as_of:
        try:
            as_of = json.load(open(OUT + "/report_data.json", encoding="utf-8")).get("as_of", "")
        except Exception:
            as_of = ""
    q = pd.read_csv(OUT + "/qualified.csv", dtype={"ts_code": str}).sort_values("PEG")
    e = pd.read_csv(OUT + "/entities.csv")
    e = e[(e["合格股数"] >= 3) & (e["判定"] == "资金聚集")].sort_values("合格股数", ascending=False).head(16)
    if q.empty:
        print("[chart] 无合格股票，跳过出图"); return
    sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ea8aaca93ffeHyamlhKpZfCuW2/py_deps")
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import font_manager as fm
    import matplotlib.pyplot as plt
    fp = fm.FontProperties(fname=FONT)
    fig = plt.figure(figsize=(32, 16), dpi=100)
    fig.patch.set_facecolor("white")
    fig.text(0.5, 0.958, "MAD 均线指选股：合格股票（PEG 升序）与资金聚集概念", ha="center",
             fontproperties=fp, fontsize=30, color="#1A1A1A")
    fig.text(0.5, 0.928, "口径：MAD>0、MAD20>0、MA40 向上 → 行业前 50/概念全量 → 实体周等权>0 → 分子≥3 且资金聚集 → 缠论/SMC 买点 → 剔除宽基 → 一致预期 PEG≤1.5",
             ha="center", fontproperties=fp, fontsize=13, color="#555555")
    fig.text(0.5, 0.905, "数据截至 %s｜来源：本地前复权日线 + TuShare 一致预期 + iFind PE(TTM)｜来自 DoMate" % as_of,
             ha="center", fontproperties=fp, fontsize=13, color="#888888")
    ax1 = fig.add_axes([0.05, 0.08, 0.55, 0.79])
    y = np.arange(len(q))[::-1]
    ax1.barh(y, q.PEG.values, color="#E8842C", height=0.6)
    ax1.axvline(1.0, color="#E14B3B", lw=1.6, ls="--")
    ax1.set_yticks(y); ax1.set_yticklabels(q["名称"].tolist(), fontproperties=fp, fontsize=13)
    for i, (_, r) in enumerate(q.iterrows()):
        ax1.text(r.PEG + 0.02, y[i], "%.3f｜%s｜增速%.1f%%｜PE%.1f" % (r.PEG, r["命中"], r["增速2027"], r["PE_TTM"]),
                 va="center", fontproperties=fp, fontsize=12, color="#333333")
    ax1.set_xlabel("PEG（PE(TTM) ÷ 一致预期 2027 增速）", fontproperties=fp, fontsize=14)
    ax1.set_xlim(0, max(q.PEG.max() * 1.45, 1.6))
    for s in ("top", "right"): ax1.spines[s].set_visible(False)
    ax2 = fig.add_axes([0.68, 0.08, 0.30, 0.79])
    e = e.sort_values("合格股数")
    ax2.barh(np.arange(len(e)), e["合格股数"].values, color="#3E9B62", height=0.6)
    ax2.set_yticks(np.arange(len(e))); ax2.set_yticklabels(e["名称"].tolist(), fontproperties=fp, fontsize=13)
    for i, (_, r) in enumerate(e.iterrows()):
        ax2.text(r["合格股数"] + 0.1, i, "%d 只（周等权 %+.1f%%）" % (r["合格股数"], r["本周等权"] * 100),
                 va="center", fontproperties=fp, fontsize=12, color="#333333")
    ax2.set_xlabel("资金聚集概念下的合格股票数", fontproperties=fp, fontsize=14)
    ax2.set_xlim(0, e["合格股数"].max() * 1.5)
    for s in ("top", "right"): ax2.spines[s].set_visible(False)
    fig.savefig(a.out, dpi=100, facecolor="white")
    plt.close(fig)
    print("[chart] 已出图：%s（%d 只合格股票 / %d 个聚集概念）" % (a.out, len(q), len(e)))


if __name__ == "__main__":
    main()