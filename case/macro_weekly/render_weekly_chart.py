# -*- coding: utf-8 -*-
"""周报配图：库存周期位置 × 上下游摆动（四联图，PNG）。

画面（2×2）
1. 库存与需求：名义库存同比 / 实际库存同比（剔除 PPI）/ 营收同比，24 个月
2. 三个宽度：PPI−CPI、采掘−加工、出厂−购进（生产资料−PPIRM），零轴上下
3. 上下游期货代理指数：上游（煤焦钢有色）/ 中游（建材化工）/ CCPI，2026-06-01=100，26 周
4. 利润份额：上游 / 中游 / 下游 / 公用，近 12 个月

数据只读 data/edb_monthly.csv 与 data/edb_daily.csv（由 compute_edb.py 落盘），
不另行取数；缺列或空值就跳过该面板，不编造。
"""
import json
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib import font_manager  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
OUT_DIR = os.path.join(BASE, "out")
PNG = os.path.join(OUT_DIR, "库存周期与利润转移图表.png")

# DejaVu Sans 管 ASCII（数字/百分号/负号），Droid Sans Fallback 管中文，靠 matplotlib 的多字体 fallback
matplotlib.rcParams["font.family"] = ["DejaVu Sans", "Droid Sans Fallback"]
matplotlib.rcParams["axes.unicode_minus"] = False
matplotlib.rcParams["figure.facecolor"] = "white"

RED, GREEN, BLUE, GRAY, ORANGE = "#c0392b", "#1e8449", "#2e6da4", "#7f8c8d", "#d35400"

UP = ["CN.REBAR.FUT", "CN.FUT.JM", "CN.FUT.SC", "CN.FUT.CU", "CN.FUT.AL"]
MID = ["CN.FUT.FG", "CN.FUT.SA", "CN.FUT.TA", "CN.FUT.MA", "CN.FUT.EG"]


def load():
    m = pd.read_csv(os.path.join(DATA, "edb_monthly.csv"), index_col=0, parse_dates=True)
    d = pd.read_csv(os.path.join(DATA, "edb_daily.csv"), index_col=0, parse_dates=True)
    return m.sort_index(), d.sort_index()


