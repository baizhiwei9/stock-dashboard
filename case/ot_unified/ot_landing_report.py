#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ot_landing_report.py — 最优传输「靠岸」判断报告
================================================
基于 ot_core 的 W1 靠岸 + 耦合矩阵，回答「这几个指数跌到头了吗 / 跌完了吗」，
并给出大小盘分布对比。输出：
  - output/landing_monitor.png   归一化净值 + 滚动 W1 分位 + 状态背景
  - output/coupling.png          成对 W1 矩阵 + 谱共振/尾部依赖时序
  - OT靠岸判断_YYYY-MM-DD.html   自包含单文件报告（图片 base64 内嵌）
  - stdout 文本结论（供日报/飞书复用）

用法：
    python ot_landing_report.py [--date 2026-11-20] [--no-html]
"""

from __future__ import annotations

import argparse
import base64
import io
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

# 中文字体：本机实测 Noto Sans SC 可用
matplotlib.rcParams["font.sans-serif"] = ["Noto Sans SC", "Noto Sans CJK SC", "SimHei", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False

CAP_LABEL = {"big": "大盘", "mid": "中盘", "small": "小盘"}
CAP_COLOR = {"big": "#e15759", "mid": "#f28e2b", "small": "#4e79a7"}
STATE_COLORS = {
    "🟥 共振崩塌": "#e15759", "🟧 底部摩擦": "#f28e2b", "🟨 靠岸中": "#f9c74f",
    "ℹ️ 大盘先稳，小盘滞后": "#4e79a7", "🟩 企稳可起": "#59a14f", "⚪ 中性": "#9aa0a6",
}


def _apply_theme(fig, axs):
    for ax in axs:
        ax.set_facecolor("#f7f7f7")
        ax.grid(True, color="#d0d0d0", alpha=0.5, linewidth=0.6)
        for sp in ax.spines.values():
            sp.set_color("#d0d0d0")
    fig.patch.set_facecolor("white")


def _pil_font(size):
    """HTML/图片标题用 PIL 字体（避免 matplotlib 缺字时留空）。"""
    from PIL import ImageFont
    p = os.path.join(HERE, "SimHei.ttf")
    return ImageFont.truetype(p, size) if os.path.exists(p) else None


def plot_landing(snap, raw_close):
    """上图净值+回撤，下图 W1（线）+分位（虚）+状态色带。"""
    fig, (ax0, ax1) = plt.subplots(2, 1, figsize=(13, 8.5), height_ratios=[2, 1], sharex=True,
                                   facecolor="white")
    _apply_theme(fig, [ax0, ax1])

    for c in snap["codes"]:
        close = raw_close[c]
        nav = close / close.iloc[0]
        d = snap["detail"][c]
        ax0.plot(nav.index, nav.values, lw=1.3, color=CAP_COLOR[d["cap"]], label=f"{d['short']}")
    ax0.axhline(1.0, color="#666", lw=0.7, ls="--", alpha=0.5)
    ax0.set_ylabel("归一化净值（起点=1）")
    ax0.legend(loc="upper left", ncol=3, fontsize=8, framealpha=0.4)
    ax0.set_title(f"六指数净值与回撤（{snap['window']['steady']} 稳态模板）", fontsize=12, fontweight="bold")

    ax0b = ax0.twinx()
    ax0b.set_facecolor("none")
    for c in snap["codes"]:
        nav = raw_close[c] / raw_close[c].iloc[0]
        dd = nav / nav.cummax() - 1
        ax0b.fill_between(dd.index, dd.values * 100, 0, color=CAP_COLOR[snap["detail"][c]["cap"]],
                          alpha=0.08, lw=0)
    ax0b.set_ylabel("回撤 %（右轴）", color="#666", alpha=0.7)
    ax0b.tick_params(colors="#666", labelsize=8)

    # 状态色带
    states = snap["states"]
    start = None
    cur = None
    for dt_i, st in states.items():
        if st != cur:
            if cur is not None and cur in STATE_COLORS:
                ax1.axvspan(mdates.date2num(start), mdates.date2num(dt_i), color=STATE_COLORS[cur],
                            alpha=0.10, lw=0)
            start, cur = dt_i, st
    if cur in STATE_COLORS:
        ax1.axvspan(mdates.date2num(start), mdates.date2num(states.index[-1]),
                    color=STATE_COLORS[cur], alpha=0.10, lw=0)

    for c in snap["codes"]:
        d = snap["detail"][c]
        ax1.plot(snap["w_df"].index, snap["w_df"][c].values, lw=1.0,
                 color=CAP_COLOR[d["cap"]], alpha=0.8, label=f"{d['short']} W1")
        for lv, ls in zip((40, 60, 80), (":", "--", "-.")):
            ax1.axhline(snap["pct_lines"][c][lv], color=CAP_COLOR[d["cap"]], ls=ls, lw=0.6, alpha=0.18)
    if snap["joint"].notna().any():
        ax1.plot(snap["joint"].index, snap["joint"].values, color="#111", lw=2.0, label="联合 W1")
    ax1.set_ylabel("W1 距离（单位＝涨跌幅，不是跌幅）")
    ax1.set_title("靠岸距离：W1 ＝ 当前 15 日涨跌分布搬到稳态模板的最小成本；分位＝该距离在自身历史中的排名", fontsize=10)
    ax1.legend(loc="upper left", ncol=4, fontsize=8, framealpha=0.4)
    ax1.text(0.005, 0.02,
             "怎么读：W1 越小＝涨跌分布越接近常态（靠岸）；W1 分位低＝当前偏离比过去多数日子都小。\n"
             "它不是跌幅分位：科创50 跌 44% 但 W1 分位只有 10（跌透了、分布已稳），创业板跌 49% 分位却 77（还在剧烈漂移）。",
             transform=ax1.transAxes, fontsize=8.5, va="bottom", ha="left", color="#333333",
             bbox=dict(boxstyle="round,pad=0.35", fc="white", ec="#cccccc", alpha=0.85))
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax1.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    fig.autofmt_xdate(rotation=0, ha="center")
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "landing_monitor.png")
    with open(path, "wb") as f:
        f.write(buf.getvalue())
    return path, buf.getvalue()


def status_of(q, dir_mean=None):
    """分位 + 漂移方向 → (emoji, 图内形状, 文案, 颜色)。

    ⚠️ W1 是距离、没有方向：分位高既可能是“跌出来的偏离”（未跌完），
    也可能是“涨出来的偏离”（已经很强）。不区分方向会把银行、食品饮料这类
    上涨行业误标成“未靠岸/风险”。
      <40            → 已靠岸 🟢🌙
      40–60          → 靠岸中 🟡●
      >60 且 dir<0   → 未靠岸（下跌未结束）🔴⭐
      >60 且 dir>0   → 上行偏离（不是风险）🟢↗
    """
    if q < 40:
        return "🟢🌙", "moon", "已靠岸", "#2ecc71"
    if q < 60:
        return "🟡●", "dot", "靠岸中", "#f39c12"
    if dir_mean is not None and dir_mean > 0:
        return "🟢↗", "up", "上行偏离", "#59a14f"
    return "🔴⭐", "star", "未靠岸", "#e15759"


def plot_status_badges(snap, raw_close):
    """状态标记图：每指数一行，左=名称，中=W1 分位条，右=标记（绿●🌙 / 黄● / 红★）。"""
    rows = []
    for c in snap["codes"]:
        d = snap["detail"][c]
        low = raw_close[c].iloc[-oc.DD_LONG:].min() if len(raw_close[c]) >= 20 else raw_close[c].min()
        rows.append((d["name"], d["cap"], d["w1_pct"], (d["close"] / low - 1) * 100, d["w1_chg5"],
                     d.get("dir_mean")))
    rows.sort(key=lambda x: x[2])

    n = len(rows)
    fig, ax = plt.subplots(figsize=(11, 0.85 * n + 2.2), facecolor="white")
    _apply_theme(fig, [ax])
    ax.set_xlim(0, 118)
    ax.set_ylim(-0.8, n - 0.2)
    ax.invert_yaxis()
    ax.set_yticks([])
    ax.set_xticks([0, 20, 40, 60, 80, 100])
    ax.set_xlabel("W1 分位（越低越靠岸；<40 已靠岸、40–60 靠岸中、>60 看方向：上行=偏离非风险，下行=未跌完）")
    ax.set_title("靠岸状态：绿点/月牙＝已靠岸　黄点＝靠岸中　绿箭头＝上行偏离　黄星＝未靠岸·风险",
                 fontsize=12.5, fontweight="bold")
    for i, (name, cap, q, pos_low, dw, dmean) in enumerate(rows):
        emoji, sym, tag, color = status_of(q, dmean)
        ax.barh(i, q, height=0.52, color=color, alpha=0.85)
        ax.text(-2, i, name, ha="right", va="center", fontsize=12, fontweight="bold")
        ax.text(q + 1.5, i, f"{q:.0f}", va="center", fontsize=11, color=color, fontweight="bold")
        # 右侧标记：绿圆+偏移白圆＝月牙（靠岸）；绿上三角＝上行偏离；黄五角星＝未靠岸/风险
        if sym == "star":
            ax.scatter(110, i, marker="*", s=380, facecolor="#f1c40f", edgecolor="#e15759", lw=1.2, zorder=5)
        elif sym == "moon":
            ax.scatter(110, i, marker="o", s=230, facecolor="#2ecc71", edgecolor="#1e8449", lw=0.8, zorder=5)
            ax.scatter(112.6, i - 0.10, marker="o", s=165, facecolor="white", edgecolor="none", zorder=6, clip_on=True)
        elif sym == "up":
            ax.scatter(110, i, marker="^", s=210, facecolor="#59a14f", edgecolor="#2f6b2c", lw=0.8, zorder=5)
        else:
            ax.scatter(110, i, marker="o", s=200, facecolor="#f39c12", edgecolor="#b9770e", lw=1.0, zorder=5)
        ax.text(104, i, tag, ha="right", va="center", fontsize=9.5, color=color)
        ax.text(-2, i + 0.34, f"方向{dmean:+.3f}｜距低点{pos_low:+.1f}%｜5日ΔW1 {dw:+.3f}", ha="right",
                va="center", fontsize=8, color="#888888")
    fig.tight_layout()
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "landing_status.png")
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


def plot_industry_status(ind):
    """申万一级 31 个行业的靠岸状态标记图（按 W1 分位升序）。"""
    rows = ind["rows"]
    n = len(rows)
    fig, ax = plt.subplots(figsize=(11.5, 0.42 * n + 2.4), facecolor="white")
    _apply_theme(fig, [ax])
    ax.set_xlim(0, 118)
    ax.set_ylim(-0.8, n - 0.2)
    ax.invert_yaxis()
    ax.set_yticks([])
    ax.set_xticks([0, 20, 40, 60, 80, 100])
    ax.set_xlabel("W1 分位（<40 已靠岸、40–60 靠岸中、>60 看方向：上行=偏离非风险，下行=未跌完）")
    ax.set_title(f"申万一级行业靠岸（{n} 个，{ind['as_of']}）："
                 f"绿点/月牙＝已靠岸 {ind['n_low']}　黄点＝靠岸中 {ind['n_mid']}　"
                 f"绿箭头＝上行偏离 {ind.get('n_up', 0)}　黄星＝未靠岸 {ind.get('n_down', 0)}",
                 fontsize=11.5, fontweight="bold")
    for i, r in enumerate(rows):
        q = r["pct"]
        dmean = r.get("dir_mean")
        emoji, sym, tag, color = status_of(q, dmean)
        ax.barh(i, q, height=0.56, color=color, alpha=0.85)
        ax.text(-2, i, r["name"], ha="right", va="center", fontsize=10.5, fontweight="bold")
        ax.text(q + 1.5, i, f"{q:.0f}", va="center", fontsize=9.5, color=color, fontweight="bold")
        if sym == "star":
            ax.scatter(110, i, marker="*", s=260, facecolor="#f1c40f", edgecolor="#e15759", lw=1.0, zorder=5)
        elif sym == "moon":
            ax.scatter(110, i, marker="o", s=150, facecolor="#2ecc71", edgecolor="#1e8449", lw=0.7, zorder=5)
            ax.scatter(111.8, i - 0.09, marker="o", s=105, facecolor="white", edgecolor="none", zorder=6)
        elif sym == "up":
            ax.scatter(110, i, marker="^", s=150, facecolor="#59a14f", edgecolor="#2f6b2c", lw=0.7, zorder=5)
        else:
            ax.scatter(110, i, marker="o", s=140, facecolor="#f39c12", edgecolor="#b9770e", lw=0.9, zorder=5)
        ax.text(104, i, tag, ha="right", va="center", fontsize=8.5, color=color)
        ax.text(-2, i + 0.32, f"方向{dmean:+.2f}", ha="right", va="center", fontsize=7.5, color="#888888")
    fig.tight_layout()
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "industry_status.png")
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


def plot_coupling(coup):
    fig = plt.figure(figsize=(14, 8.6), facecolor="white")
    gs = fig.add_gridspec(2, 2, height_ratios=[1.05, 1])
    axD = fig.add_subplot(gs[0, 0])
    axL = fig.add_subplot(gs[0, 1])
    axR = fig.add_subplot(gs[1, :])
    _apply_theme(fig, [axD, axL, axR])

    for ax, M, title, cmap, vmin, vmax, fmt in (
        (axD, coup["D"], f"成对 W1 距离（{coup['as_of']}，窗={oc.WIN}）", "viridis", None, None, "{:.2f}"),
        (axL, coup["LL"], f"下尾依赖 λ_L（{coup['as_of']}，u={oc.TAIL_LEVEL:.2f}）", "Reds", 0, 1, "{:.2f}"),
    ):
        im = ax.imshow(M, cmap=cmap, vmin=vmin, vmax=vmax)
        n = M.shape[0]
        ax.set_xticks(range(n)); ax.set_yticks(range(n))
        ax.set_xticklabels(coup["labels"], fontsize=8, rotation=30)
        ax.set_yticklabels(coup["labels"], fontsize=8)
        for i in range(n):
            for j in range(n):
                ax.text(j, i, fmt.format(M[i, j]), ha="center", va="center", fontsize=7,
                        color="white" if cmap == "viridis" else "#333")
        ax.set_title(title, fontsize=10, fontweight="bold")
        fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)

    idx = coup["resonance"].index
    axR.plot(idx, coup["resonance"].values, color="#e15759", lw=1.8, label="谱共振 λmax/N（越接近1越抱团）")
    axR.plot(idx, coup["mean_lamL"].values, color="#4e79a7", lw=1.1, label="平均下尾依赖 λ_L（一起跌）")
    axR.plot(idx, coup["mean_lamU"].values, color="#59a14f", lw=1.0, ls=":", label="平均上尾依赖 λ_U（一起涨）")
    axR.axhline(1 / len(coup["codes"]), color="#666", lw=0.7, ls=":")
    for q, ls in zip((40, 60, 80), (":", "--", "-.")):
        axR.axhline(coup["res_q"][q], color="#999", ls=ls, lw=0.6, alpha=0.5)
    axR.set_ylim(0, 1.05)
    axR.set_title("谱共振与尾部依赖时序（红底=共振崩塌，绿底=企稳可起）", fontsize=10, fontweight="bold")
    axR.legend(loc="upper left", fontsize=8, framealpha=0.4)
    axR.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    axR.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    fig.autofmt_xdate(rotation=0, ha="center")
    fig.suptitle(f"六指数两两耦合：W1 距离 · 尾部依赖 · 谱共振（{coup['as_of']}）",
                 fontsize=12, fontweight="bold")
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "coupling.png")
    with open(path, "wb") as f:
        f.write(buf.getvalue())
    return path, buf.getvalue()


def _order_text(snap, raw_close):
    """靠岸顺序：按 W1 分位升序（越低越靠岸），带漂移方向与距 250 日低点。"""
    rows = []
    for c in snap["codes"]:
        d = snap["detail"][c]
        low = raw_close[c].iloc[-oc.DD_LONG:].min() if len(raw_close[c]) >= 20 else raw_close[c].min()
        rows.append((d["name"], d["cap"], d["w1_pct"], (d["close"] / low - 1) * 100, d["w1_chg5"],
                     d.get("dir_mean")))
    rows.sort(key=lambda x: x[2])
    return rows


def build_text(snap, coup, raw_close):
    lines = []
    lines.append(f"【靠岸判断】数据日 {snap['as_of']}｜状态 {snap['state']}（连续 {snap['state_streak']} 日）")
    lines.append(f"结论：{snap['verdict']}")
    lines.append("标记：🟢🌙=已靠岸（回到稳态）　🟡●=靠岸中　🔴⭐=未靠岸（偏离稳态）")
    lines.append("W1 是什么：W1＝当前 15 日涨跌分布搬到稳态模板所需的最小成本（单位仍是涨跌幅，衡量分布形状差异），"
                 "不是跌幅分位；W1 分位＝这个距离在自身历史中的百分位，低=分布已回常态（靠岸），高=仍在剧烈漂移。")
    lines.append(f"分层分位（市值口径）：大盘 {snap['big_pct']:.0f}｜中盘 {snap['mid_pct']:.0f}｜小盘 {snap['small_pct']:.0f}"
                 f"｜小盘-大盘 {snap['big_small_gap']:+.0f}")
    lines.append(f"联合 W1 {snap['joint_w1']:.3f}（分位 {snap['joint_pct']:.0f}%）｜"
                 f"谱共振 λmax/N {coup['resonance_now']:.3f}｜平均 λ_L {coup['lamL_now']:.3f}｜"
                 f"平均成对 W1 {coup['w1_now']:.3f}")
    lines.append("")
    lines.append("靠岸顺序（W1 分位升序，越低越靠岸）：")
    for name, cap, q, pos_low, dw, dmean in _order_text(snap, raw_close):
        tag, _, txt, _ = status_of(q, dmean)
        lines.append(f"  {tag} {name}: 分位 {q:.0f}（{CAP_LABEL[cap]}｜{txt}）｜方向 {dmean:+.3f}"
                     f"｜距低点 {pos_low:+.1f}%｜5日ΔW1 {dw:+.4f}")
    lines.append("")
    lines.append("指数明细（W1 分位低=收益分布已回到稳态，靠岸充分；回撤按近 250 日高点）：")
    for c in snap["codes"]:
        d = snap["detail"][c]
        low = raw_close[c].iloc[-oc.DD_LONG:].min() if len(raw_close[c]) >= 20 else raw_close[c].min()
        pos_low = (d["close"] / low - 1) * 100
        lines.append(f"  {CAP_LABEL[d['cap']]}·{d['name']}: W1={d['w1']:.4f} 分位={d['w1_pct']:.0f} "
                     f"5日ΔW1={d['w1_chg5']:+.4f} 回撤={d['dd_long']:+.1f}% 距低点={pos_low:+.1f}% "
                     f"20日={d['ret20']:+.2f}% MA40{'↑' if d['above_ma40'] else '↓'}")
    ind = snap.get("industry")
    if ind and ind["rows"]:
        rows_i = ind["rows"]
        up = [r for r in rows_i if r["pct"] >= 60 and (r.get("dir_mean") or 0) > 0]
        dn = [r for r in rows_i if r["pct"] >= 60 and (r.get("dir_mean") or 0) <= 0]
        lines.append("")
        lines.append(f"申万一级行业靠岸（{len(rows_i)} 个，{ind['as_of']}）：已靠岸 {ind['n_low']}"
                     f"｜靠岸中 {ind['n_mid']}｜上行偏离 {len(up)}｜下跌未靠岸 {len(dn)}"
                     f"｜中位分位 {ind['median_pct']:.0f}")
        lines.append("  已靠岸前 5：" + "、".join(f"{r['name']}({r['pct']:.0f})" for r in rows_i[:5]))
        lines.append("  下跌未靠岸前 5：" + "、".join(
            f"{r['name']}({r['pct']:.0f}，方向{r['dir_mean']:+.2f})" for r in dn[::-1][:5]))
        if up:
            lines.append("  上行偏离（涨出来的，不是下跌风险）：" + "、".join(
                f"{r['name']}({r['pct']:.0f}，方向{r['dir_mean']:+.2f})" for r in up[:6]))
    return "\n".join(lines)


def build_markdown(snap, coup, raw_close, figs):
    """Markdown 报告（本地文件 + 飞书文字底稿）。"""
    L = []
    L.append(f"### 最优传输·靠岸判断｜{snap['as_of']}")
    L.append("")
    L.append(f"**状态：{snap['state']}（连续 {snap['state_streak']} 日）→ {snap['verdict']}**")
    L.append("")
    L.append("| 维度 | 数值 |")
    L.append("|---|---|")
    L.append(f"| 大盘均值分位（上证50、沪深300） | {snap['big_pct']:.0f} |")
    L.append(f"| 中盘分位（中证500） | {snap['mid_pct']:.0f} |")
    L.append(f"| 小盘均值分位（中证1000、创业板、科创50） | {snap['small_pct']:.0f} |")
    L.append(f"| 小盘 − 大盘 | {snap['big_small_gap']:+.0f} |")
    L.append(f"| 联合 W1（分位） | {snap['joint_w1']:.3f}（{snap['joint_pct']:.0f}%） |")
    L.append(f"| 谱共振 λmax/N | {coup['resonance_now']:.3f} |")
    L.append(f"| 平均下尾依赖 λ_L | {coup['lamL_now']:.3f} |")
    L.append(f"| 平均成对 W1 | {coup['w1_now']:.3f} |")
    L.append("")
    L.append("#### 一、靠岸顺序与标记（W1 分位升序，越低越靠岸）")
    L.append("")
    L.append("标记规则：🟢🌙＝已靠岸（回到稳态）　🟡●＝靠岸中　🟢↗＝上行偏离（不是下跌风险）　🔴⭐＝未靠岸（偏离稳态）")
    L.append("")
    L.append("| 顺序 | 标记 | 指数 | 市值分层 | W1 分位 | 方向 | 判定 | 距250日低点 | 5日ΔW1 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for i, (name, cap, q, pos_low, dw, dmean) in enumerate(_order_text(snap, raw_close), 1):
        mark, _, tag, _ = status_of(q, dmean)
        L.append(f"| {i} | {mark} | {name} | {CAP_LABEL[cap]} | {q:.0f} | {dmean:+.3f} | {tag} | "
                 f"{pos_low:+.1f}% | {dw:+.4f} |")
    L.append("")
    L.append("#### 二、W1 到底是什么（不是跌幅分位）")
    L.append("")
    L.append("- **W1 ＝ 分布的搬运成本**：把「当前最近 15 个交易日的涨跌分布」搬到「稳态模板（2026-04~06）的涨跌分布」，"
             "最省力的搬法要花多少代价。单位还是涨跌幅，所以它衡量的是**分布形状有多不一样**，不是跌了多少。")
    L.append("- **W1 分位 ＝ 这个搬运成本在自身历史里的百分位**：分位 30 表示「当前偏离常态的程度，比过去 70% 的日子都小」→ 靠岸；"
             "分位 77 表示「比过去 77% 的日子都更异常」→ 还在剧烈漂移。")
    L.append("- **它不等于跌幅分位**：跌幅分位看价格跌了多少，W1 分位看涨跌的分布有多异常。同一轮下跌里，"
             "科创50 跌 44% 但 W1 分位只有 10（跌透了、分布已稳），创业板跌 49% 分位却是 77（仍在剧烈漂移）；"
             "上证50 只跌 12% 分位 35（没怎么跌、但分布也稳了）。")
    L.append("- **经验对应**：W1 分位 <40 且 5 日 ΔW1 <0 → 已靠岸；分位 <40 但 ΔW1 >0 → 靠岸后重新漂移（警惕二次下跌）；"
             "分位 >60 → 未靠岸。")
    L.append("")
    L.append("#### 三、六指数明细")
    L.append("")
    L.append("| 标记 | 指数 | W1 | 分位 | 5日ΔW1 | 回撤(250日) | 5日 | 20日 | MA40 | MA160 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for c in snap["codes"]:
        d = snap["detail"][c]
        L.append(f"| {status_of(d['w1_pct'])[0]} | {d['name']} | {d['w1']:.4f} | {d['w1_pct']:.0f} | {d['w1_chg5']:+.4f} | "
                 f"{d['dd_long']:+.1f}% | {d['ret5']:+.2f}% | {d['ret20']:+.2f}% | "
                 f"{'↑' if d['above_ma40'] else '↓'} | {'↑' if d['above_ma160'] else '↓'} |")
    L.append("")
    L.append("#### 四、怎么读这张表")
    L.append("")
    L.append("- W1 分位低＝当前 15 日收益分布已回到 2026-04~06 稳态模板附近，跌势的分布特征消失（靠岸）；"
             "分位高＝分布仍偏离，下跌/漂移未结束。")
    L.append("- 距 250 日低点 ≈0% 表示当日就在年内低位（仍在创新低）；回撤按 250 日最高收盘计。")
    L.append("- 市值分层：大盘＝上证50+沪深300；中盘＝中证500；小盘＝中证1000+创业板+科创50。"
             "科创50 是科创板内部市值前 50（板块内属大盘），按全市场风格归成长/中小盘，这里归小盘。")
    L.append("")
    L.append("#### 四、共振状态（原耦合图已删，只留一句话指标）")
    L.append("")
    L.append(f"- 六指数齐跌黏性（平均下尾依赖 λ_L）：**{coup['lamL_now']:.3f}**（越接近 1，越是一起跌；>0.6 视为抱团下跌未解除）。")
    L.append(f"- 抱团度（谱共振 λmax/N）：{coup['resonance_now']:.3f}（1.0＝完全同向，0.17＝各走各的）。")
    L.append("- 为什么删图：λmax/N 与 λ_L 在同一窗口内高度同向，两条曲线讲的是同一件事；单独一张图信息量低、"
             "且阶段阈值用的是全样本后验分位，不适合做成日常判读图。要看结构细节可跑 `--debug-coupling`。")
    L.append("")
    ind = snap.get("industry")
    if ind and ind["rows"]:
        L.append("#### 五、申万一级行业靠岸（全部 %d 个行业，口径同六指数）" % len(ind["rows"]))
        L.append("")
        L.append(f"已靠岸 **{ind['n_low']}** 个｜靠岸中 **{ind['n_mid']}** 个｜未靠岸 **{ind['n_high']}** 个｜"
                 f"中位分位 **{ind['median_pct']:.0f}**（数据日 {ind['as_of']}）")
        L.append("")
        L.append("| 标记 | 行业 | W1 分位 | 方向 | 判定 | 回撤(250日) | 距250日低点 | 20日 | MA40 |")
        L.append("|---|---|---|---|---|---|---|---|---|")
        for r in ind["rows"]:
            q = r["pct"]
            dmean = r.get("dir_mean")
            mark, _, tag, _ = status_of(q, dmean)
            L.append(f"| {mark} | {r['name']} | {q:.0f} | {dmean:+.3f} | {tag} | {r['dd']:+.1f}% | "
                     f"{r['pos_low']:+.1f}% | {r['ret20']:+.1f}% | {'↑' if r['above_ma40'] else '↓'} |")
        L.append("")
    if figs:
        L.append("#### 六、图")
        L.append("")
        for p in figs:
            rel = os.path.relpath(p, HERE)
            L.append(f"![{os.path.basename(p)}]({rel})")
        L.append("")
    L.append("#### 七、方法与局限")
    L.append("")
    L.append(f"- W1 = Wasserstein-1 距离；当前 {snap['window']['win']} 日分布（先做 r/σ20 波动归一）"
             f"与稳态模板 {snap['window']['steady']} 的搬运成本；联合 W1 用 POT 精确解。")
    L.append("- 状态识别，不是买卖信号；靠岸 ≠ 立即上涨，需与回撤、成交额、基差交叉看。")
    L.append("- 稳态模板为人工选定，换模板会改变分位绝对值；结论应按同一模板下的时间变化解读。")
    L.append("- 数据全部经 DataHub 获取。")
    L.append("")
    L.append(f"报告由 DoMate 生成 · 数据截至 {snap['as_of']}")
    md = "\n".join(L)
    path = os.path.join(HERE, f"OT靠岸判断_{snap['as_of']}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    return path, md


def build_html(snap, coup, raw_close, png_landing, png_coupling):
    b64a = base64.b64encode(png_landing).decode()
    b64b = base64.b64encode(png_coupling).decode()

    rows = []
    for c in snap["codes"]:
        d = snap["detail"][c]
        low = raw_close[c].iloc[-oc.DD_LONG:].min() if len(raw_close[c]) >= 20 else raw_close[c].min()
        pos_low = (d["close"] / low - 1) * 100
        color = CAP_COLOR[d["cap"]]
        q = d["w1_pct"]
        qtag = "已靠岸" if q < 40 else ("靠岸中" if q < 60 else "未靠岸")
        qcolor = "#59a14f" if q < 40 else ("#f28e2b" if q < 60 else "#e15759")
        rows.append(f"""<tr>
