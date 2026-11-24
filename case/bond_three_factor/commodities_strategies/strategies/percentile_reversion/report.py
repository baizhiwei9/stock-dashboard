# -*- coding: utf-8 -*-
"""生成价差分位回归回测 HTML 报告（内联 CSS + 内联 SVG，无外部依赖）。"""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import yaml

from ...config import ROOT

OUT = ROOT / "data" / "percentile_reversion"
CFG = ROOT / "config" / "percentile_reversion.yaml"
SECTOR_LABELS = {"nonferrous": "有色", "agri": "农产品", "black": "黑色",
                 "precious_observation": "贵金属观察", "out_of_scope": "范围外对照"}
COLORS = {"三板块合并": "#1f3a5f", "有色": "#8e44ad", "农产品": "#d68910",
          "黑色": "#2c3e50", "贵金属观察": "#7f8c8d"}


def pct(v) -> str:
    try:
        v = float(v)
        return "—" if not np.isfinite(v) else f"{v:.2%}"
    except (TypeError, ValueError):
        return "—"


def num(v, d=2) -> str:
    try:
        v = float(v)
        return "—" if not np.isfinite(v) else f"{v:,.{d}f}"
    except (TypeError, ValueError):
        return "—"


def esc(s) -> str:
    return html.escape(str(s))


