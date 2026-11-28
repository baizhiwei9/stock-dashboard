#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成两份 HTML 报告：出清周期监控 / 情绪周期监控（纯 Python，零 token）"""
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "output", "data")
REPORT_DIR = os.path.join(ROOT, "output", "report")


def load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _intraday():
    """14:50 跑：日线未收，报告需标注盘中口径。显式按北京时间判断。"""
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    return now.hour * 60 + now.minute < 15 * 60


def _intra_badge():
    return "<span>⚠️ 盘中数据，非收盘价</span>" if _intraday() else ""


CSS = """
:root{
  --bg:#0a0e13; --card:#131b24; --card2:#172230; --line:#22303f;
  --txt:#e6edf5; --dim:#8b9bad; --dim2:#5f7183;
  --green:#3ddc97; --yellow:#ffb020; --red:#ff5d5d; --blue:#4aa8ff; --purple:#a78bfa;
  --mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{background:radial-gradient(1200px 600px at 15% -10%,#132030 0%,var(--bg) 55%) fixed,var(--bg);
  color:var(--txt);font:15px/1.65 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:1180px;margin:0 auto;padding:36px 22px 72px}
a{color:var(--blue);text-decoration:none;border-bottom:1px solid rgba(74,168,255,.35)}
a:hover{border-bottom-color:var(--blue)}
a:focus-visible,summary:focus-visible{outline:2px solid var(--blue);outline-offset:3px}
header.top{border-bottom:1px solid var(--line);padding-bottom:20px;margin-bottom:26px}
.kicker{font:600 12px/1 var(--mono);letter-spacing:.18em;color:var(--dim2);text-transform:uppercase}
h1{font-size:clamp(24px,3.4vw,36px);margin:12px 0 8px;letter-spacing:-.02em;font-weight:700}
h1 span{color:var(--dim);font-weight:400;font-size:.62em;margin-left:10px;font-family:var(--mono)}
.meta{color:var(--dim);font-size:13.5px;display:flex;gap:18px;flex-wrap:wrap}
.meta b{color:var(--txt);font-family:var(--mono);font-weight:600}
.verdict{display:grid;grid-template-columns:1.15fr 2fr;gap:18px;margin:26px 0 34px}
@media(max-width:820px){.verdict{grid-template-columns:1fr}}
.vbox{background:linear-gradient(150deg,#16232f,#111a23);border:1px solid #2a3d50;border-radius:14px;padding:22px 24px}
.vbox .big{font:700 44px/1 var(--mono);letter-spacing:-.03em}
.vbox .big em{font-style:normal;font-size:19px;color:var(--dim);margin-left:6px}
.vbox .lbl{font-size:13px;color:var(--dim);letter-spacing:.06em;margin-bottom:10px}
.vbox p{margin:12px 0 0;font-size:13.5px;color:var(--dim)}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}
.chip{font:600 12px/1 var(--mono);padding:7px 11px;border-radius:999px;border:1px solid}
.chip.on{color:var(--green);border-color:rgba(61,220,151,.45);background:rgba(61,220,151,.1)}
.chip.off{color:var(--dim2);border-color:var(--line);background:#0d141c}
.chip.warn{color:var(--yellow);border-color:rgba(255,176,32,.45);background:rgba(255,176,32,.1)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:15px 16px}
.kpi .k{font-size:12px;color:var(--dim);letter-spacing:.05em}
.kpi .v{font:700 25px/1.2 var(--mono);margin-top:8px;letter-spacing:-.02em}
.kpi .s{font-size:12px;color:var(--dim2);margin-top:5px;font-family:var(--mono)}
section{margin:0 0 40px}
h2{font-size:19px;margin:0 0 4px;letter-spacing:-.01em;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
h2 .idx{font:700 12px/1 var(--mono);color:var(--bg);background:var(--dim);border-radius:5px;padding:5px 7px}
h2 .badge{font:600 11.5px/1 var(--mono);padding:6px 9px;border-radius:6px;margin-left:auto}
.badge.ok{color:var(--green);background:rgba(61,220,151,.12);border:1px solid rgba(61,220,151,.35)}
.badge.warn{color:var(--yellow);background:rgba(255,176,32,.12);border:1px solid rgba(255,176,32,.35)}
.badge.red{color:var(--red);background:rgba(255,93,93,.12);border:1px solid rgba(255,93,93,.35)}
.lede{color:var(--dim);font-size:13.5px;margin:6px 0 18px;max-width:80ch}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px 22px}
.card + .card{margin-top:14px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:820px){.grid2{grid-template-columns:1fr}}
.bar{position:relative;height:12px;background:#0b1219;border:1px solid var(--line);border-radius:999px;margin:44px 0 52px}
.bar .fill{position:absolute;left:0;top:0;bottom:0;border-radius:999px;background:linear-gradient(90deg,#2a7fbf,#3ddc97 62%,#ffb020)}
.bar .pin{position:absolute;top:-7px;width:3px;height:26px;background:#fff;border-radius:2px;box-shadow:0 0 12px rgba(255,255,255,.6)}
.tick{position:absolute;top:-2px;width:1px;height:16px;background:var(--dim2)}
.tick b{position:absolute;left:50%;transform:translateX(-50%);top:-20px;font:600 12px/1 var(--mono);color:var(--txt);white-space:nowrap}
.tick em{position:absolute;left:50%;transform:translateX(-50%);top:18px;font:400 11px/1 var(--mono);color:var(--dim2);white-space:nowrap;font-style:normal}
svg{width:100%;height:auto;display:block;margin-top:6px}
svg .grid{stroke:#1b2836;stroke-width:1}
svg .ax{fill:var(--dim2);font:11px var(--mono)}
svg .thr{stroke:var(--dim2);stroke-width:1;stroke-dasharray:5 4}
svg .thrlab{fill:var(--dim);font:11px var(--mono)}
svg .ln{fill:none;stroke:var(--blue);stroke-width:2.2;stroke-linejoin:round;stroke-linecap:round}
svg .ln2{fill:none;stroke:var(--dim2);stroke-width:1.4;stroke-dasharray:4 3}
svg .ln3{fill:none;stroke:var(--purple);stroke-width:1.8}
svg .ln4{fill:none;stroke:var(--txt);stroke-width:1.2;opacity:.55}
svg .dot{fill:var(--yellow);stroke:#0a0e13;stroke-width:1.5}
svg .mark{fill:var(--txt);font:600 11px var(--mono)}
svg .band-mid{fill:rgba(61,220,151,.07)}
svg .band-hot{fill:rgba(255,93,93,.09)}
table{width:100%;border-collapse:collapse;font-size:13.5px}
caption{text-align:left;color:var(--dim);font-size:12.5px;padding-bottom:9px}
th,td{padding:9px 10px;border-bottom:1px solid #1b2836;text-align:left;vertical-align:top}
th{color:var(--dim);font-weight:600;font-size:12px;letter-spacing:.04em;white-space:nowrap}
td.num,th.num{text-align:right;font-family:var(--mono);white-space:nowrap}
td.strong{color:var(--yellow);font-weight:700}
tr.hit td{background:rgba(61,220,151,.045)}
.sub{color:var(--dim2);font-size:11px}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
.ok{color:var(--green);font:600 12px var(--mono);white-space:nowrap}
.no{color:var(--dim2);font:600 12px var(--mono);white-space:nowrap}
ul.pts{margin:0;padding-left:20px;color:var(--dim)}
ul.pts li{margin:7px 0}
ul.pts b{color:var(--txt);font-weight:600}
.callout{border-left:3px solid var(--yellow);background:rgba(255,176,32,.06);border-radius:0 10px 10px 0;padding:14px 18px;color:#e8dcc4;font-size:13.5px}
.callout b{color:var(--yellow)}
.callout.red{border-left-color:var(--red);background:rgba(255,93,93,.06);color:#f0d8d8}
.callout.red b{color:var(--red)}
.rule{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin-top:6px}
.rule div{background:var(--card2);border:1px solid var(--line);border-radius:10px;padding:13px 15px;font-size:13px;color:var(--dim)}
.rule div b{display:block;color:var(--txt);font-family:var(--mono);font-size:14px;margin-bottom:5px}
.dimbar{display:grid;grid-template-columns:120px 1fr 56px;gap:12px;align-items:center;margin:10px 0;font-size:13px}
.dimbar .lab{color:var(--dim)}
.dimbar .track{position:relative;height:9px;background:#0b1219;border:1px solid var(--line);border-radius:999px}
.dimbar .track i{position:absolute;top:0;bottom:0;border-radius:999px;display:block}
.dimbar .val{font-family:var(--mono);text-align:right;font-weight:600}
footer{border-top:1px solid var(--line);margin-top:14px;padding-top:20px;color:var(--dim2);font-size:12.5px}
footer p{margin:6px 0}
details{margin-top:14px}
summary{cursor:pointer;color:var(--dim);font-size:13px}
details .scroll{margin-top:12px}
@media print{
  body{background:#fff;color:#111}
  .wrap{max-width:none;padding:0}
  .card,.vbox,.kpi,.rule div{background:#fff;border-color:#ccc}
  h1,h2,.kpi .v,td.strong{color:#111}
  svg .grid{stroke:#ddd} svg .ax,svg .thrlab{fill:#555}
  section{break-inside:avoid}
  a{color:#111}
}
"""