<td><span style="color:{color};font-weight:600">{CAP_LABEL[d['cap']]}</span>·{d['name']}</td>
<td>{d['w1']:.4f}</td>
<td><b style="color:{qcolor}">{q:.0f}</b> <span class="tag" style="background:{qcolor}22;color:{qcolor}">{qtag}</span></td>
<td>{d['w1_chg5']:+.4f}</td>
<td>{d['dd_long']:+.1f}%</td>
<td>{pos_low:+.1f}%</td>
<td>{d['ret5']:+.2f}%</td>
<td>{d['ret20']:+.2f}%</td>
<td>{'↑' if d['above_ma40'] else '↓'}</td>
<td>{'↑' if d['above_ma160'] else '↓'}</td>
</tr>""")

    caps = [("大盘（上证50、沪深300）", snap["big_pct"]), ("中盘（中证500）", snap["mid_pct"]),
            ("小盘（中证1000、创业板、科创50）", snap["small_pct"])]
    capbars = "".join(
        f"""<div class="barrow"><span class="barlabel">{name}</span>
<div class="bar"><i style="width:{max(0, min(100, v)) if not np.isnan(v) else 0}%;background:{'#e15759' if '大盘' in name else ('#f28e2b' if '中盘' in name else '#4e79a7')}"></i></div>
<b>{v:.0f}</b></div>""" for name, v in caps)

    phase_txt = {"red": "共振崩塌（红）", "green": "企稳可起（绿）", None: "中性"}.get(coup["phase_now"], "中性")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>最优传输·靠岸判断 {snap['as_of']}</title>
<style>
:root{{--ink:#1a1a1a;--sub:#5b6472;--line:#e3e7ee;--bg:#f5f7fa;--card:#fff;}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,"Noto Sans SC","PingFang SC","Microsoft YaHei",sans-serif;line-height:1.65}}
.wrap{{max-width:1180px;margin:0 auto;padding:26px 22px 60px}}
h1{{font-size:25px;margin:0 0 4px}}
.sub{{color:var(--sub);font-size:13px;margin-bottom:18px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px 22px;margin-bottom:18px}}
.hero{{border-left:5px solid #e15759}}
.kicker{{font-size:12px;color:var(--sub);letter-spacing:.06em}}
.verdict{{font-size:22px;font-weight:700;margin:6px 0 10px}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:12px}}
.kpi{{background:#f8fafc;border:1px solid var(--line);border-radius:9px;padding:12px 14px}}
.kpi .v{{font-size:20px;font-weight:700}}
.kpi .k{{font-size:12px;color:var(--sub)}}
h2{{font-size:16px;margin:0 0 12px;border-left:4px solid #4e79a7;padding-left:10px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{padding:8px 6px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}}
th:first-child,td:first-child{{text-align:left}}
th{{color:var(--sub);font-weight:600;background:#fafbfd}}
.tag{{display:inline-block;font-size:11px;padding:1px 7px;border-radius:10px}}
.barrow{{display:flex;align-items:center;gap:10px;margin:9px 0}}
.barlabel{{width:250px;font-size:13px;color:var(--sub)}}
.bar{{flex:1;height:14px;background:#eef1f6;border-radius:7px;overflow:hidden}}
.bar i{{display:block;height:100%;border-radius:7px}}
img{{width:100%;border-radius:9px;border:1px solid var(--line)}}
.note{{font-size:12px;color:var(--sub)}}
ul{{margin:8px 0 0 20px;padding:0}} li{{margin:5px 0}}
@media(max-width:820px){{.grid{{grid-template-columns:repeat(2,1fr)}}.barlabel{{width:150px}}table{{font-size:12px}}}}
</style></head><body><div class="wrap">
<h1>最优传输 · 靠岸判断（是否跌到头）</h1>
<div class="sub">数据日 {snap['as_of']}｜六指数：上证50 / 沪深300 / 中证500 / 中证1000 / 创业板指 / 科创50｜
稳态模板 {snap['window']['steady']}｜滚动窗 {snap['window']['win']} 日｜波动归一 {snap['window']['roll_std']} 日｜数据源 DataHub</div>

<div class="card hero">
<div class="kicker">当前状态（Max OT 六档）</div>
<div class="verdict">{snap['state']}　→　{snap['verdict']}</div>
<div class="note">判断口径：W1 分位低＝当前收益分布已回到稳态（靠岸充分）；分位高＝仍在偏离（下跌/漂移未结束）。
状态已连续 {snap['state_streak']} 个交易日。</div>
<div class="grid">
<div class="kpi"><div class="k">大盘均值分位</div><div class="v">{snap['big_pct']:.0f}</div></div>
<div class="kpi"><div class="k">小盘均值分位</div><div class="v">{snap['small_pct']:.0f}</div></div>
<div class="kpi"><div class="k">小盘 − 大盘</div><div class="v">{snap['big_small_gap']:+.0f}</div></div>
<div class="kpi"><div class="k">联合 W1（分位）</div><div class="v">{snap['joint_w1']:.3f}<span style="font-size:12px;color:var(--sub)"> ({snap['joint_pct']:.0f}%)</span></div></div>
<div class="kpi"><div class="k">谱共振 λmax/N</div><div class="v">{coup['resonance_now']:.3f}</div></div>
<div class="kpi"><div class="k">平均下尾依赖 λ_L</div><div class="v">{coup['lamL_now']:.3f}</div></div>
<div class="kpi"><div class="k">平均成对 W1</div><div class="v">{coup['w1_now']:.3f}</div></div>
<div class="kpi"><div class="k">耦合阶段</div><div class="v" style="font-size:16px">{phase_txt}</div></div>
</div>
</div>

<div class="card">
<h2>一、大小盘分布：谁先靠岸</h2>
{capbars}
<div class="note" style="margin-top:12px">分位为各指数 W1 相对自身历史的分位；数值越低＝越靠岸。
小盘 − 大盘 &gt; 20 表示小盘明显滞后（大盘先稳、小盘还在跌）；&lt; −20 表示小盘先靠岸。</div>
</div>

<div class="card">
<h2>二、六指数明细</h2>
<table><thead><tr><th>指数</th><th>W1</th><th>W1 分位</th><th>5日ΔW1</th><th>回撤(250日)</th><th>距250日低点</th><th>5日</th><th>20日</th><th>MA40</th><th>MA160</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
<div class="note" style="margin-top:10px">距 250 日低点 ≈0% 表示当日就在年内低位（仍在创新低）；回撤按 250 日最高收盘计算。</div>
</div>

<div class="card">
<h2>三、靠岸监测图（净值 / 回撤 / W1 / 状态）</h2>
<img src="data:image/png;base64,{b64a}" alt="靠岸监测图">
</div>

<div class="card">
<h2>四、两两耦合：是否还在共振下跌</h2>
<img src="data:image/png;base64,{b64b}" alt="耦合矩阵">
<div class="note" style="margin-top:10px">λ_L 趋近 1＝齐跌黏性强；λmax/N 越接近 1＝六指数越抱团。二者同时处于高位是「共振崩塌」，同时低位是「企稳可起」。</div>
</div>

<div class="card">
<h2>五、方法与局限</h2>
<ul>
<li>W1 = Wasserstein-1 距离：当前 {snap['window']['win']} 日收益分布（先做波动归一 r/σ20）与稳态模板分布之间的搬运成本，一维用精确解、六维联合用 POT 精确解。</li>
<li>状态识别，不是买卖信号：必须与回撤、成交额、基差交叉看；靠岸≠立即上涨，只代表「下跌的分布特征已回到常态」。</li>
<li>稳态模板为人工选定窗口，换模板会改变分位绝对值；本报告在文末标注模板，结论应按「同一模板下的时间变化」解读。</li>
<li>数据全部经 DataHub（datahub-multi-tier/data_hub）获取，指数日线无成交额字段时以成交量代填留档。</li>
</ul>
</div>
<div class="note">报告由 DoMate 生成 · 数据截至 {snap['as_of']}</div>
</div></body></html>"""
    path = os.path.join(HERE, f"OT靠岸判断_{snap['as_of']}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="截止日期（默认今天）")
    ap.add_argument("--start", default=None)
    ap.add_argument("--no-html", action="store_true")
    ap.add_argument("--push", action="store_true", help="推送飞书卡片（含配图）")
    args = ap.parse_args()

    snap = oc.build_landing_snapshot(start=args.start, end=args.date)
    coup = oc.build_coupling_snapshot(start=args.start, end=args.date)
    # 申万一级 31 个行业靠岸（口径与六指数一致）；失败不阻断主报告
    try:
        snap["industry"] = oc.build_industry_landing(start=args.start, end=args.date)
    except Exception as e:
        snap["industry"] = None
        print(f"[行业] 取数失败，报告不含行业章节：{e}")
    raw = oc.get_index_batch(snap["codes"], args.start or "2025-01-01", args.date or date.today().isoformat())
    raw_close = {c: raw[c]["close"] for c in snap["codes"]}

    p1, b1 = plot_landing(snap, raw_close)
    p2, b2 = plot_coupling(coup)          # 保留给 HTML 用；不再进报告图集（用户看不出意义）
    p3 = plot_status_badges(snap, raw_close)
    figs = [p1, p3]
    if snap.get("industry"):
        figs.append(plot_industry_status(snap["industry"]))
    for extra in ("ot_flow_wide.png", "ot_flow_ind.png"):
        p = os.path.join(HERE, extra)
        if os.path.exists(p):
            figs.append(p)
    text = build_text(snap, coup, raw_close)
    md_path, md = build_markdown(snap, coup, raw_close, figs)
    print(text)
    print(f"\n[图] {p1}\n[图] {p2}\n[Markdown] {md_path}")
    if not args.no_html:
        html = build_html(snap, coup, raw_close, b1, b2)
        print(f"[报告] {html}")

    if args.push:
        _push_feishu(snap, coup, text, figs)


def _push_feishu(snap, coup, text, figs):
    """飞书卡片：结论加粗 + column_set 原生表格（标红/黄星/加粗）+ 配图。"""
    import ot_render as odr
    import requests

    rows = sorted([(snap["detail"][c]["name"], snap["detail"][c]["cap"], snap["detail"][c]["w1_pct"],
                    0.0, snap["detail"][c]["w1_chg5"]) for c in snap["codes"]], key=lambda x: x[2])
    dd_map = {snap["detail"][c]["name"]: snap["detail"][c]["dd_long"] for c in snap["codes"]}

    def cell(txt, weight=1):
        return {"tag": "column", "width": "weighted", "weight": weight,
                "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": txt}}]}

    head = {"tag": "column_set", "flex_mode": "none", "background_style": "grey",
            "columns": [cell("**标记**", 1), cell("**指数**", 2), cell("**W1分位**", 1),
                        cell("**判定**", 1), cell("**回撤250日**", 1)]}
    table = [head]
    for name, cap, q, pos_low, dw in rows:
        mark, _, tag, _ = status_of(q)
        bold_tag = f"**{tag}**" if q >= 60 else tag
        table.append({"tag": "column_set", "flex_mode": "none",
                      "columns": [cell(mark, 1), cell(f"**{name}**", 2), cell(f"{q:.0f}", 1),
                                  cell(bold_tag, 1), cell(f"{dd_map.get(name, float('nan')):+.1f}%", 1)]})

    ind = snap.get("industry")
    ind_line = (f"申万一级 **{len(ind['rows'])}** 个行业：已靠岸 **{ind['n_low']}**｜靠岸中 {ind['n_mid']}"
                f"｜未靠岸 **{ind['n_high']}**｜中位分位 {ind['median_pct']:.0f}") \
        if ind and ind.get("rows") else ""
    head_lines = [
        f"**{snap['state']}　→　{snap['verdict']}**",
        f"大小盘分位：大盘 **{snap['big_pct']:.0f}**｜中盘 {snap['mid_pct']:.0f}｜小盘 **{snap['small_pct']:.0f}**"
        f"（小盘−大盘 {snap['big_small_gap']:+.0f}）",
        f"联合 W1 {snap['joint_w1']:.3f}（分位 {snap['joint_pct']:.0f}%）｜六指数齐跌黏性 λ_L **{coup['lamL_now']:.3f}**",
        ind_line,
        "标记：🟢🌙 已靠岸（回到稳态）　🟡● 靠岸中　🔴⭐ 未靠岸（偏离稳态）",
        "**W1 是什么**：W1＝当前 15 日涨跌分布搬到稳态模板的最小成本（单位是涨跌幅，衡量分布形状差异），"
        "**不是跌幅分位**；分位＝该距离在自身历史中的排名，低=分布已回常态（靠岸），高=仍在漂移。",
    ]
    head_lines = [x for x in head_lines if x]
    elements = [{"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(head_lines)}}]
    elements += table
    elements.append({"tag": "hr"})
    elements.append({"tag": "div", "text": {"tag": "lark_md",
                                            "content": "靠岸是状态识别，不是买卖信号；需与回撤、成交额、基差交叉看。"}})

    tok = odr.get_token()
    keys = []
    if tok:
        for p in figs:
            k = odr.upload(p, tok)
            if k:
                keys.append(k)
    for k in keys:
        elements.append({"tag": "img", "img_key": k, "alt": {"tag": "plain_text", "content": "图"}})
    title = f"最优传输·靠岸判断 | {snap['as_of']} | {snap['state']} | 来自 DoMate"
    card = {"config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": title}, "template": "blue"},
            "elements": elements}
    r = requests.post(odr.WEBHOOK_URL, json={"msg_type": "interactive", "card": card}, timeout=25)
    print(f"[飞书] {r.json()}")


if __name__ == "__main__":
    main()