def line_chart(series: Dict[str, pd.Series], title: str, width: int = 1040,
               height: int = 330, y_fmt: str = "pct", zero_line: bool = False) -> str:
    series = {k: v.dropna() for k, v in series.items() if len(v.dropna())}
    if not series:
        return ""
    idx = sorted(set().union(*[set(v.index) for v in series.values()]))
    pos = {d: i for i, d in enumerate(idx)}
    vals = np.concatenate([v.to_numpy(float) for v in series.values()])
    lo, hi = float(np.min(vals)), float(np.max(vals))
    if zero_line:
        lo, hi = min(lo, 0.0), max(hi, 0.0)
    pad = (hi - lo) * 0.08 or abs(hi) * 0.1 or 1.0
    lo, hi = lo - pad, hi + pad
    left, right, top, bottom = 74, 22, 46, 42
    w, h = width - left - right, height - top - bottom

    def x(i): return left + (i / max(len(idx) - 1, 1)) * w
    def y(v): return top + (hi - v) / (hi - lo) * h
    def f(v): return f"{v:.1%}" if y_fmt == "pct" else f"{v:,.2f}"

    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{esc(title)}">',
             f'<text x="{left}" y="24" class="svg-title">{esc(title)}</text>']
    for k in range(5):
        yy = top + k * h / 4
        v = hi - k * (hi - lo) / 4
        parts.append(f'<line x1="{left}" y1="{yy:.1f}" x2="{left+w}" y2="{yy:.1f}" class="grid"/>')
        parts.append(f'<text x="{left-10}" y="{yy+4:.1f}" class="axis" text-anchor="end">{f(v)}</text>')
    if zero_line and lo < 0 < hi:
        parts.append(f'<line x1="{left}" y1="{y(0):.1f}" x2="{left+w}" y2="{y(0):.1f}" class="zero"/>')
    for k in range(5):
        i = round(k * (len(idx) - 1) / 4)
        parts.append(f'<text x="{x(i):.1f}" y="{height-14}" class="axis" text-anchor="middle">{idx[i].strftime("%Y-%m")}</text>')
    for n, (label, s) in enumerate(series.items()):
        pts = [(x(pos[d]), y(float(v))) for d, v in s.items()]
        d = "M" + " L".join(f"{px:.1f},{py:.1f}" for px, py in pts)
        parts.append(f'<path d="{d}" fill="none" stroke="{COLORS.get(label, "#333")}" stroke-width="2" class="line"/>')
        lx = left + 8 + n * 150
        parts.append(f'<line x1="{lx}" y1="{top-18}" x2="{lx+18}" y2="{top-18}" stroke="{COLORS.get(label, "#333")}" stroke-width="3"/>')
        parts.append(f'<text x="{lx+24}" y="{top-13}" class="legend">{esc(label)}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def drawdown_chart(series: pd.Series, title: str, width: int = 1040, height: int = 220) -> str:
    s = series.dropna()
    if s.empty:
        return ""
    lo, hi = float(s.min()), 0.0
    pad = (hi - lo) * 0.1 or 0.01
    lo -= pad
    left, right, top, bottom = 74, 22, 40, 36
    w, h = width - left - right, height - top - bottom

    def x(i): return left + (i / max(len(s) - 1, 1)) * w
    def y(v): return top + (hi - v) / (hi - lo) * h

    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{esc(title)}">',
             f'<text x="{left}" y="22" class="svg-title">{esc(title)}</text>']
    for k in range(3):
        yy = top + k * h / 2
        v = hi - k * (hi - lo) / 2
        parts.append(f'<line x1="{left}" y1="{yy:.1f}" x2="{left+w}" y2="{yy:.1f}" class="grid"/>')
        parts.append(f'<text x="{left-10}" y="{yy+4:.1f}" class="axis" text-anchor="end">{v:.1%}</text>')
    pts = [(x(i), y(float(v))) for i, v in enumerate(s.to_numpy())]
    d = "M" + f"{left},{top} L" + " L".join(f"{px:.1f},{py:.1f}" for px, py in pts) + f" L{left+w},{top} Z"
    parts.append(f'<path d="{d}" fill="rgba(192,57,43,.18)" stroke="#c0392b" stroke-width="1.5"/>')
    for k in range(5):
        i = round(k * (len(s) - 1) / 4)
        parts.append(f'<text x="{x(i):.1f}" y="{height-12}" class="axis" text-anchor="middle">{s.index[i].strftime("%Y-%m")}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def hbar_chart(items: List[Tuple[str, float]], title: str, width: int = 1040,
               row_h: int = 30, value_fmt: str = "num") -> str:
    if not items:
        return ""
    height = 54 + row_h * len(items)
    left, right, top = 230, 90, 38
    w = width - left - right
    maxabs = max(abs(v) for _, v in items) or 1.0
    zero = left + w / 2

    def x(v): return zero + (v / maxabs) * (w / 2 - 6)

    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{esc(title)}">',
             f'<text x="20" y="24" class="svg-title">{esc(title)}</text>',
             f'<line x1="{zero}" y1="{top-8}" x2="{zero}" y2="{height-10}" class="grid"/>']
    for i, (label, v) in enumerate(items):
        yy = top + i * row_h
        color = "#c0392b" if v >= 0 else "#27ae60"
        x0, x1 = sorted([zero, x(v)])
        parts.append(f'<text x="{left-10}" y="{yy+16}" class="axis" text-anchor="end">{esc(label)}</text>')
        parts.append(f'<rect x="{x0:.1f}" y="{yy+2}" width="{max(x1-x0,1):.1f}" height="{row_h-8}" fill="{color}" opacity=".88"/>')
        txt = f"{v:.1%}" if value_fmt == "pct" else f"{v:,.0f}"
        parts.append(f'<text x="{x(v) + (6 if v>=0 else -6):.1f}" y="{yy+16}" class="axis" text-anchor="{"start" if v>=0 else "end"}">{txt}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def table(df: pd.DataFrame, cols: List[str], headers: List[str], classes: str = "") -> str:
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = []
    for _, r in df.iterrows():
        cells = []
        for c in cols:
            v = r.get(c, "")
            cls = ""
            if isinstance(v, (int, float, np.floating)) and not isinstance(v, bool):
                if "cagr" in c or "ret" in c or "pnl" in c or "mdd" in c or "win" in c:
                    cls = ' class="pos"' if v > 0 else (' class="neg"' if v < 0 else "")
            cells.append(f"<td{cls}>{v}</td>")
        body.append(f"<tr>{''.join(cells)}</tr>")
    return f'<table class="{classes}"><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>'


def main() -> int:
    import json, sqlite3
    cfg = yaml.safe_load(CFG.read_text(encoding="utf-8"))
    u = cfg["universe"]
    main_ids = u["black"] + u["agri"]
    rolling_ids = u.get("rolling", [])
    metrics = pd.read_csv(OUT / "metrics_all.csv")
    spread = pd.read_csv(OUT / "spread_metrics_w2y_95_adf_hurst.csv")
    trades = pd.read_csv(OUT / "trades_w2y_95_adf_hurst.csv")
    diag = json.load(open(OUT / "diagnosis_summary.json", encoding="utf-8"))
    diag_ext = pd.read_csv(OUT / "diagnosis_extremes.csv")

    def m(variant, group):
        row = metrics[(metrics.variant == variant) & (metrics.group == group)]
        return row.iloc[0] if len(row) else None

    def nav(panel, ids):
        cols = [c for c in ids if c in panel.columns]
        r = panel[cols].mean(axis=1).fillna(0.0)
        return (1 + r).cumprod()

    panels = {}
    for name, fname in [("2年 95/5 双验证", "w2y_95_adf_hurst"),
                        ("2年 90/10 双验证", "w2y_90_adf_hurst")]:
        panels[name] = pd.read_csv(OUT / f"panel_{fname}.csv", index_col=0, parse_dates=True)

    default_1y = m("w1y_90_adf_hurst", "两板块合并")
    default_roll = m("w1y_90_adf_hurst", "滚动期限差")
    main95 = m("w2y_95_adf_hurst", "两板块合并")
    main90 = m("w2y_90_adf_hurst", "两板块合并")
    roll95 = m("w2y_95_adf_hurst", "滚动期限差")
    roll90 = m("w2y_90_adf_hurst", "滚动期限差")
    noma = m("w2y_95_no_ma", "两板块合并")
    ma40 = m("w2y_95_ma40", "两板块合并")

    nav_main = nav(panels["2年 95/5 双验证"], main_ids)
    nav_roll = nav(panels["2年 95/5 双验证"], rolling_ids)
    nav_roll90 = nav(panels["2年 90/10 双验证"], rolling_ids)
    dd_roll = nav_roll / nav_roll.cummax() - 1

    # 固定 1-5 vs 滚动 1-6 逐品种
    pairs = [("RB", "RB_01_05", "RB_1_6_ROLL", "螺纹"),
             ("M", "M_01_05", "M_1_6_ROLL", "豆粕"),
             ("Y", "Y_01_05", "Y_1_6_ROLL", "豆油")]
    c = sqlite3.connect(ROOT / "data" / "commodities_strategies.db")
    roll_counts = dict(c.execute(
        "SELECT spread_id, SUM(roll_flag) FROM spread_daily WHERE spread_id IN ('RB_01_05','M_01_05','Y_01_05','RB_1_6_ROLL','M_1_6_ROLL','Y_1_6_ROLL') GROUP BY spread_id").fetchall())
    fixed_rows = []
    for code, old, new, label in pairs:
        a = spread[spread.spread_id == old].iloc[0]
        b = spread[spread.spread_id == new].iloc[0]
        fixed_rows.append(f"<tr><td>{label}</td><td>{pct(a['cagr'])}</td><td>{pct(b['cagr'])}</td>"
                          f"<td>{num(a['sharpe'])}</td><td>{num(b['sharpe'])}</td>"
                          f"<td>{pct(a['mdd'])}</td><td>{pct(b['mdd'])}</td>"
                          f"<td>{int(a['n_trades'])}</td><td>{int(b['n_trades'])}</td>"
                          f"<td>{int(roll_counts.get(old,0))}</td><td>{int(roll_counts.get(new,0))}</td></tr>")
    fixed_table = ("<table><thead><tr><th>品种</th><th>固定1-5年化</th><th>滚动1-6年化</th>"
                   "<th>固定Sharpe</th><th>滚动Sharpe</th><th>固定回撤</th><th>滚动回撤</th>"
                   "<th>固定交易</th><th>滚动交易</th><th>固定换月</th><th>滚动换月</th></tr></thead><tbody>"
                   + "".join(fixed_rows) + "</tbody></table>")

    # 滚动逐价差
    rolling = spread[spread.sector == "滚动期限差"].sort_values("cagr", ascending=False)
    rolling_rows = []
    pnl = trades.groupby("spread_id")["pnl"].sum()
    for _, r in rolling.iterrows():
        rolling_rows.append(f"<tr><td>{esc(r['name'])}</td><td>{int(r['n_trades'])}</td>"
                            f"<td>{int(r['entry_signals'])}</td><td>{int(r['filter_rejected'])}</td>"
                            f"<td>{pct(r['cagr'])}</td><td>{num(r['sharpe'])}</td><td>{pct(r['mdd'])}</td>"
                            f"<td class={'pos' if pnl.get(r['spread_id'],0)>0 else 'neg'}>{pnl.get(r['spread_id'],0):,.0f}</td></tr>")
    rolling_table = ("<table><thead><tr><th>滚动价差</th><th>交易数</th><th>触发</th><th>双验证拒绝</th>"
                     "<th>年化</th><th>Sharpe</th><th>最大回撤</th><th>累计盈亏(元)</th></tr></thead><tbody>"
                     + "".join(rolling_rows) + "</tbody></table>")

    # 极值诊断
    top = diag_ext.sort_values("days_extreme", ascending=False).head(5)
    diag_rows = []
    for _, r in top.iterrows():
        diag_rows.append(f"<tr><td>{esc(r.spread_id)}</td><td>{'高' if r.direction=='high' else '低'}</td>"
                         f"<td>{esc(r.start)}</td><td>{int(r.days_extreme)}</td>"
                         f"<td>{num(r.value_start,1)} → {num(r.value_end,1)}</td>"
                         f"<td>{int(r.rolls_in_episode)}</td><td>{esc(r.reverted_date) if r.reverted_date else '未回归'}</td></tr>")
    diag_table = ("<table><thead><tr><th>价差</th><th>方向</th><th>开始</th><th>极值持续(交易日)</th>"
                  "<th>价差变化</th><th>段内换月</th><th>回到中位</th></tr></thead><tbody>"
                  + "".join(diag_rows) + "</tbody></table>")

    # 旧跨期换月跳空
    jumps = pd.DataFrame(diag["calendar_roll_jumps"])
    jumps["abs"] = jumps["jump"].abs()
    jump_view = jumps.loc[jumps.groupby("spread_id")["abs"].idxmax()].sort_values("spread_id")
    jump_table = ("<table><thead><tr><th>价差</th><th>最大跳空日</th><th>换月后合约</th><th>前值</th><th>换月值</th><th>跳空</th></tr></thead><tbody>"
                  + "".join(f"<tr><td>{esc(r.spread_id)}</td><td>{esc(r.roll_date)}</td>"
                            f"<td>{esc(r.leg_a)}/{esc(r.leg_b)}</td><td>{num(r.prev_value,1)}</td>"
                            f"<td>{num(r.value,1)}</td><td class={'pos' if r.jump>0 else 'neg'}>{r.jump:+,.1f}</td></tr>"
                            for _, r in jump_view.iterrows())
                  + "</tbody></table>")

    nav_svg = line_chart({"主池8条": nav_main, "滚动1-6（3条）": nav_roll,
                          "滚动1-6 90/10": nav_roll90},
                         "净值：主池固定期限差 vs 滚动 1-6（2年分位+双验证）", y_fmt="num")
    dd_svg = drawdown_chart(dd_roll, "滚动 1-6（95/5，双验证）：回撤")
    bar_items = []
    for _, old, new, label in pairs:
        a = spread[spread.spread_id == old].iloc[0]
        b = spread[spread.spread_id == new].iloc[0]
        bar_items += [(f"{label} 固定1-5", float(a["cagr"])), (f"{label} 滚动1-6", float(b["cagr"]))]
    cagr_svg = hbar_chart(bar_items, "固定 1-5 vs 滚动 1-6：年化（正红负绿）", value_fmt="pct")

    html_doc = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>分位极值不回归诊断与滚动移仓 1-6 回测｜commodities_strategies</title>
<style>
:root{{--ink:#1f2933;--muted:#64748b;--line:#e2e8f0;--bg:#f6f7f9;--red:#c0392b;--green:#27ae60;--blue:#1f3a5f}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.7 "Noto Sans CJK SC","Source Han Sans SC","PingFang SC","Microsoft YaHei",sans-serif}}
.wrap{{max-width:1180px;margin:0 auto;padding:32px 22px 60px}}
header{{border-bottom:3px solid var(--blue);padding-bottom:18px;margin-bottom:24px}}
h1{{font-size:28px;margin:0 0 6px}}h2{{font-size:21px;margin:34px 0 12px;border-left:5px solid var(--blue);padding-left:10px}}
h3{{font-size:16px;margin:22px 0 8px}}.sub{{color:var(--muted)}}.meta{{color:var(--muted);font-size:12.5px;margin-top:8px}}
.card{{background:#fff;border:1px solid var(--line);border-radius:10px;padding:18px 20px;margin:16px 0}}
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:16px 0}}
.kpi{{background:#fff;border:1px solid var(--line);border-radius:10px;padding:14px 16px}}.kpi .k{{color:var(--muted);font-size:12px}}.kpi .v{{font-size:25px;font-weight:700;margin:4px 0}}.kpi .s{{color:var(--muted);font-size:11.5px}}
table{{width:100%;border-collapse:collapse;font-size:12.5px;margin:8px 0 4px}}th,td{{border-bottom:1px solid var(--line);padding:7px 7px;text-align:right}}th{{background:#f8fafc;color:#475569;font-weight:600}}th:first-child,td:first-child{{text-align:left}}th:nth-child(2),td:nth-child(2){{text-align:left}}
.pos{{color:var(--red);font-weight:600}}.neg{{color:var(--green);font-weight:600}}
svg{{width:100%;height:auto;display:block;margin:6px 0}}.grid{{stroke:#e5e7eb;stroke-width:1}}.zero{{stroke:#94a3b8;stroke-width:1;stroke-dasharray:4 3}}.axis{{fill:#64748b;font-size:11px}}.legend{{fill:#334155;font-size:11px}}.svg-title{{fill:#1f2933;font-size:13px;font-weight:600}}
.callout{{border-left:5px solid var(--red);background:#fff5f4;padding:14px 18px;border-radius:6px;margin:16px 0}}
.callout.good{{border-left-color:var(--green);background:#f3fbf5}}
ul{{margin:8px 0 8px 20px}}li{{margin:4px 0}}.foot{{color:var(--muted);font-size:12px;margin-top:34px;border-top:1px solid var(--line);padding-top:14px}}
a{{color:var(--blue)}}@media(max-width:860px){{.kpis{{grid-template-columns:1fr}}}}
</style></head><body><div class="wrap">
<header>
<h1>分位极值不回归诊断与滚动移仓 1-6（默认 1 年 90/10；2 年对照）</h1>
<div class="sub">之前没有按“1-6 → 2-7 → 3-8”逐月平移；本版补上滚动移仓再回测</div>
<div class="meta">样本 2019-01-02 ~ 2026-11-12（1906 个交易日）｜默认口径：252 日（1 年）90/10 + ADF/Hurst 双验证｜本报告主体为 504 日（2 年）对照｜只做黑色与农产品左侧｜数据源 DataHub</div>
</header>

<div class="callout">
<b>默认策略口径（与用户定义一致）：1 年 90/10 + ADF/Hurst 双验证、黑色/农产品。</b>
主池 8 条年化 <span class="neg">{pct(default_1y['cagr'])}</span>、Sharpe {num(default_1y['sharpe'])}、最大回撤 {pct(default_1y['mdd'])}、{int(default_1y['n_trades'])} 笔；
滚动 1-6 组年化 <span class="pos">{pct(default_roll['cagr'])}</span>、Sharpe {num(default_roll['sharpe'])}、{int(default_roll['n_trades'])} 笔。
报告以下正文用 2 年窗口做对照研究，以说明移仓与窗口的影响。
</div>

<div class="callout">
<b>先回答你的问题：之前不是这样。</b> 跨品种价差的两条腿会按持仓量自动换主力，但跨期 1-5 / 5-9 只是“每年换一次固定月份对”，不是近月到期后逐月平移；
换月时还有 120 天以上空档和巨大跳空（螺纹 -138、豆粕 -371、豆油 -404）。
补上 1-6 逐月平移后，滚动期限差组双验证年化 <span class="pos">{pct(roll95['cagr'])}</span>、Sharpe {num(roll95['sharpe'])}，但收益几乎全部来自螺纹 1-6；豆粕 1-6 零交易。
</div>

<div class="kpis">
  <div class="kpi"><div class="k">默认1年90/10 主池</div><div class="v neg">{pct(default_1y['cagr'])}</div><div class="s">Sharpe {num(default_1y['sharpe'])}｜MDD {pct(default_1y['mdd'])}</div></div>
  <div class="kpi"><div class="k">极值段 40 日内回归</div><div class="v neg">{pct(diag['within_40d_rate'])}</div><div class="s">{int(diag['within_40d'])} / {int(diag['extreme_episodes'])} 段｜中位 {num(diag['median_days_to_reversion'],0)} 天</div></div>
  <div class="kpi"><div class="k">双验证拒绝率</div><div class="v neg">{pct(1-main95['n_trades']/main95['entry_signals'])}</div><div class="s">主池 {int(main95['entry_signals'])} 触发→{int(main95['n_trades'])} 笔</div></div>
  <div class="kpi"><div class="k">滚动 1-6 双验证</div><div class="v pos">{pct(roll95['cagr'])}</div><div class="s">Sharpe {num(roll95['sharpe'])}｜MDD {pct(roll95['mdd'])}｜{int(roll95['n_trades'])} 笔</div></div>
  <div class="kpi"><div class="k">主池 8 条 双验证</div><div class="v neg">{pct(main95['cagr'])}</div><div class="s">Sharpe {num(main95['sharpe'])}｜MDD {pct(main95['mdd'])}</div></div>
</div>

<h2>1. 为什么分位到极值却一直不回来</h2>
<div class="card">
<p>把 504 日分位 ≥95% 或 ≤5% 的连续区间定义为“极值段”，共 <b>{int(diag['extreme_episodes'])}</b> 段。结果：</p>
<ul>
<li><b>只有 {pct(diag['within_40d_rate'])} 在 40 个交易日内回到分位中位</b>（{int(diag['within_40d'])} 段）；中位数要 <b>{num(diag['median_days_to_reversion'],0)} 天</b>；<b>{int(diag['never_reverted'])} 段至今未回归</b>。</li>
<li>策略最长持有 40 天，15 笔实际交易里有 <b>{int(diag['dual_filter_trade_exit_reasons']['max_hold'])} 笔是时间止损</b>离场，只有 1 笔是分位回到中位。</li>
<li>ADF/Hurst 双验证在入场时拒绝 <b>{int(main95['filter_rejected'])} / {int(main95['entry_signals'])}</b> 次（99.3%），所以大量极值根本没有开仓。</li>
<li>旧跨期价差在年度换月处有巨大跳空：极值可能是“换了一对合约”造成的水平跳变，不是同一价差的均值偏离。</li>
</ul>
{diag_table}
</div>

<h2>2. 旧移仓换月到底做了什么</h2>
<div class="card">
<ul>
<li><b>跨品种价差</b>（螺矿比、焦化利润、卷螺差）：两条腿各自按持仓量选主力，主力切换时用价差法后复权，2019-2026 每个品种换月 23 次；这就是“自动移仓”，但不是你描述的固定月份平移。</li>
<li><b>跨期价差</b>（螺纹/豆粕/豆油 1-5）：用固定的 01 与 05 合约，每年 5 月换到下一年的 01/05，换月 8 次；中间有 121~124 天空档，且不后复权。</li>
<li><b>所以旧口径不是</b>“1 月结束买 2 月、2-7、3-8……一直移”。极值长期不回归，有一部分就是换月断点造成的。</li>
</ul>
{jump_table}
</div>

<h2>3. 新实现：1-6 → 2-7 → 3-8 逐月平移</h2>
<div class="card">
<p>新增 <code>calendar_roll</code>：每日选最近未到期且有持仓/成交的近月，远月取不早于“近月 + 5 个月”的最近挂牌月；
近月到期后自动切到下一月，保持固定月份间隔。每次换月记录 <code>roll_flag</code>，并按两腿各平/开一次计换月成本。</p>
<ul>
<li>螺纹 1-6：1906 天，94 次换月；</li>
<li>豆粕 1-6：1906 天，68 次换月（豆粕缺 6 月合约，取不早于目标月的最近月）；</li>
<li>豆油 1-6：1906 天，64 次换月。</li>
</ul>
{fixed_table}
{cagr_svg}
<p class="sub">主池 8 条包含跨品种价差与固定 1-5；滚动 1-6 是单独的 3 条池子，两者不是替换关系，而是“固定期限差 vs 滚动期限差”的对照。</p>
<p class="sub">滚动 1-6 相对固定 1-5 明显改善：螺纹年化从 {pct(spread[spread.spread_id=='RB_01_05'].iloc[0]['cagr'])} 升到 {pct(spread[spread.spread_id=='RB_1_6_ROLL'].iloc[0]['cagr'])}；
豆油从 {pct(spread[spread.spread_id=='Y_01_05'].iloc[0]['cagr'])} 升到 {pct(spread[spread.spread_id=='Y_1_6_ROLL'].iloc[0]['cagr'])}；豆粕两版都因双验证几乎不交易。</p>
</div>

<h2>4. 滚动 1-6 的回测结果</h2>
<div class="card">
{nav_svg}
{dd_svg}
{rolling_table}
<p class="sub">豆粕 1-6 有 68 次换月但 0 笔交易：296 次分位触发全部被 ADF/Hurst 拒绝，不是没有滚动数据。
单品种回撤（螺纹 -66.3%）可以大于等权组合回撤（-26.5%）：组合按活跃价差等权、空仓期计 0，且各价差并不同时持仓。</p>
<p class="sub">滚动组 95/5 双验证：年化 {pct(roll95['cagr'])}、Sharpe {num(roll95['sharpe'])}、最大回撤 {pct(roll95['mdd'])}、{int(roll95['n_trades'])} 笔；
90/10 双验证年化 {pct(roll90['cagr'])}、回撤 {pct(roll90['mdd'])}。
收益集中在螺纹 1-6（年化 {pct(spread[spread.spread_id=='RB_1_6_ROLL'].iloc[0]['cagr'])}，但单品种回撤 {pct(spread[spread.spread_id=='RB_1_6_ROLL'].iloc[0]['mdd'])}），豆粕 1-6 零交易。</p>
</div>

<h2>5. 结论</h2>
<div class="card">
<ul>
<li><b>你的判断是对的</b>：旧版没有逐月平移，只有按年换 1-5/5-9；换月空档和跳空会污染分位、ADF 和 Hurst，极值自然不容易回归。</li>
<li><b>补上滚动 1-6 后确实变好</b>：滚动组双验证从主池的 {pct(main95['cagr'])} 变为 {pct(roll95['cagr'])}，说明移仓方式是关键变量之一。</li>
<li><b>但不能过度乐观</b>：正收益几乎全部来自螺纹 1-6，单品种最大回撤 {pct(spread[spread.spread_id=='RB_1_6_ROLL'].iloc[0]['mdd'])}；豆粕 1-6 被双验证全部拒绝；样本只有 {int(roll95['n_trades'])} 笔交易。</li>
<li><b>双验证仍是强过滤器</b>：它把主池 {pct(1-main95['n_trades']/main95['entry_signals'])}、滚动组 {pct(1-roll95['n_trades']/roll95['entry_signals'])} 的信号挡掉，回撤下降但交易极少。若要做，应该单独把滚动 1-6 作为一个小池子，而不是混入原来的 8 条价差。</li>
<li><b>40 日持有上限与极值回归时间不匹配</b>：中位回归要 209 天，40 天时间止损会把大部分“等待回归”变成“时间止损”。</li>
</ul>
<h3>边界</h3>
<ul>
<li>滚动 1-6 的换月成本按两腿各平/开一次估算；未模拟交割、涨跌停和流动性。</li>
<li>远月取“不早于目标月的最近挂牌月”，豆粕/豆油因缺少部分月份，实际间隔可能大于 5 个月。</li>
<li>结果仍集中在一个宏观周期（2019-2026），不能外推为稳定策略。</li>
</ul>
</div>

<div class="foot">
明细：<a href="./diagnosis_extremes.csv">diagnosis_extremes.csv</a> ·
<a href="./diagnosis_roll.csv">diagnosis_roll.csv</a> ·
<a href="./spread_metrics_w2y_95_adf_hurst.csv">spread_metrics_w2y_95_adf_hurst.csv</a> ·
<a href="./trades_w2y_95_adf_hurst.csv">trades_w2y_95_adf_hurst.csv</a><br>
commodities_strategies · 移仓诊断 · 生成于 {pd.Timestamp.now():%Y-%m-%d %H:%M} · 仅研究，不构成交易建议。
</div>
</div></body></html>"""
    (OUT / "report.html").write_text(html_doc, encoding="utf-8")
    print(f"report -> {OUT/'report.html'} ({len(html_doc):,} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
