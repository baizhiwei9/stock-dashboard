# -*- coding: utf-8 -*-
"""报告层：纯文本 / markdown 报告 + matplotlib 持仓状态图。

结论口径必须保持原周报的诚实版本：
  · COT104 ≥0.90 做多 / ≤0.10 做空，目标窗口 4~8 周；
  · 单周胜率只有 50~58%，4 周才到 ~77%（历史事件去重 74 次）；
  · 国内盘只对贵金属有效（沪银 > 沪金），沪铜、玉米别套。
不美化，不写「高胜率」「稳定 alpha」这类词。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from . import signals as SG
from .engine import CommodityState, CftcConfig
from .universe import Commodity

FONT_CANDIDATES = ["Noto Sans CJK SC", "Noto Sans CJK JP", "Source Han Sans SC",
                   "WenQuanYi Zen Hei", "WenQuanYi Micro Hei", "SimHei", "DejaVu Sans"]


# ----------------------------------------------------------------------
def _pct(v, nd=1) -> str:
    try:
        v = float(v)
        return "—" if not np.isfinite(v) else f"{v*100:+.{nd}f}%"
    except (TypeError, ValueError):
        return "—"


def _cwidth(s: str) -> int:
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in s)


def _pad(s: str, w: int) -> str:
    return s + " " * max(0, w - _cwidth(s))


def _last_week(states: Sequence[CommodityState]) -> Optional[pd.Timestamp]:
    dts = [st.episode.last_date for st in states if st.episode.last_date is not None]
    return max(dts) if dts else None


# ----------------------------------------------------------------------
# 文本报告
# ----------------------------------------------------------------------
def build_text(states: Sequence[CommodityState], cfg: CftcConfig) -> str:
    last = _last_week(states)
    L: List[str] = []
    L.append(f"CFTC COT 极值 · 持仓状态周报   数据截至 {last.strftime('%Y-%m-%d') if last else '—'} 持仓周")
    L.append("=" * 78)
    L.append("COT 数字 = 管理基金净持仓在过去 %d 周（约两年）的百分位：0=两年最少，1=两年最多" % cfg.lookback_weeks)
    L.append(f"规则：≥{cfg.hi:.2f} 做多 / ≤{cfg.lo:.2f} 做空 / 中间观望；目标持有窗口 {cfg.win_min}~{cfg.win_max} 周")
    L.append("")
    for st in states:
        ep, vd = st.episode, st.verdict
        nm = _pad(st.commodity.name_cn, 13)
        if not np.isfinite(ep.cot):
            L.append(f"{nm}数据不可用  {st.warning}")
            continue
        bar = ""
        if ep.dir != 0:
            n = min(ep.held, 10)
            bar = " 第" + str(ep.held) + "周 " + "█" * n + "░" * (10 - n)
        else:
            bar = " 无仓位"
        L.append(f"{nm}{vd.action:<4} COT {ep.cot:.2f} = 两年{ep.cot*100:.0f}%分位{bar}")
        L.append(f"{'':13}{vd.advice}")
        if st.warning:
            L.append(f"{'':13}[warn] {st.warning}")
    L.append("")
    L.append("-" * 78)
    L.append(conclusion_line(states, cfg))
    L.append("单周胜率仅 50~58%，4 周才到 ~77% —— 波段/头寸级信号，别按周盯盘、别当日内用。")
    if any(st.key == "crude" for st in states):
        L.append("原油诚实口径：4 周命中 73%(+213bp) 看着不错，但单周只有 46%(-124bp)、8 周仅 38%，")
        L.append("            n=26 且多腿仅 5 个 —— 不显著；原油极值多是趋势，别照搬金属仓位。")
    L.append("国内盘只对贵金属有效（沪银 > 沪金），沪铜、玉米、原油别套（数据源："
             + _source_note(states) + "）")
    return "\n".join(L)


def _source_note(states: Sequence[CommodityState]) -> str:
    srcs = sorted({st.price_source for st in states if st.price_source})
    return "/".join(srcs) if srcs else "—"


def conclusion_line(states: Sequence[CommodityState], cfg: CftcConfig) -> str:
    actives = [st for st in states if st.episode.dir != 0]
    if not actives:
        return "● 当前全部品种处于观望区，无中期方向信号。"
    parts = []
    for st in actives:
        parts.append(f"{st.commodity.name_cn}{st.verdict.action}（第{st.episode.held}周，"
                     f"价格区间{_pct(st.episode.pnl_pct)}，"
                     f"按方向{_pct(st.episode.pnl_pct * st.episode.dir)}）")
    return "● " + "；".join(parts) + "。"


# ----------------------------------------------------------------------
# Markdown
# ----------------------------------------------------------------------
def build_markdown(states: Sequence[CommodityState], cfg: CftcConfig,
                   bt: Optional[dict] = None) -> str:
    last = _last_week(states)
    L: List[str] = []
    L.append(f"# CFTC COT 极值 · 持仓状态周报（{last.strftime('%Y-%m-%d') if last else '—'} 持仓周）")
    L.append("")
    L.append(f"- 数据：CFTC Disaggregated COT，管理基金（Managed Money）持仓，"
             f"报告日周二 / 发布周五 15:30 ET（北京周六 03:30）")
    L.append(f"- 口径：COT = 净多单在过去 **{cfg.lookback_weeks} 周**的 min-max 分位；"
             f"**≥{cfg.hi:.2f} 做多 / ≤{cfg.lo:.2f} 做空 / 其余观望**；"
             f"目标持有窗口 **{cfg.win_min}~{cfg.win_max} 周**")
    L.append(f"- 价格：外盘 `akshare.futures_foreign_hist`（DataHub 无外盘，**已知例外**）"
             f"，对齐={cfg.foreign_align}；国内主力优先 DataHub，回退新浪主力连续，对齐={cfg.domestic_align}")
    L.append("")
    L.append("## 当前状态")
    L.append("")
    L.append("| 品种 | COT 分位 | 两年分位 | 方向 | 持仓第几周 | 区间盈亏(按方向) | 距止盈线 | 结论 |")
    L.append("|---|---:|---:|---|---:|---:|---:|---|")
    for st in states:
        ep, vd = st.episode, st.verdict
        if not np.isfinite(ep.cot):
            L.append(f"| {st.commodity.name_cn} | — | — | 观望 | — | — | — | 数据不可用 |")
            continue
        remain = cfg.win_max - ep.held if ep.dir != 0 else None
        L.append(
            f"| {st.commodity.name_cn} | {ep.cot:.2f} | {ep.cot*100:.0f}% | {vd.action} | "
            f"{ep.held if ep.dir != 0 else '—'} | "
            f"{_pct(ep.pnl_pct * ep.dir) if ep.dir != 0 else '—'} | "
            f"{f'剩 {remain} 周' if remain is not None and remain > 0 else (f'超期 {abs(remain)} 周' if remain is not None else '—')} | "
            f"{vd.advice} |")
    L.append("")
    L.append("## 结论")
    L.append("")
    L.append("**" + conclusion_line(states, cfg) + "**")
    L.append("")
    L.append("> 单周胜率只有 **50~58%**，目标窗口 4 周才到约 **77%**（2020-2026 共 74 次极值事件，按事件去重）。"
             "这是波段/头寸级信号，**不要按周盯盘、不要当日内信号用**。")
    L.append(">")
    L.append("> **国内盘只对贵金属有效**（原周报结论：沪银约 62%、沪金约 59%），"
             "沪铜、玉米、原油别套。加 `--backtest` 会给出按本仓库口径重算的国内腿胜率表 —— "
             "**重算值和历史数字不一致时以重算表为准**。")
    if any(st.key == "crude" for st in states):
        L.append(">")
        L.append("> **原油（WTI）额外诚实口径**：2022 年中 CFTC 合约更名"
                 "（`CRUDE OIL, LIGHT SWEET` → `WTI-PHYSICAL`，同一合约代码 067651），"
                 "数据已按代码兜底 + 按报告日去重拼接。历史 26 个极值事件里 4 周命中 73%、"
                 "均值 +213bp，但**单周胜率只有 46%（均值 −124bp）、8 周仅 38%**，"
                 "多腿样本只有 5 个 —— 原油 COT 极值多数是趋势延续/趋势起点被反复打脸，"
                 "**别照搬金属的仓位和持有节奏**。")
    L.append("")

    if bt:
        L.extend(_backtest_markdown(bt, cfg))

    L.append("---")
    L.append("")
    L.append(f"生成时间 {datetime.now().strftime('%Y-%m-%d %H:%M')}；"
             "纯规则计算（0 token），不含主观调整。")
    return "\n".join(L)


def _backtest_markdown(bt: dict, cfg: CftcConfig) -> List[str]:
    L: List[str] = ["## 事件胜率（极值 episode 去重）", ""]
    hit = bt.get("hit") or {}
    horizons = sorted({h for d in hit.values() for h in d})
    if hit and horizons:
        L.append("| 品种 | 事件数 | " + " | ".join(f"{h} 周胜率 | {h} 周均值" for h in horizons) + " |")
        L.append("|---|---:|" + "---:|" * (2 * len(horizons)))
        for key, d in hit.items():
            n = max((d[h]["n"] for h in horizons), default=0)
            cells = []
            for h in horizons:
                v = d.get(h, {})
                hitv = v.get("hit", float("nan"))
                mev = v.get("mean_bp", float("nan"))
                cells.append(f"{'—' if not np.isfinite(hitv) else f'{hitv:.0%}'}")
                cells.append(f"{'—' if not np.isfinite(mev) else f'{mev:+.0f}bp'}")
            L.append(f"| {key} | {n} | " + " | ".join(cells) + " |")
        L.append("")
    else:
        L.append("_事件样本不足，跳过。_")
        L.append("")

    dom = bt.get("domestic") or {}
    if dom:
        L.append("## 国内传导核对（内外盘相关性 + 国内腿 1/4/8 周胜率）")
        L.append("")
        L.append("| 品种 | 国内数据源 | 样本周 | 内外盘相关性 | 4 周胜率（多/空） | 8 周胜率（多/空） | 可用性 |")
        L.append("|---|---|---:|---:|---:|---:|---|")
        for key, d in dom.items():
            if "error" in d:
                L.append(f"| {key} | {d.get('source','—')} | — | — | — | — | — | {d['error']} |")
                continue
            corr = d.get("link_corr", float("nan"))
            hits = d.get("hit") or {}

            bd = d.get("hit_by_dir") or {}

            def _h(h):
                v = (bd.get("1") or {}).get(str(h), {})
                w = (bd.get("-1") or {}).get(str(h), {})
                hl, hs_ = v.get("hit", float("nan")), w.get("hit", float("nan"))
                f = lambda x: "—" if not np.isfinite(x) else f"{x:.0%}"   # noqa: E731
                return f"{f(hl)}/{f(hs_)}"

            L.append(
                f"| {key} | {d.get('source','—')} | {d.get('n', 0)} | "
                f"{'—' if not np.isfinite(corr) else f'{corr:+.2f}'} | "
                f"{_h(4)} | {_h(8)} | "
                f"{'联动弱，别套' if d.get('weak_link') else '可参考'} |")
        L.append("")
        if "crude" in dom:
            L.append("> ⚠️ **原油的国内腿（上海原油 SC）别用**：内外盘相关性虽高（约 0.7），"
                     "但按 COT 方向做国内腿的多腿 4/8 周胜率只有 **27%**（做空腿 48%/49%），"
                     "方向信号传不过去 —— 相关性高只说明价格同涨跌，不等于信号有效。")
            L.append(">")
        L.append("> 相关性 < 0.30 判为「联动弱，仅国际可用」。原周报（更早样本）给的是"
                 "「沪银约 62%、沪金约 59%」，**本表是重算值，两者不一致时以本表为准**，"
                 "别拿旧数字直接下单；国内腿用的是不复权主力连续，换月跳空会污染胜率。")
        L.append("")

    q = bt.get("q") or {}
    if q:
        L.append("## Q 五口径稳健性（周频 IC / 净值）")
        L.append("")
        L.append("| 品种 | 口径 | IC_rev | IC_mom | 反转净值 | 动量净值 |")
        L.append("|---|---|---:|---:|---:|---:|")
        for key, d in q.items():
            for qc, v in d.items():
                L.append(f"| {key} | {qc} | {v['ic_rev']:.3f} | {v['ic_mom']:.3f} | "
                         f"{v['rev']['nav']:.2f} | {v['mom']['nav']:.2f} |")
        L.append("")
        L.append("> Q 口径的 IC 只有 0.0x 级别，扣成本后不可交易；本节仅作稳健性对照，"
                 "策略本体仍是 COT 分位极值 + 持仓时钟。")
        L.append("")
    return L


# ----------------------------------------------------------------------
# 图
# ----------------------------------------------------------------------
def _setup_font() -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    names = {f.name for f in font_manager.fontManager.ttflist}
    for cand in FONT_CANDIDATES:
        if cand in names:
            plt.rcParams["font.sans-serif"] = [cand]
            plt.rcParams["axes.unicode_minus"] = False
            return cand
    plt.rcParams["axes.unicode_minus"] = False
    return ""


def grid_layout(n: int) -> dict:
    """按品种数算图幅与网格：≤4 用经典 2×2，≥5 用 2×3（首行 3 个）。

    返回 dict（fig_w / fig_h / ncols / nrows / pw / ph / xc / rows_y / clock / concl）。
    布局参数集中在这里，report 里不再硬编码品种顺序或个数。
    """
    if n <= 4:
        ncols, nrows, fig_w, fig_h = 2, max(1, (n + 1) // 2), 22.0, 18.0
        pw, gapx, x0, x1 = 0.415, 0.065, 0.055, 0.950
        chart_top, chart_bottom, gapy = 0.880, 0.415, 0.022
        clock, concl = (0.055, 0.215, 0.895, 0.145), (0.055, 0.015, 0.895, 0.175)
    else:
        ncols, nrows, fig_w, fig_h = 3, (n + 2) // 3, 26.0, 21.0
        pw, gapx, x0, x1 = 0.300, 0.021, 0.048, 0.960
        # gapy 要放得下上一行的 x 轴标签 + 下一行的标题（实测约需 0.025，留 0.046 余量）
        chart_top, chart_bottom, gapy = 0.885, 0.355, 0.046
        clock, concl = (0.052, 0.180, 0.900, 0.145), (0.052, 0.010, 0.900, 0.155)

    ph = (chart_top - chart_bottom - gapy * (nrows - 1)) / nrows
    xc = [x0 + c * (pw + gapx) for c in range(ncols)]
    rows_y = [chart_top - ph - r * (ph + gapy) for r in range(nrows)]
    return dict(fig_w=fig_w, fig_h=fig_h, ncols=ncols, nrows=nrows, pw=pw, ph=ph,
                xc=xc, rows_y=rows_y, clock=clock, concl=concl)


def make_chart(states: Sequence[CommodityState], cfg: CftcConfig,
               out_png: Optional[Path] = None) -> Path:
    """各品种 COT 走势（网格自适应）+ 持仓时钟 + 结论汇总。

    品种数由 `states`（= 配置 universe.commodities 顺序）决定，
    5 个品种走 2×3 网格（首行 3 个、次行 2 个），不裁切、不重叠。
    """
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    font = _setup_font()
    usable = [st for st in states if st.panel is not None and not st.panel.empty]
    if not usable:
        raise RuntimeError("无可绘制品种（全部数据不可用）")

    order = usable
    n = len(order)
    lay = grid_layout(n)
    fig = plt.figure(figsize=(lay["fig_w"], lay["fig_h"]), dpi=100)
    last = _last_week(order)
    PW, PH = lay["pw"], lay["ph"]
    XC, YC = lay["xc"], lay["rows_y"]
    ncols = lay["ncols"]

    for idx, st in enumerate(order):
        r, c = divmod(idx, ncols)
        ax = fig.add_axes([XC[c], YC[r], PW, PH])
        p = st.panel.dropna(subset=["cot"]).iloc[-120:]
        if p.empty:
            continue
        x = np.arange(len(p))
        ep, vd = st.episode, st.verdict

        ax.axhspan(cfg.hi, 1.02, color=SG.COLOR_LONG, alpha=0.10)
        ax.axhspan(-0.02, cfg.lo, color=SG.COLOR_SHORT, alpha=0.10)
        ax.axhline(cfg.hi, color=SG.COLOR_LONG, lw=1.3, ls="--", alpha=0.75)
        ax.axhline(cfg.lo, color=SG.COLOR_SHORT, lw=1.3, ls="--", alpha=0.75)
        ax.axhline(0.5, color="#bdc3c7", lw=0.9, ls=":")

        ax.plot(x, p["cot"].values, color="#2c3e50", lw=2.2, zorder=4)
        ax.fill_between(x, 0, 1, where=(p["cot"].values >= cfg.hi),
                        color=SG.COLOR_LONG, alpha=0.35, zorder=3)
        ax.fill_between(x, 0, 1, where=(p["cot"].values <= cfg.lo),
                        color=SG.COLOR_SHORT, alpha=0.35, zorder=3)
        ax.scatter([x[-1]], [p["cot"].iloc[-1]], s=170, color=vd.color,
                   edgecolor="white", linewidth=2.2, zorder=6)

        ax.set_ylim(-0.05, 1.05)
        ax.set_xlim(-1, len(p) + 1)
        ax.set_yticks([0, 0.1, 0.5, 0.9, 1.0])
        if c == 0:
            # 只有每行第一列写长标签，否则 3 列布局下标签会压到左邻图里
            ax.set_yticklabels(["0.00 净多两年最少", "0.10 做空线", "0.50 两年中位",
                                "0.90 做多线", "1.00 净多两年最多"], fontsize=8.8)
        else:
            ax.set_yticklabels(["0.00", "0.10", "0.50", "0.90", "1.00"], fontsize=8.8)
        for lb, cl in zip(ax.get_yticklabels(),
                          [SG.COLOR_SHORT, SG.COLOR_SHORT, "#333", SG.COLOR_LONG, SG.COLOR_LONG]):
            lb.set_color(cl)
            if cl != "#333":
                lb.set_fontweight("bold")
        ticks = list(range(0, len(p), 26))
        ax.set_xticks(ticks)
        ax.set_xticklabels([p.index[i].strftime("%y-%m") for i in ticks], fontsize=9)
        ax.grid(alpha=0.22, ls="--")
        if c == 0:
            ax.set_ylabel(f"COT 分位（过去{cfg.lookback_weeks}周）", fontsize=9.5)
        ax.set_title(f"{st.commodity.name_cn}   COT {ep.cot:.2f}（两年{ep.cot*100:.0f}%分位）   →  {vd.action}",
                     fontsize=14.5, pad=9, color=vd.color, fontweight="bold")

        box = []
        if ep.dir != 0:
            box.append(f"持仓第 {ep.held} 周")
            box.append(f"入场 {ep.start.strftime('%Y-%m-%d')}" if ep.start is not None else "入场 —")
            box.append(f"区间 {ep.pnl_pct*100:+.1f}%")
        else:
            box.append("无信号 · 观望")
            box.append(f"距做多 {max(0.0, cfg.hi-ep.cot):.2f}")
            box.append(f"距做空 {max(0.0, ep.cot-cfg.lo):.2f}")
        ax.text(0.012, 0.965, "\n".join(box), transform=ax.transAxes, fontsize=11,
                va="top", ha="left", color="#2c3e50", linespacing=1.5,
                bbox=dict(boxstyle="round,pad=0.42", fc="white", ec=vd.color, lw=1.6, alpha=0.94))

    # 持仓时钟
    ax3 = fig.add_axes(list(lay["clock"]))
    ax3.set_xlim(0, 12.8)
    ax3.set_ylim(-0.7, n - 0.3)
    ax3.invert_yaxis()
    ax3.axis("off")
    ax3.set_title(f"持仓时钟 —— 现在走到第几周（目标窗口 {cfg.win_min}~{cfg.win_max} 周）",
                  fontsize=15, pad=12, fontweight="bold")
    ax3.add_patch(Rectangle((0, -0.6), cfg.win_min, n, color="#3498db", alpha=0.09))
    ax3.add_patch(Rectangle((cfg.win_min, -0.6), cfg.win_max - cfg.win_min, n,
                            color="#27ae60", alpha=0.13))
    ax3.add_patch(Rectangle((cfg.win_max, -0.6), 2.4, n, color="#e67e22", alpha=0.09))
    for xv, lb in [(cfg.win_min, f"{cfg.win_min}周 目标起点"), (cfg.win_max, f"{cfg.win_max}周 止盈线")]:
        ax3.axvline(xv, color="#34495e", lw=1.6, ls="--", alpha=0.65)
        ax3.text(xv, -0.42, lb, fontsize=10.5, ha="center", color="#34495e")

    for i, st in enumerate(order):
        ep, vd = st.episode, st.verdict
        y = i
        ax3.text(0, y - 0.30, st.commodity.name_cn, fontsize=13.5, fontweight="bold", va="center")
        if ep.dir == 0:
            ax3.plot([0, 10], [y, y], color="#bdc3c7", lw=7, solid_capstyle="round", alpha=0.55)
            ax3.text(0.15, y + 0.02, "  观望 · 无仓位", fontsize=12, va="center", color=SG.COLOR_FLAT)
            ax3.text(10.2, y, f"COT {ep.cot:.2f}", fontsize=12, va="center", color=SG.COLOR_FLAT)
        else:
            cut = min(ep.held, 10)
            ax3.plot([0, cut], [y, y], color=vd.color, lw=11, solid_capstyle="round", zorder=4)
            ax3.plot([0, 10], [y, y], color="#ecf0f1", lw=11, solid_capstyle="round", zorder=2)
            ax3.plot([0, cut], [y, y], color=vd.color, lw=11, solid_capstyle="round", zorder=4)
            ax3.scatter([cut], [y], s=260, color=vd.color, edgecolor="white", lw=2.4, zorder=6)
            ax3.text(cut, y - 0.28, f"第{ep.held}周", fontsize=11.5, ha="center",
                     color=vd.color, fontweight="bold")
            remain = cfg.win_max - ep.held
            rt = f"剩 {remain} 周窗口" if remain > 0 else f"超期 {abs(remain)} 周"
            ax3.text(10.2, y, f"{vd.action}  ·  {rt}", fontsize=12, va="center",
                     color=vd.color, fontweight="bold")

    # 结论
    ax4 = fig.add_axes(list(lay["concl"]))
    ax4.axis("off")
    lines: List[str] = []
    actives = [st for st in order if st.episode.dir != 0]
    for st in actives:
        lines.append(f"● {st.commodity.name_cn}：{st.verdict.action}，{st.verdict.advice}")
    if not actives:
        lines.append("● 当前全部品种处于观望区，无中期方向信号。")
    idle = [st for st in order if st.episode.dir == 0]
    if idle:
        lines.append("○ 观望中：" + "、".join(f"{st.commodity.name_cn}({st.episode.cot:.2f})" for st in idle))

    y = 0.97
    step = min(0.155, 0.86 / max(1, min(len(lines), 8)))
    for ln in lines[:8]:
        ax4.text(0.008, y, ln, fontsize=12.8, va="top", color="#2c3e50")
        y -= step
    ax4.text(0.008, 0.02,
             f"注意：COT 数字 = 管理基金净多单在过去 {cfg.lookback_weeks} 周（约2年）的百分位："
             "0=两年最少、1=两年最多、0.5=中位。\n"
             "注意：单周胜率仅 50~58%，4 周才到 ~77% —— 波段/头寸级信号，别按周盯盘、别当日内用。\n"
             "注意：国内只对贵金属有效（沪银/沪金），沪铜/玉米/原油别套。\n"
             "注意：原油 COT 单周胜率仅 46%、8 周 38%（n=26），极值多是趋势，别照搬金属仓位。",
             fontsize=11.5, va="bottom", color="#7f8c8d", linespacing=1.7)

    fig.text(0.5, 0.972,
             f"CFTC COT 极值 · 持仓状态周报    数据截至 {last.strftime('%Y-%m-%d') if last else '—'} 持仓周",
             fontsize=18.5, fontweight="bold", ha="center", va="bottom")
    if not font:
        fig.text(0.5, 0.955, "(中文字体缺失，中文可能显示为方块)", fontsize=10,
                 ha="center", color="#c0392b")

    out_png = Path(out_png) if out_png else (cfg.fig_dir / "cot_status.png")
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=100, facecolor="white")
    plt.close(fig)
    return out_png


def write_reports(states: Sequence[CommodityState], cfg: CftcConfig, bt: Optional[dict] = None,
                 asof_tag: Optional[str] = None, chart: bool = True) -> Dict[str, Path]:
    """落盘 md / txt / png（+ 稳定名 cot_status.png）。"""
    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    tag = asof_tag or datetime.now().strftime("%Y%m%d")
    outs: Dict[str, Path] = {}

    md = build_markdown(states, cfg, bt=bt)
    txt = build_text(states, cfg)
    p_md = cfg.out_dir / f"cftc_cot_{tag}.md"
    p_txt = cfg.out_dir / f"cftc_cot_{tag}.txt"
    p_md.write_text(md.rstrip() + "\n", encoding="utf-8")
    p_txt.write_text(txt.rstrip() + "\n", encoding="utf-8")
    outs["markdown"] = p_md
    outs["text"] = p_txt

    if chart:
        png = make_chart(states, cfg, cfg.fig_dir / f"cot_status_{tag}.png")
        stable = cfg.fig_dir / "cot_status.png"
        try:
            stable.write_bytes(png.read_bytes())
            outs["figure"] = png
            outs["figure_stable"] = stable
        except OSError:
            outs["figure"] = png
    return outs
