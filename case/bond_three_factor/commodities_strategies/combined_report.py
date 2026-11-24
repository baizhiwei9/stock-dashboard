# -*- coding: utf-8 -*-
"""横截面对冲 + 价差分位回归的统一 HTML 报告。

报告本身不重算回测，只读取两条策略已经落盘的结果：
- data/cross_section/out/core_metrics.csv
- data/percentile_reversion/metrics_all.csv

因此不会把两套策略的收益简单相加，也不会把价差策略的负收益藏到横截面结果后面。
"""
from __future__ import annotations

import html
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

import pandas as pd
import yaml

from .config import ROOT

CORE = ROOT / "data" / "cross_section" / "out" / "core_metrics.csv"
METRICS = ROOT / "data" / "percentile_reversion" / "metrics_all.csv"
PERCENTILE_CFG = ROOT / "config" / "percentile_reversion.yaml"
OUT = ROOT / "reports"
DEFAULT_VARIANT = "w1y_90_adf_hurst"


def _fmt_pct(v: Any) -> str:
    try:
        x = float(v)
        return "—" if pd.isna(x) else f"{x:.2%}"
    except (TypeError, ValueError):
        return "—"


def _fmt_num(v: Any, digits: int = 2) -> str:
    try:
        x = float(v)
        return "—" if pd.isna(x) else f"{x:,.{digits}f}"
    except (TypeError, ValueError):
        return "—"


def _fmt_int(v: Any) -> str:
    try:
        return "—" if pd.isna(v) else f"{int(float(v)):,}"
    except (TypeError, ValueError):
        return "—"


def _esc(v: Any) -> str:
    return html.escape(str(v if v is not None else ""))


def _table(headers: Sequence[str], rows: Iterable[Sequence[str]], cls: str = "") -> str:
    head = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f'<table class="{cls}"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def _find(metrics: pd.DataFrame, variant: str, group: str) -> Dict[str, Any]:
    if metrics.empty:
        return {}
    hit = metrics[(metrics["variant"] == variant) & (metrics["group"] == group)]
    return hit.iloc[0].to_dict() if len(hit) else {}


def _load_adf_thresholds() -> tuple[float, float]:
    try:
        cfg = yaml.safe_load(PERCENTILE_CFG.read_text(encoding="utf-8"))
        f = cfg["variants"][DEFAULT_VARIANT].get("filter", {})
        # filter 只存名称；阈值在全局 signal.filter 下
        f = cfg.get("signal", {}).get("filter", f)
        return float(f.get("adf_alpha", 0.05)), float(f.get("hurst_threshold", 0.50))
    except Exception:
        return 0.05, 0.50