def chart(pts, keys, w=880, h=300, pad=(46, 18, 34, 52), ymin=None, ymax=None,
          bands=(), hlines=(), marks=(), fmt="{:.0f}", xlabel_every=10, aria="趋势图"):
    """keys: [(field, css_class)]，pts 每项含多个数值字段"""
    keys = [(k, c) for k, c in keys]
    vals = []
    for p in pts:
        for k, _ in keys:
            if p.get(k) is not None:
                vals.append(p[k])
    vals += [v for v, _ in hlines]
    if not vals:
        return "<p>无数据</p>"
    lo = ymin if ymin is not None else min(vals)
    hi = ymax if ymax is not None else max(vals)
    if hi - lo < 1e-9:
        hi = lo + 1
    top, right, bottom, left = pad
    iw, ih = w - left - right, h - top - bottom
    n = len(pts)

    def X(i):
        return left + (iw * i / (n - 1) if n > 1 else iw / 2)

    def Y(v):
        return top + ih - (v - lo) / (hi - lo) * ih

    out = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{esc(aria)}">']
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        y = Y(v)
        out.append(f'<line x1="{left}" y1="{y:.1f}" x2="{w-right}" y2="{y:.1f}" class="grid"/>')
        out.append(f'<text x="{left-8}" y="{y+4:.1f}" class="ax" text-anchor="end">{fmt.format(v)}</text>')
    for a, b, cls in bands:
        y1, y2 = Y(b), Y(a)
        out.append(f'<rect x="{left}" y="{y1:.1f}" width="{iw}" height="{max(1,y2-y1):.1f}" class="{cls}"/>')
    for v, lab in hlines:
        y = Y(v)
        out.append(f'<line x1="{left}" y1="{y:.1f}" x2="{w-right}" y2="{y:.1f}" class="thr"/>')
        out.append(f'<text x="{w-right-4}" y="{y-5:.1f}" class="thrlab" text-anchor="end">{esc(lab)}</text>')
    for k, cls in keys:
        seg = [(i, p[k]) for i, p in enumerate(pts) if p.get(k) is not None]
        if not seg:
            continue
        d = " ".join(("M" if j == 0 else "L") + f"{X(i):.1f},{Y(v):.1f}" for j, (i, v) in enumerate(seg))
        out.append(f'<path d="{d}" class="{cls}"/>')
    for idx, lab in marks:
        if 0 <= idx < n and pts[idx].get(keys[0][0]) is not None:
            p = pts[idx]
            out.append(f'<circle cx="{X(idx):.1f}" cy="{Y(p[keys[0][0]]):.1f}" r="3.6" class="dot"/>')
            out.append(f'<text x="{X(idx):.1f}" y="{Y(p[keys[0][0]])-10:.1f}" class="mark" text-anchor="middle">{esc(lab)}</text>')
    for i, p in enumerate(pts):
        if i % xlabel_every == 0 or i == n - 1:
            out.append(f'<text x="{X(i):.1f}" y="{h-bottom+18:.1f}" class="ax" text-anchor="middle">{esc(p["date"][5:])}</text>')
    out.append("</svg>")
    return "".join(out)


