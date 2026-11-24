#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ot_analyze.py — 最优传输·任意标的靠岸/漂移分析
================================================
输入任意指数 / ETF / 个股（或一组），用自校准 W1 判断它「是否跌到头 / 分布是否靠岸」，
并与基准（默认沪深300）做耦合对比。输出 Markdown + 图，可选推飞书。

用法：
    python ot_analyze.py --code 000300.SH
    python ot_analyze.py --code 510300 --code 588000 --code 600519.SH
    python ot_analyze.py --code 399006.SZ --push          # 推飞书
    python ot_analyze.py --code 512480.SH --kind etf --start 2025-06-01

口径：
    W1(t) = W1(最近 15 日收益分布, 再往前 250 日分布)，r_norm = r/σ20；
    分位 = 该 W1 序列的滚动 252 日分位。分位低=分布已回到自身常态（靠岸），
    分位高=仍在偏离（下跌/漂移未结束）。这是状态识别，不是买卖信号。
"""

from __future__ import annotations

import argparse
import os
from datetime import date

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

import ot_core as oc

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "output")
matplotlib.rcParams["font.sans-serif"] = ["Noto Sans SC", "Noto Sans CJK SC", "SimHei", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False


def plot_symbol(snap):
    w, q, c = snap["w1_series"], snap["pct_series"], snap["close_series"]
    fig, (ax0, ax1) = plt.subplots(2, 1, figsize=(11, 6.6), height_ratios=[2, 1], sharex=True, facecolor="white")
    for ax in (ax0, ax1):
        ax.set_facecolor("#f7f7f7")
        ax.grid(True, color="#d0d0d0", alpha=0.5, lw=0.6)
        for sp in ax.spines.values():
            sp.set_color("#d0d0d0")
    ax0.plot(c.index, c.values, color="#4e79a7", lw=1.4, label="复权收盘")
    ax0.plot(c.index, c.rolling(40).mean().values, color="#f28e2b", lw=0.9, ls="--", label="MA40")
    ax0.plot(c.index, c.rolling(160).mean().values, color="#e15759", lw=0.9, ls=":", label="MA160")
    ax0.set_ylabel("价格（前复权）")
    ax0.legend(loc="upper left", fontsize=8, framealpha=0.4)
    ax0.set_title(f"{snap['name']}（{snap['kind']}）｜{snap['as_of']}｜{snap['state']}", fontsize=12, fontweight="bold")

    ax1.plot(w.index, w.values, color="#59a14f", lw=1.4, label="W1（15 日 vs 前 250 日）")
    ax1b = ax1.twinx()
    ax1b.set_facecolor("none")
    ax1b.plot(q.index, q.values, color="#e15759", lw=1.1, alpha=0.8, label="W1 滚动 252 日分位")
    ax1b.axhline(40, color="#e15759", ls=":", lw=0.7, alpha=0.4)
    ax1b.axhline(60, color="#e15759", ls="--", lw=0.7, alpha=0.4)
    ax1b.axhline(80, color="#e15759", ls="-.", lw=0.7, alpha=0.4)
    ax1b.set_ylim(0, 105)
    ax1b.set_ylabel("分位 %（右轴）", color="#e15759", fontsize=9)
    ax1.set_ylabel("Wasserstein-1")
    ax1.legend(loc="upper left", fontsize=8, framealpha=0.4)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax1.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    fig.autofmt_xdate(rotation=0, ha="center")
    fig.tight_layout()
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"ot_symbol_{snap['name']}_{snap['as_of']}.png".replace("/", "_"))
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


def symbol_md(snap):
    b = snap.get("bench", {})
    L = []
    L.append(f"#### {snap['name']}（{snap['kind']}）｜{snap['as_of']}")
    L.append("")
    q = snap["w1_pct"]
    d20 = snap.get("ret20")
    d20 = d20 if d20 is not None and not np.isnan(d20) else 0.0
    if q < 40:
        mark = "🟢🌙"
    elif q < 60:
        mark = "🟡●"
    elif d20 > 0:
        mark = "🟢↗"      # 分位高但 20 日向上：上行偏离，不是下跌风险
    else:
        mark = "🔴⭐"
    L.append(f"**状态：{mark} {snap['state']}**　W1 = {snap['w1']:.4f}（分位 {q:.0f}%）"
             f"｜5 日 ΔW1 {snap['w1_chg5']:+.4f}")
    L.append("")
    L.append("| 指标 | 数值 | 指标 | 数值 |")
    L.append("|---|---|---|---|")
    L.append(f"| 距 250 日高点 | {snap['dd_long']:+.1f}% | 距 250 日低点 | {snap['pos_low']:+.1f}% |")
    L.append(f"| 5 日 | {snap['ret5']:+.2f}% | 20 日 | {snap['ret20']:+.2f}% |")
    L.append(f"| 80 日 | {snap['ret80']:+.2f}% | MA40 / MA160 | {'↑' if snap['ma40'] else '↓'} / {'↑' if snap['ma160'] else '↓'} |")
    if b and "pair_w1" in b:
        L.append(f"| 与{b['bench']}成对 W1 | {b['pair_w1']:.4f} | 近 60 日相关 | {b['corr60']:+.2f} |")
        L.append(f"| 下尾依赖 λ_L | {b['tail_l']:.2f} | 20 日相对强弱 | {b['rel20']:+.2f}pp |")
    elif b.get("error"):
        L.append(f"| 基准对比 | 不可用（{b['error']}） | | |")
    L.append("")
    return L


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", action="append", default=None, help="可多次指定：指数/ETF/个股代码")
    ap.add_argument("--kind", default=None, choices=["index", "etf", "stock"])
    ap.add_argument("--bench", default="000300.SH")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--no-fig", action="store_true")
    args = ap.parse_args()

    codes = args.code or ["000300.SH"]
    end = args.end or date.today().isoformat()
    start = args.start or (pd.Timestamp(end) - pd.Timedelta(days=900)).strftime("%Y-%m-%d")

    snaps, figs = [], []
    for code in codes:
        try:
            s = oc.analyze_symbol(code, start=start, end=end, kind=args.kind, bench=args.bench)
            snaps.append(s)
            if not args.no_fig:
                figs.append(plot_symbol(s))
        except Exception as e:
            print(f"[失败] {code}: {e}")

    if not snaps:
        print("无可用结果，退出")
        return

    L = [f"### 最优传输 · 标的靠岸分析｜{snaps[0]['as_of']}", ""]
    L.append("W1 = 最近 15 日收益分布 vs 前 250 日分布（波动归一）；分位 = W1 的滚动 252 日分位。"
             "分位 <40 视为已靠岸、40–60 靠岸中、>60 未靠岸；状态识别，不是买卖信号。")
    L.append("")
    for s in snaps:
        L += symbol_md(s)
    if figs:
        L.append("#### 图")
        L.append("")
        for p in figs:
            L.append(f"![{os.path.basename(p)}]({os.path.relpath(p, HERE)})")
        L.append("")
    md = "\n".join(L)
    path = os.path.join(HERE, f"OT标的分析_{snaps[0]['as_of']}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    print(md)
    print(f"\n[Markdown] {path}")
    for p in figs:
        print(f"[图] {p}")

    if args.push:
        import ot_render as odr
        import requests
        tok = odr.get_token()
        keys = []
        if tok:
            for p in figs:
                k = odr.upload(p, tok)
                if k:
                    keys.append(k)
        title = f"最优传输·标的靠岸分析 | {snaps[0]['as_of']} | 来自 DoMate"
        elements = [{"tag": "div", "text": {"tag": "lark_md", "content": md}}]
        for k in keys:
            elements.append({"tag": "img", "img_key": k, "alt": {"tag": "plain_text", "content": "图"}})
        card = {"config": {"wide_screen_mode": True},
                "header": {"title": {"tag": "plain_text", "content": title}, "template": "blue"},
                "elements": elements}
        r = requests.post(odr.WEBHOOK_URL, json={"msg_type": "interactive", "card": card}, timeout=20)
        print(f"[飞书] {r.json()}")


if __name__ == "__main__":
    main()