def build_report(out_path: Path | None = None) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    target = Path(out_path) if out_path else OUT / "commodities_strategies_report.html"
    target.parent.mkdir(parents=True, exist_ok=True)

    core = pd.read_csv(CORE) if CORE.exists() else pd.DataFrame()
    metrics = pd.read_csv(METRICS) if METRICS.exists() else pd.DataFrame()
    adf_alpha, hurst_cut = _load_adf_thresholds()

    default_merge = _find(metrics, DEFAULT_VARIANT, "两板块合并")
    default_black = _find(metrics, DEFAULT_VARIANT, "黑色")
    default_agri = _find(metrics, DEFAULT_VARIANT, "农产品")
    default_roll = _find(metrics, DEFAULT_VARIANT, "滚动期限差")

    cross_rows: List[List[str]] = []
    for _, r in core.iterrows():
        label = str(r.get("config", "")).split("|")[-1]
        cross_rows.append([
            _esc(label),
            _fmt_int(r.get("n")),
            _fmt_pct(r.get("cagr")),
            _fmt_num(r.get("sharpe")),
            _fmt_pct(r.get("mdd")),
            _fmt_num(r.get("nav")),
            _fmt_pct(r.get("hit")),
        ])

    price_specs = [
        ("默认：1年 90/10 + ADF/Hurst", DEFAULT_VARIANT, "两板块合并"),
        ("默认：黑色", DEFAULT_VARIANT, "黑色"),
        ("默认：农产品", DEFAULT_VARIANT, "农产品"),
        ("默认：滚动 1-6（3条）", DEFAULT_VARIANT, "滚动期限差"),
        ("2年 95/5 双验证", "w2y_95_adf_hurst", "两板块合并"),
        ("2年 90/10 双验证", "w2y_90_adf_hurst", "两板块合并"),
        ("2年 95/5 无过滤", "w2y_95_no_ma", "两板块合并"),
        ("2年 95/5 + MA40", "w2y_95_ma40", "两板块合并"),
    ]
    price_rows: List[List[str]] = []
    for label, variant, group in price_specs:
        r = _find(metrics, variant, group)
        price_rows.append([
            _esc(label),
            _fmt_pct(r.get("cagr")),
            _fmt_num(r.get("sharpe")),
            _fmt_pct(r.get("mdd")),
            _fmt_int(r.get("n_trades")),
            _fmt_int(r.get("entry_signals")),
            _fmt_int(r.get("filter_rejected")),
        ])

    if CORE.exists():
        shutil.copyfile(CORE, target.parent / "cross_section_core_metrics.csv")

    def kpi(label: str, value: str, sub: str, neg: bool = False) -> str:
        cls = "neg" if neg else ""
        return (f'<div class="kpi"><div class="k">{_esc(label)}</div>'
                f'<div class="v {cls}">{value}</div><div class="s">{_esc(sub)}</div></div>')

    cross_same = next((r for r in cross_rows if r[0] == "同板块"), None) if cross_rows else None
    cross_all = next((r for r in cross_rows if r[0] == "全市场"), None) if cross_rows else None
    cross_same_cagr = cross_same[2] if cross_same else "—"
    cross_all_cagr = cross_all[2] if cross_all else "—"

    adf_status = (
        f"默认价差入场必须同时通过 <b>ADF p&lt;{adf_alpha:g}</b> 与 "
        f"<b>Hurst&lt;{hurst_cut:g}</b>；任一不过直接判为 NO_SIGNAL。"
    )

    css = """
:root{--ink:#1f2933;--muted:#64748b;--line:#e2e8f0;--bg:#f6f7f9;--red:#c0392b;--green:#27ae60;--blue:#1f3a5f}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.7 "Noto Sans CJK SC","Source Han Sans SC","PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:32px 22px 60px}
header{border-bottom:3px solid var(--blue);padding-bottom:18px;margin-bottom:24px}
h1{font-size:28px;margin:0 0 6px}h2{font-size:21px;margin:34px 0 12px;border-left:5px solid var(--blue);padding-left:10px}
h3{font-size:16px;margin:22px 0 8px}.sub{color:var(--muted)}.meta{color:var(--muted);font-size:12.5px;margin-top:8px}
.card{background:#fff;border:1px solid var(--line);border-radius:10px;padding:18px 20px;margin:16px 0}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:16px 0}
.kpi{background:#fff;border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.kpi .k{color:var(--muted);font-size:12px}.kpi .v{font-size:25px;font-weight:700;margin:4px 0}.kpi .s{color:var(--muted);font-size:11.5px}
table{width:100%;border-collapse:collapse;font-size:12.5px;margin:8px 0 4px}th,td{border-bottom:1px solid var(--line);padding:7px 7px;text-align:right}
th{background:#f8fafc;color:#475569;font-weight:600}th:first-child,td:first-child{text-align:left}
th:nth-child(2),td:nth-child(2){text-align:left}
.pos{color:var(--red);font-weight:600}.neg{color:var(--green);font-weight:600}
.callout{border-left:5px solid var(--red);background:#fff5f4;padding:14px 18px;border-radius:6px;margin:16px 0}
.callout.good{border-left-color:var(--green);background:#f3fbf5}
ul{margin:8px 0 8px 20px}li{margin:4px 0}.foot{color:var(--muted);font-size:12px;margin-top:34px;border-top:1px solid var(--line);padding-top:14px}
a{color:var(--blue)}code{background:#eef2f7;padding:1px 4px;border-radius:3px}
@media(max-width:860px){.kpis{grid-template-columns:1fr}table{display:block;overflow-x:auto}}
"""

    generated = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")
    body = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>期货策略统一报告｜横截面对冲 × 价差分位回归</title>
<style>{css}</style></head><body><div class="wrap">
<header>
<h1>期货策略统一报告：横截面对冲 × 价差分位回归</h1>
<div class="sub">同一工程、同一份报告；两套策略分别核算，不把收益直接相加。</div>
<div class="meta">数据：DataHub 期货全市场｜价差默认口径：252 日（1 年）90/10 + ADF/Hurst 双验证、黑色/农产品｜生成于 {generated}</div>
</header>

<div class="callout">
<b>先看结论：</b>横截面对冲是两套策略里唯一有正收益记录的一条；价差分位回归默认口径仍为负收益。
本报告把两条策略放在一起，是为了对照信号逻辑和风险，不是把两套资金曲线拼成一条。
</div>

<div class="kpis">
{kpi("横截面｜同板块年化", cross_same_cagr, "三重筛选 · 等权 · 2019-2026", False)}
{kpi("横截面｜全市场年化", cross_all_cagr, "三重筛选 · 等权 · 2019-2026", False)}
{kpi("价差默认｜1年90/10 年化", _fmt_pct(default_merge.get("cagr")), f"Sharpe {_fmt_num(default_merge.get('sharpe'))}", True)}
{kpi("价差默认｜最大回撤", _fmt_pct(default_merge.get("mdd")), f"{_fmt_int(default_merge.get('n_trades'))} 笔", True)}
</div>