def pct_bar(value, lines, lo=4.0, hi=9.0):
    pos = max(0.0, min(1.0, (value - lo) / (hi - lo))) * 100

    def lp(v):
        return max(0.0, min(1.0, (v - lo) / (hi - lo))) * 100

    ticks = "".join(
        f'<span class="tick" style="left:{lp(v):.1f}%"><i></i><b>{v}%</b><em>{lab}</em></span>'
        for v, lab in lines
    )
    return f'<div class="bar"><span class="fill" style="width:{pos:.1f}%"></span><span class="pin" style="left:{pos:.1f}%"></span>{ticks}</div>'


FOOT = """<footer>
  <p><b>数据来源</b>：ETF 份额与规模来自<a href="http://www.sse.com.cn" target="_blank" rel="noopener noreferrer">上海证券交易所</a>、
  <a href="http://www.szse.cn" target="_blank" rel="noopener noreferrer">深圳证券交易所</a>；ETF 净值来自
  <a href="https://fund.eastmoney.com" target="_blank" rel="noopener noreferrer">天天基金</a>；指数收盘与成交来自
  <a href="https://www.csindex.com.cn" target="_blank" rel="noopener noreferrer">中证指数</a>；股指期货行情来自
  <a href="https://finance.sina.com.cn" target="_blank" rel="noopener noreferrer">新浪财经</a>；沪深300自由流通市值来自
  <a href="https://www.wind.com.cn" target="_blank" rel="noopener noreferrer">万得 Wind</a>；两融数据来自
  <a href="https://data.eastmoney.com/rzrq/" target="_blank" rel="noopener noreferrer">东方财富两融</a>。</p>
  <p><b>口径参照</b>：被动化率刻度与 7% 提醒线、基差正常区间 4%–5% 来自用户提供的土狗公众号 Vol.261128 截图；出清周期边界（MA40/MA80）按用户设定。</p>
  <p>报告生成于 __TS__ · 数据截止 __ASOF__ __CUTOFF__ · 量化监控输出，不构成投资建议。</p>
</footer>"""