def keys():
    p = os.path.join(DATA, "key_numbers.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    m, d = load()
    K = keys()
    fig, axes = plt.subplots(2, 2, figsize=(14.5, 10.2), dpi=110)
    fig.suptitle("库存周期位置 与 上下游摆动（周频跟踪）", fontsize=17, fontweight="bold", y=0.985)
    asof = K.get("last", "")
    fig.text(0.5, 0.948,
             "月度数据截至 %s；高频代理取最近 26 周。周中读数只作方向预判，正式结论以月度工企效益数据为准。"
             % (asof or "—"), ha="center", fontsize=10.5, color=GRAY)

    # 1) 库存与需求
    ax = axes[0][0]
    mm = m.tail(24)
    if {"CN.PROFIT.INV.YOY", "CN.PPI.YOY"} <= set(mm.columns):
        inv = mm["CN.PROFIT.INV.YOY"].astype(float)
        ppi = mm["CN.PPI.YOY"].astype(float)
        real = ((1 + inv / 100) / (1 + ppi / 100) - 1) * 100
        ax.plot(inv.index, inv.values, color=RED, lw=2.2, marker="o", ms=3, label="名义库存同比")
        ax.plot(real.index, real.values, color=BLUE, lw=2.0, marker="s", ms=3, label="实际库存同比(剔PPI)")
        if "CN.PROFIT.REVENUE.YOY" in mm.columns:
            rev = mm["CN.PROFIT.REVENUE.YOY"].astype(float)
            ax.plot(rev.index, rev.values, color=GREEN, lw=1.8, ls="--", marker="^", ms=3, label="营收累计同比")
        ax.axhline(0, color="#cccccc", lw=0.8)
        ax.set_title("① 库存与需求：%s，连续 %s 个月"
                     % (K.get("quad", "—"), int(K.get("quad_months") or 0)), fontsize=12.5, loc="left")
        ax.legend(fontsize=9, loc="best", framealpha=0.9)
        ax.grid(alpha=0.25)
        ax.set_ylabel("%")
    # 2) 三个宽度
    ax = axes[0][1]
    pairs = [("CN.PPI.YOY", "CN.CPI.YOY", "PPI−CPI", RED),
             ("CN.PPI.MINING.YOY", "CN.PPI.PROC.YOY", "采掘−加工", ORANGE),
             ("CN.PPI.PROD.YOY", "CN.PPIRM.YOY", "出厂−购进(生产资料)", BLUE)]
    plotted = []
    for a, b, lab, col in pairs:
        if {a, b} <= set(mm.columns):
            v = (mm[a].astype(float) - mm[b].astype(float)).dropna()
            if len(v):
                ax.plot(v.index, v.values, color=col, lw=2.0, marker="o", ms=2.5, label=lab)
                plotted.append(lab)
    ax.axhline(0, color="#999999", lw=0.9, ls=":")
    if plotted:
        ax.set_title("② 上下游价差宽度（走阔=向上）", fontsize=12.5, loc="left")
        ax.legend(fontsize=9, loc="best", framealpha=0.9)
    ax.grid(alpha=0.25)
    ax.set_ylabel("pp")

    # 3) 上下游期货代理指数
    ax = axes[1][0]
    if len(d) > 30 and all(c in d.columns for c in UP + MID):
        dd = d.copy()
        base_rows = dd.loc[dd.index >= "2026-06-01"]
        if len(base_rows) > 5:
            def idx(cols):
                parts = [base_rows[c].dropna() / base_rows[c].dropna().iloc[0] * 100 for c in cols
                         if c in base_rows.columns and base_rows[c].dropna().size]
                return pd.concat(parts, axis=1).mean(axis=1) if parts else None
            up, mid = idx(UP), idx(MID)
            w = pd.DataFrame({"up": up, "mid": mid}).resample("W-FRI").last().tail(26)
            w["spread"] = w["mid"] - w["up"]
            ax.plot(w.index, w["up"].values, color=RED, lw=2.0, label="上游(煤焦钢+有色)")
            ax.plot(w.index, w["mid"].values, color=BLUE, lw=2.0, label="中游(建材+化工)")
            ax.axhline(100, color="#bbbbbb", lw=0.8, ls="--")
            ax2 = ax.twinx()
            ax2.bar(w.index, w["spread"].values, width=4.0, color=GREEN, alpha=0.22, label="中游−上游")
            ax2.axhline(0, color=GREEN, lw=0.7, alpha=0.5)
            ax2.set_ylabel("spread（中−上）", fontsize=9, color=GREEN)
            ax2.tick_params(axis="y", labelsize=8, colors=GREEN)
            ax.set_title("③ 上下游价格代理（期货，2026-06-01=100）｜看价格强弱，不是利润", fontsize=12.5, loc="left")
            h1, l1 = ax.get_legend_handles_labels()
            h2, l2 = ax2.get_legend_handles_labels()
            ax.legend(h1 + h2, l1 + l2, fontsize=8.5, loc="lower left", framealpha=0.9)
    ax.grid(alpha=0.25)

    # 4) 利润：存量份额（线）+ 增量贡献（文字，谁拿走新增利润）
    ax = axes[1][1]
    groups = [("CN.PROFIT.SHARE.UP", "上游", RED, "CN.PROFIT.AMT.UP"),
              ("CN.PROFIT.SHARE.MID", "中游", BLUE, "CN.PROFIT.AMT.MID"),
              ("CN.PROFIT.SHARE.DOWN", "下游", GREEN, "CN.PROFIT.AMT.DOWN"),
              ("CN.PROFIT.SHARE.UTIL", "公用", GRAY, "CN.PROFIT.AMT.UTIL")]
    tail = m.tail(12)
    have = [(c, n, col, a) for c, n, col, a in groups if c in m.columns and m[c].notna().any()]
    for c, n, col, _a in have:
        v = tail[c].astype(float).dropna()
        if v.empty:
            continue
        d_pp = v.iloc[-1] - v.iloc[0]
        ax.plot(v.index, v.values, color=col, lw=2.0, marker="o", ms=3,
                label="%s %.1f%%（12个月 %+.1fpp）" % (n, float(v.iloc[-1]), float(d_pp)))

    # 增量贡献：今年同月累计额 − 去年同月累计额，占全部增量的比重
    contrib = {}
    if "CN.PROFIT.AMT" in m.columns and m["CN.PROFIT.AMT"].notna().any():
        cur = m["CN.PROFIT.AMT"].dropna().index[-1]
        base = [i for i in m.index if i.year == cur.year - 1 and i.month == cur.month]
        if base:
            b = base[0]
            tot = float(m.loc[cur, "CN.PROFIT.AMT"]) - float(m.loc[b, "CN.PROFIT.AMT"])
            if abs(tot) > 0:
                for _c, n, _col, amt in groups:
                    if amt in m.columns and pd.notna(m.loc[cur, amt]) and pd.notna(m.loc[b, amt]):
                        contrib[n] = (float(m.loc[cur, amt]) - float(m.loc[b, amt])) / tot * 100.0

    if have:
        ax.set_title("④ 利润：存量份额（线）+ 谁拿走新增（增量贡献）", fontsize=12.5, loc="left")
        ax.legend(fontsize=8.5, loc="lower left", framealpha=0.9)
        if contrib:
            txt = "增量贡献（%s vs %s 累计）：%s" % (
                cur.strftime("%Y-%m"), b.strftime("%Y-%m"),
                "｜".join("%s %+.0f%%" % (n, v) for n, v in contrib.items()))
            ax.text(0.015, 0.965, txt, transform=ax.transAxes, fontsize=8.6, color="#333",
                    va="top", ha="left",
                    bbox=dict(boxstyle="round,pad=0.35", fc="#f7f7f7", ec="#dddddd", lw=0.7))
    ax.grid(alpha=0.25)
    ax.set_ylabel("份额 %")

    fig.autofmt_xdate(rotation=20, ha="right")
    fig.tight_layout(rect=[0, 0, 1, 0.925])
    fig.savefig(PNG, bbox_inches="tight")
    plt.close(fig)
    print("written:", PNG, os.path.getsize(PNG), "bytes")
    return PNG


if __name__ == "__main__":
    main()