<h2>1. 横截面对冲</h2>
<div class="card">
<p>策略目录：<code>strategies/futures_cross_section</code>。按流动性筛选合约，三重筛选（基差/动量/期限结构）后构建多空组合；同板块与全市场分别核算。</p>
{_table(["池", "交易日", "年化", "Sharpe", "最大回撤", "净值", "日胜率"], cross_rows)}
<p class="sub">横截面主口径来自 <code>data/cross_section/out/core_metrics.csv</code>；同板块与全市场是两套独立资金曲线，不能相加。</p>
</div>

<h2>2. 价差分位回归</h2>
<div class="card">
<p>策略目录：<code>strategies/percentile_reversion</code>；底层价差、统计、信号和 SQLite 在 <code>Hedge</code>。只做黑色与农产品、只做左侧均值回归。</p>
{_table(["口径", "年化", "Sharpe", "最大回撤", "交易数", "触发", "双验证拒绝"], price_rows)}
<p class="sub">默认口径为 1 年 90/10；2 年 95/5 等仅作对照。当前默认合并年化为
<span class="neg">{_fmt_pct(default_merge.get('cagr'))}</span>、Sharpe {_fmt_num(default_merge.get('sharpe'))}、
最大回撤 {_fmt_pct(default_merge.get('mdd'))}、{_fmt_int(default_merge.get('n_trades'))} 笔，
不能表述为正期望。黑色 {_fmt_pct(default_black.get('cagr'))}、农产品 {_fmt_pct(default_agri.get('cagr'))}；
滚动 1-6 分支年化 {_fmt_pct(default_roll.get('cagr'))}，但回撤 {_fmt_pct(default_roll.get('mdd'))}，收益集中，不能单独当作稳健结论。</p>
</div>

<h2>3. ADF + Hurst 双验证</h2>
<div class="card">
<div class="callout good">{adf_status}</div>
<ul>
<li>ADF 检验价差是否平稳：p 值越小，越支持均值回归；默认阈值 <code>p&lt;{adf_alpha:g}</code>。</li>
<li>Hurst 判断长期记忆：默认要求 <code>Hurst&lt;{hurst_cut:g}</code>；偏高视为趋势性，不进场。</li>
<li>通过双验证后，还要经过波动率极端过滤、极值分位触发、基差/仓单冲突否决；仓位不会因为“分位到 90%”就自动开。</li>
<li>实现入口：<code>Hedge/signals.py</code>；默认参数在 <code>config/percentile_reversion.yaml</code> 的 <code>w1y_90_adf_hurst</code>。</li>
</ul>
<p class="sub">默认口径的 <code>filter=adf_hurst</code>；2 年对照变体也沿用同一双验证，但窗口为 504 日。</p>
</div>

<h2>4. 交易机会邮件通知</h2>
<div class="card">
<p>已提供 <code>commodities_strategies.email_notify</code> 与 <code>scripts/notify_opportunities.py</code>：从 SQLite 最新信号中筛出
<code>LONG_SPREAD</code> / <code>SHORT_SPREAD</code>，生成含分位、Z、ADF、Hurst 和原因的邮件。</p>
<ul>
<li>默认 <b>dry-run</b>：只打印主题、收件人和正文，不连接 SMTP；</li>
<li>真实发送需设置 <code>SMTP_HOST</code>、<code>SMTP_PORT</code>、<code>SMTP_FROM</code>、<code>SMTP_TO</code>，并按需设置 <code>SMTP_USER</code>/<code>SMTP_PASSWORD</code>；</li>
<li>没有交易机会时不生成邮件，也不会发送空通知。</li>
</ul>
</div>

<h2>5. 数据与口径边界</h2>
<div class="card">
<ul>
<li>横截面结果来自 <code>core_metrics.csv</code>（n=4,329 个交易日）；价差结果来自 <code>metrics_all.csv</code>（n=1,906 个交易日），两者样本区间不同。</li>
<li>两套策略的保证金、持仓周期和交易成本假设不同，不能直接比较绝对收益，也不能合并成一条净值。</li>
<li>价差默认回测为负收益，报告保留负号；横截面历史回测也不代表未来收益。</li>
<li>本报告只做研究与信号辅助，不构成交易建议。</li>
</ul>
</div>

<div class="foot">
明细：<a href="cross_section_core_metrics.csv">横截面指标</a> ·
<a href="../data/percentile_reversion/metrics_all.csv">价差全部口径指标</a> ·
<a href="../data/percentile_reversion/default_metrics.csv">价差默认口径指标</a> ·
<a href="../data/percentile_reversion/report.html">价差详细报告</a><br>
commodities_strategies · 统一报告 · 生成于 {generated}
</div>
</div></body></html>"""
    target.write_text(body, encoding="utf-8")
    return target


def main() -> int:
    p = build_report()
    print(f"combined report -> {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())