def page(title, as_of, body, kicker="Clearance Cycle Monitor · v3.1"):
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
{body}
{FOOT.replace('__TS__', datetime.now().strftime('%Y-%m-%d %H:%M')).replace('__ASOF__', as_of).replace('__CUTOFF__', '（盘中，非收盘价）' if _intraday() else '收盘')}
</div>
</body>
</html>
"""


# ============ 报告一：出清周期监控 ============
def build_cycle(c):
    ma, p, b = c["ma"], c["passive"], c["basis"]
    as_of = c["as_of"]

    series = ma["series"]
    mchart = chart(
        series,
        [("close", "ln4"), ("ma_fast", "ln"), ("ma_slow", "ln3")],
        hlines=[], marks=[(len(series) - 1, f'MA40 {ma["ma_fast"]:.0f}')],
        xlabel_every=12, fmt="{:.0f}", aria="沪深300收盘与MA40/MA80",
    )

    cross_rows = "".join(
        f"<tr><td>{x['date']}</td><td>{esc(x['type'])}</td>"
        f"<td class='num'>{x['close']:.2f}</td><td class='num'>{x['ma_fast']:.2f}</td>"
        f"<td class='num'>{x['ma_slow']:.2f}</td></tr>"
        for x in reversed(ma["crosses"])
    )

    ph = [{"date": r["date"], "dog": r.get("pct_dog"), "wind": r.get("pct")} for r in p["history"]]
    pchart = chart(
        ph, [("dog", "ln"), ("wind", "ln2")], h=280, ymin=4.6, ymax=8.7,
        hlines=[(4.9, "起计时 4.9%"), (7.0, "提醒线 7.0%"), (8.5, "区间上沿 8.5%")],
        bands=[(4.9, 7.0, "band-mid"), (7.0, 8.5, "band-hot")],
        marks=[(len(ph) - 1, f'{p["value_pct_dog"]:.2f}%')] if ph else [],
        fmt="{:.1f}", xlabel_every=4, aria="被动化率走势",
    )

    bh = [{"date": r["date"], "y": r["annual_pct"]} for r in b["IF"]["series"]]
    bmarks = []
    if bh:
        hi_i = max(range(len(bh)), key=lambda i: bh[i]["y"])
        bmarks = [(hi_i, f'高点 {bh[hi_i]["y"]:.2f}%'), (len(bh) - 1, f'{bh[-1]["y"]:.2f}%')]
    bchart = chart(
        bh, [("y", "ln")], h=280, hlines=[(4.0, "正常下沿 4%"), (5.0, "正常上沿 5%")],
        bands=[(4.0, 5.0, "band-mid")], marks=bmarks, fmt="{:.1f}", xlabel_every=5,
        aria="IF 远季年化基差走势",
    )

    basis_rows = "".join(
        f"<tr class='{'hit' if d['triggered'] else ''}'><td><b>{v}</b></td><td>{esc(d['contract'])}</td>"
        f"<td class='num'>{d['spot']:.2f}</td><td class='num'>{d['fut']:.2f}</td>"
        f"<td class='num'>{d['basis']:+.1f}</td><td class='num'>{d['days_left']}</td>"
        f"<td class='num strong'>{d['annual_pct']:.2f}%</td>"
        f"<td class='num'>{d['high5']:.2f}%<br><span class='sub'>{d['high5_date'][5:]}</span></td>"
        f"<td class='num'>{d['drop_pp']:.2f}pp</td>"
        f"<td>{'<span class=\"ok\">✓ 完成</span>' if d['triggered'] else '<span class=\"no\">✗ 未完成</span>'}</td></tr>"
        for v, d in b.items()
    )

    cats = sorted(p["by_cat_yi"].items(), key=lambda kv: -kv[1])
    cat_rows = "".join(
        f"<tr><td>{esc(k)}</td><td class='num'>{v:,.1f}</td><td class='num'>{v/p['wide_scale_yi']*100:.1f}%</td></tr>"
        for k, v in cats
    )

    state_cls = "ok" if ma["in_clearing"] else "warn"
    body = f"""
<header class="top">
  <div class="kicker">Clearance Cycle Monitor · v3.1</div>
  <h1>出清周期监控<span>{as_of}</span></h1>
  <div class="meta">
    <span>周期边界：沪深300 MA40 / MA80</span>
    <span>当前状态 <b>{esc(ma['state'])}</b></span>
    <span>已持续 <b>{ma['days']}</b> 个交易日</span>
    <span>报告动作 <b>{esc(ma['report_action'])}</b></span>
    {_intra_badge()}
  </div>
</header>

