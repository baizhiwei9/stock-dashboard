#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成「指标 × 事件窗口」预警有效性报告（自包含单文件 HTML，无外链、无 CDN）。

输入：rmt_episode_stats.json（统计结果）+ rmt_hist_indicators.json（指数序列与指标）
输出：rmt_episode_report_YYYYMMDD.html
"""
import os
import json
from datetime import datetime

import numpy as np
import pandas as pd

WORK = os.path.dirname(os.path.abspath(__file__))
ST = json.load(open(os.path.join(WORK, 'rmt_episode_stats.json'), encoding='utf-8'))
IND = json.load(open(os.path.join(WORK, 'rmt_hist_indicators.json'), encoding='utf-8'))
ROWS = pd.DataFrame(ST['rows'])
WIN = ST['windows']
CFG = ST['criteria']
IDF = pd.DataFrame(IND['rows'])

TODAY = IDF['date'].iloc[-1].replace('-', '')


def pct(v, nd=2):
    return '—' if v is None or (isinstance(v, float) and np.isnan(v)) else f'{v * 100:+.{nd}f}%'


def pp(v):
    return '—' if v is None or (isinstance(v, float) and np.isnan(v)) else f'{v:+.2f}pp'


# ---------------- 指数走势 SVG（标出四段窗口） ----------------
def svg_index():
    d = IDF[IDF['index_close'].notna()].reset_index(drop=True)
    if d.empty:
        return '<p>指数序列缺失</p>'
    W, H, ml, mr, mt, mb = 1000, 250, 52, 20, 18, 40
    x0, x1, y0, y1 = ml, W - mr, mt, H - mb
    lo, hi = float(d['index_close'].min()), float(d['index_close'].max())
    lo, hi = lo - (hi - lo) * 0.05, hi + (hi - lo) * 0.05
    dates = list(d['date'])
    pos = {dt: i for i, dt in enumerate(dates)}

    def X(dt):
        i = pos.get(dt)
        return None if i is None else x0 + i / max(1, len(dates) - 1) * (x1 - x0)

    def Y(v):
        return y1 - (v - lo) / (hi - lo) * (y1 - y0)

    pts = ' '.join(f'{X(dt):.1f},{Y(v):.1f}' for dt, v in zip(dates, d['index_close']))
    shade = []
    for w in WIN:
        xa, xb = X(w['anchor']), X(w['end'])
        if xa is None or xb is None:
            continue
        col = '#f6d5cf' if w['dir'] == 'down' else '#d6ead9'
        shade.append(f'<rect x="{min(xa, xb):.1f}" y="{y0}" width="{abs(xb - xa):.1f}" '
                     f'height="{y1 - y0}" fill="{col}" opacity="0.75"/>')
        mid = (min(xa, xb) + abs(xb - xa) / 2)
        shade.append(f'<text x="{mid:.0f}" y="{y0 + 12}" font-size="11" fill="#6b7280" '
                     f'text-anchor="middle">{w["key"]} {w["name"][5:]}</text>')
    yticks = ''
    for frac in (0, .5, 1):
        v = lo + frac * (hi - lo)
        y = Y(v)
        yticks += (f'<line x1="{x0}" y1="{y:.1f}" x2="{x1}" y2="{y:.1f}" stroke="#eef0f3"/>'
                   f'<text x="{x0 - 6}" y="{y + 4:.1f}" font-size="11" fill="#6b7280" '
                   f'text-anchor="end">{v:.0f}</text>')
    xticks = ''
    for k in range(6):
        i = int(round(k * (len(dates) - 1) / 5))
        xticks += (f'<text x="{x0 + i / (len(dates) - 1) * (x1 - x0):.0f}" y="{y1 + 16}" '
                   f'font-size="11" fill="#6b7280" text-anchor="middle">{dates[i][2:]}</text>')
    return (f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" '
            f'aria-label="全A 指数与四段事件窗口">{yticks}{"".join(shade)}'
            f'<polyline points="{pts}" fill="none" stroke="#1f4e79" stroke-width="1.6"/>'
            f'<line x1="{x0}" y1="{y1}" x2="{x1}" y2="{y1}" stroke="#c8ced6"/>'
            f'{xticks}</svg>')


# ---------------- 主表 ----------------
def _row(label):
    m = ROWS[ROWS['信号'].str.startswith(label)]
    return m.iloc[0] if len(m) else None


def _fmt_fwd(r):
    if r is None:
        return '—'
    d = r.get('前瞻差(pp)')
    base = f"触发后 20 日平均 {pct(r.get('全样本触发后20日'))}"
    if d is None:
        return base
    rel = '高' if d > 0 else '低'
    return f"{base}、比基准{rel} {abs(d):.2f}pp"


R_L1 = _row('λ₁ 250日分位')
R_EG = _row('复杂度缺口')
R_RH = _row('⟨r⟩ > 0.47')
R_GN = _row('Δ收窄：gap₁ 5日变化')
R_IPR = _row('IPR > 0.15')
R_DS = _row('ΔS < −0.10')
R_GNQ = _row('Δ收窄 250日分位')
L1_DAYS = int(round((R_L1['全样本触发占比'] / 100.0) * len(IDF))) if R_L1 is not None else 0


def main_table():
    th = ('<tr><th>指标（口径）</th><th>类别</th><th>全样本触发占比</th><th>触发后 20 日'
          '（vs 基准 +0.45%）</th><th>判定</th>' +
          ''.join(f'<th>{w["key"]} {w["name"]}</th>' for w in WIN) + '</tr>')
    tr = ''
    for _, r in ROWS.iterrows():
        cells = ''
        for w in WIN:
            k = w['key']
            v = r.get(k, '')
            days = r.get(f'{k}_days')
            when = r.get(f'{k}_when')
            fwd = r.get(f'{k}_fwd')
            if v in ('无',) or (isinstance(v, str) and v.startswith('无')):
                cells += '<td class="no">无</td>'
            elif v == '未实现':
                cells += '<td class="dim">未实现</td>'
            elif v == '锚点缺失':
                cells += '<td class="dim">锚点缺失</td>'
            else:
                extra = []
                if isinstance(days, (int, float)) and not (isinstance(days, float) and np.isnan(days)):
                    if days > 0:
                        extra.append(f'提前 {int(days)} 日')
                    elif days < 0:
                        extra.append(f'滞后 {abs(int(days))} 日')
                if when and isinstance(when, str):
                    extra.append(f'首触 {when}')
                if fwd is not None and not (isinstance(fwd, float) and np.isnan(fwd)):
                    extra.append(f'触发后20日 {pct(fwd)}')
                cls = 'yes' if isinstance(v, str) and v.startswith('领先') else 'mid'
                cells += f'<td class="{cls}">{v}<span class="sub">{"　".join(extra)}</span></td>'
        judge = str(r.get('判定', ''))
        jcls = 'yes' if judge.startswith('有前瞻区分度') else ('no' if ('常态' in judge or '极罕' in judge
                                                                     or '相反' in judge) else 'mid')
        tr += (f"<tr><td><b>{r['信号']}</b><span class='sub'>{r.get('含义', '')}</span></td>"
               f"<td>{r.get('类别', '')}</td><td>{r.get('全样本触发占比', '—')}%</td>"
               f"<td>{pct(r.get('全样本触发后20日'))}　<span class='sub'>差 {pp(r.get('前瞻差(pp)'))}</span></td>"
               f"<td class='{jcls}'>{judge}</td>{cells}</tr>")
    return f'<table class="main">{th}{tr}</table>'


def window_cards():
    out = ''
    for w in WIN:
        k = w['key']
        rows = ''
        for _, r in ROWS.iterrows():
            if not str(r.get(k, '')).startswith('领先'):
                continue
            days = r.get(f'{k}_days')
            when = r.get(f'{k}_when')
            bits = []
            if isinstance(when, str) and when:
                bits.append(f'首触 {when}')
            if (isinstance(days, (int, float)) and not (isinstance(days, float) and np.isnan(days))
                    and days > 0):
                bits.append(f'提前 {int(days)} 个交易日')
            rows += (f"<tr><td>{r['信号']}</td><td>{r.get('判定', '')}</td>"
                     f"<td>{'　'.join(bits)}</td></tr>")
        if not rows:
            rows = '<tr><td colspan="3" class="dim">没有指标在该窗口前 20 个交易日内领先触发</td></tr>'
        out += (f"<div class='card'><h3>{w['key']} · {w['name']}"
                f"<span class='sub'>{w['note']}</span></h3>"
                f"<table><tr><th>领先触发的指标</th><th>全样本判定</th><th>首次触发与提前量</th></tr>"
                f"{rows}</table></div>")
    return out


SM = IDF[IDF['index_close'].notna()]
html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>RMT/CGAP 指标对四段行情的预警有效性（{TODAY}）</title>
<style>
 body{{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:#f7f8fa;
   color:#1f2430;margin:0;padding:24px;line-height:1.6}}
 .wrap{{max-width:1180px;margin:0 auto}}
 h1{{font-size:22px;margin:0 0 6px}} h2{{font-size:17px;margin:22px 0 8px}}
 h3{{font-size:15px;margin:0 0 8px}} .sub{{display:block;color:#8a93a0;font-size:12px;font-weight:400}}
 .card{{background:#fff;border:1px solid #e6e9ee;border-radius:10px;padding:16px;margin:14px 0}}
 table{{border-collapse:collapse;width:100%;font-size:12.5px}}
 th,td{{border-bottom:1px solid #eef0f3;padding:6px 8px;text-align:left;vertical-align:top}}
 th{{background:#f2f4f7;font-weight:600;white-space:nowrap}}
 td.yes{{background:#eef7ee}} td.no{{background:#fbeceb;color:#8a2b20}}
 td.mid{{background:#fff9ec}} td.dim{{background:#f4f5f7;color:#8a93a0}}
 .sum{{background:#fff;border-left:4px solid #1f4e79}}
 ul{{margin:6px 0 0 18px;padding:0}} li{{margin:3px 0}}
 .note{{color:#5d6675;font-size:12.5px}}
</style></head><body><div class="wrap">
<h1>RMT / CGAP 指标对四段行情的预警有效性</h1>
<div class="note">数据截止 {IDF['date'].iloc[-1]}　样本 {len(IDF)} 个交易日（{IDF['date'].iloc[0]} → {IDF['date'].iloc[-1]}），
其中带指数收盘 {len(SM)} 天　基准：全样本 20 日前瞻全A 平均 +0.45%、平均最大回撤 −3.65%
（口径：{CFG['roll']} 日滚动分位；领先 = 锚点前 {CFG['pre_win']} 个交易日内首触且提前 ≥{CFG['lead_min']} 日）</div>

<div class="card sum">
<b>结论摘要</b>
<ul>
<li><b>能预警 7 月下跌的</b>：复杂度缺口 250 日分位 &gt;90%（6/15 首触，提前 4 个交易日，该次触发后 20 日全A −4.09%）；
λ₁ 250 日分位 &gt;90% 在这一波<b>没有</b>触发（所以 7 月这波它没给出信号）。</li>
<li><b>能预警 10 月下跌的</b>：λ₁ 250 日分位 &gt;90% 与复杂度缺口 250 日分位 &gt;90% 在 8 月上旬就已进入预警状态、
一路亮到 9/1 的峰（提前 ≥19 个交易日）：λ₁ {_fmt_fwd(R_L1)}；复杂度缺口 {_fmt_fwd(R_EG)}。</li>
<li><b>8 月上涨与近日暴涨</b>：没有任何"风险型"指标提前看多——它们在这两段还在亮红灯（属误报）；
唯一有前瞻区分度的机会型指标是 Δ收窄 250 日分位 &gt;90%{('（' + _fmt_fwd(R_GNQ) + '）') if R_GNQ is not None else ''}，
它在两段上涨前都已触发。</li>
<li><b>不能预警的</b>：C(t)&gt;35%（100% 时间恒亮）、⟨r⟩&gt;0.47（{R_RH['全样本触发占比'] if R_RH is not None else '—'}%）、
Δ收窄&gt;0（{R_GN['全样本触发占比'] if R_GN is not None else '—'}%）——常态触发，亮着不代表预警；
IPR&gt;0.15（{R_IPR['全样本触发占比'] if R_IPR is not None else '—'}%）与 ΔS&lt;−0.10（{R_DS['全样本触发占比'] if R_DS is not None else '—'}%）从不/极罕触发；
τ 隧穿<b>未实现</b>（图上未给 ε 与放量口径）。λ₁ 分位信号全样本共触发 {L1_DAYS} 天。</li>
</ul>
</div>

<h2>指数走势与四段事件窗口</h2>
<div class="card">{svg_index()}
<div class="note">阴影为四段事件窗口：{ '；'.join(w['key'] + ' ' + w['name'] + '（' + w['note'] + '）' for w in WIN) }</div></div>

<h2>指标 × 事件窗口 对照表</h2>
<div class="card">{main_table()}
<div class="note">单元格含义：领先 = 锚点前 20 个交易日内首触且提前 ≥3 个交易日；同步 = 锚点后 0~5 个交易日内首触；
无 = 两段都不触发。「全样本触发后 20 日」为所有触发日的平均前瞻全A 收益，判定按与基准（+0.45%）之差 ≥2pp 记有区分度。</div></div>

<h2>各窗口领先触发明细</h2>
{window_cards()}

<h2>口径与限制</h2>
<div class="card note">
<ul>
<li><b>⟨r⟩ 两种口径</b>：图上为"越轨特征值相邻间距比"，但在 18 只标的的池子上越轨特征值中位只有 2 个、凑不出间距比统计，因此本报告用
<b>体内谱（≤λ+）展开后的相邻间距比均值</b>（RMT 文献标准 r̄：泊松≈0.386、GOE≈0.531；本池中位 0.506，最新 {IDF['rbar'].iloc[-1]:.4f}），
图上 0.43/0.47 的阈值按此口径使用；越轨口径的结果单独记录（可用天数 {int(IDF['rbar_out'].notna().sum())} 天）。</li>
<li><b>复杂度缺口</b> = ln(N) − 熵（N=18），<b>Δ收窄</b> = gap₁ 的 5 交易日变化（gap₁=(λ₁−λ₂)/λ₁），两者均为本报告新增的可复现定义，替代图上未给口径的描述。</li>
<li><b>τ 隧穿未实现</b>：图上没有 ε 价位带宽与"放量天数"的判定口径，不臆造，故标"未实现"、不参与结论。</li>
<li><b>样本与样本外风险</b>：全样本 539 个交易日（其中带指数收盘 469 天），事件窗口只有 4 段；
λ₁ 分位信号全样本共 {L1_DAYS} 天触发（{R_L1['全样本触发占比'] if R_L1 is not None else '—'}%），
且 9 月那一波属"高位持续亮灯"而非新触发，样本外不保证同样有效。</li>
<li>所有数字由 <code>rmt_hist_indicators.py</code>（与线上 λ₁ 交叉校验最大差 {IND['cross_check']['max_abs_d_lambda1']:.0e}）与
<code>rmt_episode_predict.py</code> 生成，明细见 <code>rmt_episode_stats.csv</code>。</li>
</ul>
</div>

<div class="note" style="margin:18px 0 40px">数据来源：腾讯行情（前复权日线）重建 18 只行业/宽基 ETF 的 60 交易日滚动相关矩阵；
指数为全A（中证全指 000985）。生成时间 {datetime.now().strftime('%Y-%m-%d %H:%M')}。</div>
</div></body></html>"""

out = os.path.join(WORK, f'rmt_episode_report_{TODAY}.html')
open(out, 'w', encoding='utf-8').write(html)
print(f'HTML -> {out}  ({os.path.getsize(out) / 1024:.0f} KB)')