<div class="verdict">
  <div class="vbox">
    <div class="lbl">出清周期状态</div>
    <div class="big" style="color:{'var(--yellow)' if ma['in_clearing'] else 'var(--green)'}">{'出清中' if ma['in_clearing'] else '已结束'}<em>第 {ma['days']} 日</em></div>
    <p>{ma['since']} MA40 <b style="color:var(--txt)">{esc(ma['last_cross_type'])}</b> MA80 起算；完成条件为 MA40 上穿 MA80，目前 MA40 仍在 MA80 下方 {ma['gap_pct']:.2f}%。</p>
    <div class="chips">
      <span class="chip on">出清开始（下穿）</span>
      <span class="chip {'on' if not ma['in_clearing'] else 'off'}">出清完成（上穿）</span>
      <span class="chip {'on' if c['progress_done']==2 else 'warn'}">进度指标 {c['progress_done']}/2</span>
    </div>
  </div>
  <div class="kpis">
    <div class="kpi"><div class="k">沪深300 收盘</div><div class="v">{ma['close']:,.2f}</div><div class="s">MA40 {ma['ma_fast']:,.0f} · MA80 {ma['ma_slow']:,.0f}</div></div>
    <div class="kpi"><div class="k">MA40 − MA80</div><div class="v" style="color:var(--red)">{ma['dif']:,.1f}</div><div class="s">缺口 {ma['gap_pct']:.2f}%，需收窄后上穿</div></div>
    <div class="kpi"><div class="k">被动化率</div><div class="v">{p['value_pct']:.2f}%</div><div class="s">土狗刻度 {p['value_pct_dog']:.2f}% · 提醒线 7%</div></div>
    <div class="kpi"><div class="k">IF 远季年化</div><div class="v" style="color:var(--yellow)">{b['IF']['annual_pct']:.2f}%</div><div class="s">回落 {b['IF']['drop_pp']:.2f}pp · 正常 4–5%</div></div>
  </div>
</div>

<main>

<section>
  <h2><span class="idx">1</span>周期边界：MA40 / MA80<span class="badge {state_cls}">{esc(ma['state'])}</span></h2>
  <p class="lede">
    出清周期的开关只认均线：<b style="color:var(--txt)">MA40 下穿 MA80 开始出清、开始发送报告</b>；
    <b style="color:var(--txt)">MA40 上穿 MA80 视为出清完成、停止发送报告</b>。
    被动化率与基差是出清期间的进度条，用来衡量走了多远，不作为完成判定。
  </p>
  <div class="card">
    <figure style="margin:0">
      <figcaption style="color:var(--dim);font-size:12.5px;margin-bottom:8px">
        沪深300 近 {len(series)} 个交易日：细线为收盘价，蓝线 MA40，紫线 MA80。MA40 位于 MA80 下方即为出清区间。
      </figcaption>
      {mchart}
    </figure>
  </div>
  <div class="card">
    <div class="scroll">
      <table>
        <caption>最近 6 次 MA40/MA80 交叉记录</caption>
        <thead><tr><th scope="col">日期</th><th scope="col">方向</th><th scope="col" class="num">收盘</th><th scope="col" class="num">MA40</th><th scope="col" class="num">MA80</th></tr></thead>
        <tbody>{cross_rows}</tbody>
      </table>
    </div>
  </div>
  <div class="callout" style="margin-top:14px">
    <b>修复测算：</b>MA40 目前 {ma['ma_fast']:,.0f}，MA80 {ma['ma_slow']:,.0f}，缺口 {ma['gap_pct']:.2f}%（约 {abs(ma['dif']):.0f} 点）。
    按当日收盘价横盘推算，MA40 与 MA80 每日收敛约 <b>{ma['converge_speed']:.2f} 点</b>，
    需要 <b>约 {ma['flat_days']} 个交易日</b>才能上穿；指数若持续走强，收敛会明显加快。
    注意下跌末端常出现「MA80 快速下移造成的被动金叉」，届时需结合成交与被动化率确认，避免把假金叉当周期结束。
    <b>只要仍在下穿状态，本报告就继续发送。</b>
  </div>
</section>

<section>
  <h2><span class="idx">2</span>进度指标 A · 被动化率<span class="badge {'ok' if p['value_pct']>=7 else 'warn'}">{'已过 7% 提醒线' if p['value_pct']>=7 else '计时中'}</span></h2>
  <p class="lede">
    口径 = 宽基 ETF 总规模 ÷ 沪深300 自由流通市值。4.9% 起计时，7.0% 触发提醒，历史区间上沿 8.5%。
    当日 <b style="color:var(--txt)">{p['value_pct']:.2f}%</b>（Wind 分母 {p['denom_yi']/1e4:.2f} 万亿），
    按土狗原文刻度折算 <b style="color:var(--txt)">{p['value_pct_dog']:.2f}%</b>。
  </p>
  {pct_bar(p['value_pct'], [(4.9, '起计时'), (7.0, '提醒'), (8.5, '上沿')])}
  <div class="card">
    <figure style="margin:0">
      <figcaption style="color:var(--dim);font-size:12.5px;margin-bottom:8px">
        近 {len(p['history'])} 个交易日被动化率；实线为土狗锚点刻度，虚线为 Wind 自由流通市值口径。绿色带 4.9%–7.0% 为出清进行中，红色带 7.0%–8.5% 为接近完成。
      </figcaption>
      {pchart}
    </figure>
  </div>
  <div class="grid2" style="margin-top:14px">
    <div class="card">
      <h3 style="margin:0 0 12px;font-size:14px;color:var(--dim);letter-spacing:.05em">宽基 ETF 分类规模（亿元）</h3>
      <div class="scroll" style="max-height:320px">
        <table>
          <caption>合计 {p['wide_scale_yi']:,.1f} 亿元</caption>
          <thead><tr><th scope="col">类别</th><th scope="col" class="num">规模</th><th scope="col" class="num">占比</th></tr></thead>
          <tbody>{cat_rows}</tbody>
        </table>
      </div>
    </div>
    <div class="card">
      <h3 style="margin:0 0 12px;font-size:14px;color:var(--dim);letter-spacing:.05em">口径说明</h3>
      <ul class="pts">
        <li><b>分母必须用自由流通市值</b>（{p['denom_yi']/1e4:.2f} 万亿）。换成总市值/流通市值会把读数压到 3% 附近，与 4.9%–8.5% 刻度完全对不上。</li>
        <li><b>双读数</b>：土狗原文 11/27 读 7.83%，按其刻度反推分母约 {p['denom_dog_yi']/1e4:.2f} 万亿，故并列展示以便对照原文。</li>
        <li><b>历史近似</b>：沪市份额按日回溯、深市份额固定当日值、净值用最新值，历史段对深市流入略有低估，趋势方向可靠。</li>
      </ul>
    </div>
  </div>
</section>

<section>
  <h2><span class="idx">3</span>进度指标 B · 年化基差<span class="badge ok">回落 {b['IF']['drop_pp']:.2f}pp</span></h2>
  <p class="lede">
    口径 =（现货 − 期货）÷ 现货 × 365 ÷ 剩余日历天数，取剩余 60–150 天的远季合约（{esc(b['IF']['contract'])} 一类），
    避开近月临到期时的天数放大失真。正常区间 4%–5%，距 5 日高点回落 ≥5 个百分点视为基差修复到位。
  </p>
  <div class="card">
    <div class="scroll">
      <table>
        <caption>当日各品种远季合约基差</caption>
        <thead><tr>
          <th scope="col">品种</th><th scope="col">合约</th><th scope="col" class="num">现货</th><th scope="col" class="num">期货</th>
          <th scope="col" class="num">基差</th><th scope="col" class="num">剩余天数</th><th scope="col" class="num">年化</th>
          <th scope="col" class="num">5日高点</th><th scope="col" class="num">回落</th><th scope="col">判定</th>
        </tr></thead>
        <tbody>{basis_rows}</tbody>
      </table>
    </div>
  </div>
  <div class="card">
    <figure style="margin:0">
      <figcaption style="color:var(--dim);font-size:12.5px;margin-bottom:8px">IF 远季合约年化基差近 40 个交易日走势，绿色带为 4%–5% 正常区间。</figcaption>
      {bchart}
    </figure>
  </div>
  <div class="callout" style="margin-top:14px">
    <b>读数：</b>IF 远季年化当前 <b>{b['IF']['annual_pct']:.2f}%</b>，5 日高点 <b>{b['IF']['high5']:.2f}%</b>（{b['IF']['high5_date'][5:]}），
    回落 <b>{b['IF']['drop_pp']:.2f}pp</b>，{'已过 5pp 门槛，基差修复到位。' if b['IF']['triggered'] else '尚未达到 5pp 门槛。'}
    {'已回到 4%–5% 正常带。' if b['IF']['in_normal'] else '目前不在 4%–5% 正常带内。'}
    IC/IM 同步展示，中证500/1000 的对冲盘是否撤出以对应读数为准。
  </div>
</section>

<section>
  <h2><span class="idx">→</span>结论</h2>
  <div class="card">
    <ul class="pts">
      <li><b>周期状态：{esc(ma['state'])}，第 {ma['days']} 个交易日。</b>边界由 MA40 与 MA80 的相对位置维持，报告动作：{esc(ma['report_action'])}。</li>
      <li><b>进度指标 {c['progress_done']}/2 到位</b>：被动化率 {p['value_pct_dog']:.2f}%（土狗刻度，提醒线 7%）、基差回落 {b['IF']['drop_pp']:.2f}pp（门槛 5pp）。</li>
      <li><b>完成与否只看均线</b>：MA40 与 MA80 当前相差 {ma['gap_pct']:.2f}%（{abs(ma['dif']):.0f} 点）。进度指标到位不等于周期结束，只说明出清走了大半程。</li>
      <li><b>停止发送的条件</b>：MA40 上穿 MA80 当日，本报告自动停发，不再占用每日推送位。</li>
    </ul>
  </div>
  <div class="card">
    <h3 style="margin:0 0 12px;font-size:14px;color:var(--dim);letter-spacing:.05em">触发规则</h3>
    <div class="rule">
      <div><b>MA40 下穿 MA80</b>出清开始，开始发送本报告</div>
      <div><b>MA40 上穿 MA80</b>出清完成，停止发送本报告</div>
      <div><b>4.9% / 7.0% / 8.5%</b>被动化率 起计时 / 邮件提醒 / 区间上沿</div>
      <div><b>−5pp</b>年化基差距 5 日高点回落，基差修复到位</div>
    </div>
  </div>
</section>

</main>
"""
    return page(f"出清周期监控 · {as_of}", as_of, body)


# ============ 报告二：情绪周期监控 ============
def build_emotion(e):
    m = e["metrics"]
    mg = m["margin"] or {}
    qv = m["qvix"] or {}
    as_of = e["as_of"]
    heat = e["heat"]

    tone_color = {"red": "var(--red)", "yellow": "var(--yellow)", "blue": "var(--blue)", "green": "var(--green)"}.get(e["tone"], "var(--txt)")

    # 五维条
    parts_html = ""
    for k, v in e["parts"].items():
        span = 45 if v > 0 else 25
        w = min(100, abs(v) / span * 100)
        color = "var(--green)" if v > 0 else ("var(--red)" if v < 0 else "var(--dim2)")
        side = "left:50%" if v >= 0 else f"right:50%"
        parts_html += (
            f'<div class="dimbar"><span class="lab">{esc(k)}</span>'
            f'<span class="track"><i style="{side};width:{w/2:.0f}%;background:{color}"></i></span>'
            f'<span class="val" style="color:{color}">{v:+d}</span></div>'
        )

    # 阶段刻度
    stage_scale = ""
    for st in e["stages"]:
        active = st["name"] == e["stage"]
        stage_scale += (
            f'<div style="flex:1;text-align:center;padding:10px 6px;border-radius:8px;'
            f'background:{"rgba(255,93,93,.14)" if active else "transparent"};'
            f'border:1px solid {"var(--red)" if active else "var(--line)"};margin:0 3px">'
            f'<div style="font:700 13px var(--mono);color:{"var(--red)" if active else "var(--dim)"}">{esc(st["name"])}</div>'
            f'<div style="font-size:11px;color:var(--dim2);margin-top:4px">≥{st["threshold"]}</div></div>'
        )

    body = f"""
<header class="top">
  <div class="kicker">Emotion Cycle Monitor · v1.0</div>
  <h1>情绪周期监控<span>{as_of}</span></h1>
  <div class="meta">
    <span>独立模块，不再作为出清完成信号</span>
    <span>情绪温度 <b>{heat} / 100</b></span>
    <span>阶段 <b>{esc(e['stage'])}</b></span>
    <span>沪深300 <b>{m['hs300_close']:,.2f}</b>（{m['hs300_chg_pct']:+.2f}%）</span>
    {_intra_badge()}
  </div>
</header>

<div class="verdict">
  <div class="vbox">
    <div class="lbl">情绪温度</div>
    <div class="big" style="color:{tone_color}">{heat}<em>/ 100 · {esc(e['stage'])}</em></div>
    <p>{esc(e['meaning'])}</p>
    <div class="chips">
      <span class="chip {'warn' if heat>=55 else 'on'}">涨停 {m['limit_up']}</span>
      <span class="chip {'warn' if (m['median_chg'] or 0)>=2 else 'on'}">中位数 {m['median_chg']:+.1f}%</span>
      <span class="chip {'off' if (m['amount_rank_250'] or 0)<60 else 'warn'}">成交分位 {m['amount_rank_250']}%</span>
    </div>
  </div>
  <div class="kpis">
    <div class="kpi"><div class="k">涨跌家数</div><div class="v">{m['up']} <span style="font-size:15px;color:var(--dim)">/</span> {m['down']}</div><div class="s">上涨占比 {m['up_ratio']}%</div></div>
    <div class="kpi"><div class="k">涨停 / 跌停</div><div class="v" style="color:var(--red)">{m['limit_up']} <span style="font-size:15px;color:var(--dim)">/</span> {m['limit_down']}</div><div class="s">极端结构参考</div></div>
    <div class="kpi"><div class="k">全A 成交</div><div class="v">{(m['amount_yi_all'] or 0)/1e4:.2f}<span style="font-size:14px;color:var(--dim)"> 万亿</span></div><div class="s">沪深300 {(m['amount_yi'] or 0):,.0f} 亿</div></div>
    <div class="kpi"><div class="k">融资余额</div><div class="v">{(mg.get('fin_bal_yi') or 0):,.0f}<span style="font-size:14px;color:var(--dim)"> 亿</span></div><div class="s">20日 {(mg.get('chg20_pct') or 0):+.2f}% · {esc(mg.get('date',''))}</div></div>
  </div>
</div>

<main>

<section>
  <h2><span class="idx">1</span>五维拆解<span class="badge {'red' if heat>=70 else ('warn' if heat>=55 else 'ok')}">{esc(e['stage'])}</span></h2>
  <p class="lede">
    情绪温度由五个维度合成，原始分 {e['raw_score']:+d}，平移后落在 0–100。
    宽度与涨幅代表赚钱效应的扩散程度，量能与杠杆代表资金参与度，波动率代表恐慌是否消退。
  </p>
  <div class="card">
    {parts_html}
    <p style="color:var(--dim2);font-size:12px;margin:16px 0 0">
      正分推高情绪温度（偏热），负分压低（偏冷）。宽度上限 45 分，涨幅上限 35 分，量能 20 分，杠杆 ±18 分，波动 ±10 分。
    </p>
  </div>
  <div class="card">
    <h3 style="margin:0 0 14px;font-size:14px;color:var(--dim);letter-spacing:.05em">阶段刻度</h3>
    <div style="display:flex;flex-wrap:wrap">{stage_scale}</div>
  </div>
</section>

<section>
  <h2><span class="idx">2</span>明细数据</h2>
  <div class="card">
    <div class="scroll">
      <table>
        <caption>各指标数据日期见行内标注，非全部为 {as_of}</caption>
        <thead><tr><th scope="col">维度</th><th scope="col">指标</th><th scope="col" class="num">数值</th><th scope="col">读数含义</th></tr></thead>
        <tbody>
          <tr><td>宽度</td><td>涨跌家数</td><td class="num">{m['up']} / {m['down']}</td><td>上涨占比 {m['up_ratio']}%</td></tr>
          <tr><td>宽度</td><td>涨停 / 跌停</td><td class="num strong">{m['limit_up']} / {m['limit_down']}</td><td>极端偏多程度看涨跌停比</td></tr>
          <tr><td>涨幅</td><td>个股涨跌中位数</td><td class="num strong">{m['median_chg']:+.1f}%</td><td>赚钱效应扩散程度</td></tr>
          <tr><td>量能</td><td>沪深300 成交额</td><td class="num">{(m['amount_yi'] or 0):,.0f} 亿</td><td>250 日分位 {m['amount_rank_250']}%</td></tr>
          <tr><td>量能</td><td>全A 成交额</td><td class="num">{(m['amount_yi_all'] or 0)/1e4:.2f} 万亿</td><td>全市场资金参与度</td></tr>
          <tr><td>杠杆</td><td>融资余额（{esc(mg.get('date',''))}）</td><td class="num">{mg.get('fin_bal_yi',0):,.0f} 亿</td><td>20 日 {mg.get('chg20_pct',0):+.2f}%</td></tr>
          <tr><td>杠杆</td><td>维持担保比例</td><td class="num">{mg.get('maint_ratio',0):.1f}%</td><td>安全垫厚度</td></tr>
          <tr><td>波动</td><td>300ETF QVIX（{esc(qv.get('date',''))}）</td><td class="num">{qv.get('value',0):.2f}</td><td>250 日分位 {qv.get('rank_250')}%，高波动=恐慌</td></tr>
          <tr><td>位置</td><td>距 250 日高回撤</td><td class="num">{m['drawdown_from_250d_high_pct']:.1f}%</td><td>指数在周期中的位置</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</section>

<section>
  <h2><span class="idx">3</span>与出清周期的关系</h2>
  <div class="card">
    <ul class="pts">
      <li><b>情绪低点不等于出清完成。</b>出清末端确实会出现情绪冰点，但「低点已过」只是反弹开始，周期是否结束由 MA40/MA80 决定。</li>
      <li><b>当前情绪位置：{esc(e['stage'])}（{heat}/100）</b>。涨停 {m['limit_up']} 只、个股中位数 {m['median_chg']:+.1f}%，宽度与涨幅合计贡献 {e['parts'].get('宽度(涨停)',0)+e['parts'].get('涨幅(中位数)',0):+d} 分。</li>
      <li><b>杠杆方向：</b>融资余额 20 日 {mg.get('chg20_pct',0):+.2f}%，{('杠杆仍在去化，情绪高潮的持续性要打折扣' if (mg.get('chg20_pct') or 0) < 0 else '杠杆资金同步参与，持续性更好')}。</li>
      <li><b>波动率：</b>QVIX {qv.get('value',0):.1f}（{qv.get('rank_250')}% 分位），期权市场对风险偏好的确认程度以此为准。</li>
    </ul>
  </div>
  <div class="callout red" style="margin-top:14px">
    <b>使用提醒：</b>情绪温度是<b>位置指标</b>不是<b>择时信号</b>。过热读数只说明当前不在低吸区，不代表马上要跌；
    冰点读数也不代表马上要涨。把它当作「现在处于周期哪一段」的地图，方向判断仍然回到均线与资金。
  </div>
</section>

</main>
"""
    return page(f"情绪周期监控 · {as_of}", as_of, body)


def main():
    os.makedirs(REPORT_DIR, exist_ok=True)
    c = load("cycle.json")
    e = load("emotion.json")
    as_of = c["as_of"]
    outs = []
    for name, html in [
        (f"出清周期监控_{as_of}.html", build_cycle(c)),
        (f"情绪周期监控_{as_of}.html", build_emotion(e)),
    ]:
        p = os.path.join(REPORT_DIR, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(html)
        outs.append(p)
        print(p)
    return outs


if __name__ == "__main__":
    main()
