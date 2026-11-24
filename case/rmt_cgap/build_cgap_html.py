#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成「市场临界监测 C(t) & Gap」自包含单文件 HTML（内联 ECharts，无外链）。

输入：rmt_cgap_history.json（逐日 λ1/λ2/C/Gap/IPR/Δλ1/分位） + rmt_cgap_today.json
输出：market_cgap_monitor_YYYYMMDD.html
"""
import os, sys, json, numpy as np
from datetime import datetime

WORK = os.path.dirname(os.path.abspath(__file__))
HIST = os.path.join(WORK, 'rmt_cgap_history.json')
TODAY = os.path.join(WORK, 'rmt_cgap_today.json')
ECHARTS = os.path.join(WORK, 'echarts.min.js')
SHOW_DAYS = 62           # 图上展示的交易日数（约 3 个月，与卡片图一致）

with open(HIST, encoding='utf-8') as f:
    hist = json.load(f)
with open(TODAY, encoding='utf-8') as f:
    t = json.load(f)

dates_all = [r['date'] for r in hist]
C_all = [r['C'] for r in hist]
G_all = [r['Gap'] for r in hist]
l1_all = [r['lambda1'] for r in hist]

n = min(SHOW_DAYS, len(hist))
seg = hist[-n:]
x = [r['date'][5:] for r in seg]
C = [round(r['C'], 3) for r in seg]
G = [round(r['Gap'], 4) for r in seg]
IV = [None if r.get('index_close') is None else round(r['index_close'], 2) for r in seg]

d_l1 = t['d_lambda1']
vol = t['vol_ratio']
q250 = t['q250']

from cgap_rules import evaluate_rules
from cgap_warning_power import warning_power

R = evaluate_rules(t, hist)
WP = warning_power(hist)
level, tag, color, reasons = R['level'], R['tag'], R['color'], R['reasons']

# ---- 退潮诊断 / 分布分位 / 建议 / 明日观察：统一来自 cgap_rules.evaluate_rules ----
retreat, advice, watch = R['retreat'], R['advice'], R['watch']
Cfull, Gfull = R['Cfull'], R['Gfull']
Crank, Grank, Crank250, Grank250 = R['Crank'], R['Grank'], R['Crank250'], R['Grank250']

# ---- 新增：预警条件逐条核对（按 已触发/未触发/数据未知 分组）----
groups = {'已触发': [], '未触发': [], '数据未知': []}
for c in R['conditions']:
    groups[c['status']].append(c)


def cond_table(rows):
    if not rows:
        return '<p class="note">无</p>'
    tr = ''.join(
        f"<tr><td>{c['level']}</td><td>{c['name']}</td><td><b>{c['current']}</b></td>"
        f"<td>{c['threshold']}</td><td>{c['data_date']}</td><td>{c['note']}</td></tr>"
        for c in rows)
    return ('<table><tr><th>级别</th><th>条件</th><th>当前值</th><th>阈值</th>'
            '<th>数据日期</th><th>说明</th></tr>' + tr + '</table>')


group_html = ''.join(f"<h3>{k}（{len(v)} 条）</h3>{cond_table(v)}" for k, v in groups.items())

# ---- 新增：预警历史与连续天数 ----
lh = R['level_history']
hist10_html = ''.join(
    f"<tr><td>{x['date']}</td><td>{x['C']:.2f}%</td><td>{x['Gap']:.3f}</td>"
    f"<td>{(('%+.4f' % x['d_lambda1']) if x['d_lambda1'] is not None else 'n/a')}</td>"
    f"<td>{x['level']}</td></tr>" for x in lh['last10'])
all_same = lh['streak'] >= len(lh['levels'])
hist_note = ('（当前等级自序列起点起从未改变——绝对阈值在本池恒定触发，'
             '这本身说明 15/25/35 刻度对本池判别力有限）' if all_same else
             f"（上一次出现不同等级：{lh['prev_diff']}）")
recent_seq = ' → '.join(lh['recent_levels'])

# ---- 新增：全A 与 C(t) 背离提示 ----
dv = R['divergence']
dv_txt = (f"{dv['text']}　C(t) {dv['win']} 日 {dv['dC']:+.2f} 个百分点"
          f"（{dv['C_prev']:.2f}% → {dv['C_now']:.2f}%），全A {dv['win']} 日 {dv['dI']:+.2f}%"
          f"（{dv['idx_prev']:,.2f} → {dv['idx_now']:,.2f}）") if dv.get('ok') else dv['text']

# ---- 新增：退潮诊断详情 ----
rd = R['retreat_detail']
rd_txt = f"{rd['text']}<br><span class='note'>{rd.get('detail', '')}</span>"

# ---- 新增：本池自身分位刻度参照 ----
pct_rows = ''.join(
    f"<tr><td>{r['name']}</td><td>{r['p50']:.2f}</td><td>{r['p75']:.2f}</td><td>{r['p90']:.2f}</td>"
    f"<td>{r['p95']:.2f}</td><td>{r['min']:.2f} – {r['max']:.2f}</td><td><b>{r['cur']:.3f}</b></td>"
    f"<td>{r['rank_full']:.1f}% / {r['rank250']:.1f}%</td></tr>"
    for r in R['percentile_ref']['rows'])

vol_txt = '不可用（当日成交量接口未更新，覆盖率 %s）' % t.get('vol_coverage', 'n/a') if vol is None \
    else f"{vol:.3f}"

# ---- 全A指数（叠加对照，不进相关矩阵）----
idx_name = t.get('index_name', '全A')
idx_close, idx_chg, idx_mom = t.get('index_close'), t.get('index_chg_1'), t.get('index_mom_20')
idx_close_txt = f"{idx_close:,.2f}" if idx_close is not None else '缺失'
idx_chg_txt = f"{idx_chg * 100:+.2f}%" if idx_chg is not None else 'n/a'
idx_mom_txt = f"{idx_mom * 100:+.2f}%" if idx_mom is not None else 'n/a'
iv_missing = sum(1 for r in seg if r.get('index_close') is None)

# ---- 机会侧（池内多维排序；只做派生计算，不改动任何预警口径）----
opp = t.get('opportunity')


def _rs_bar(v):
    """20 日超额（相对全A）水平条：正红负绿，单侧 20 个百分点映射为满宽。"""
    if v is None:
        return '<span class="note">n/a</span>'
    w = min(abs(v) / 0.20, 1.0) * 50.0
    color = '#c0392b' if v >= 0 else '#1e8449'
    anchor = 'left:50%' if v >= 0 else 'right:50%'
    return (f'<span style="position:relative;display:inline-block;width:110px;height:11px;'
            f'background:#f2f4f7;border-radius:3px;vertical-align:middle">'
            f'<span style="position:absolute;{anchor};width:{w:.1f}%;height:11px;'
            f'background:{color};border-radius:3px"></span></span>'
            f'<span class="note"> {v * 100:+.2f}%</span>')


if opp and opp.get('rows'):
    _orows = []
    for r in opp['rows']:
        volr_txt = 'n/a' if r['volr'] is None else f"{r['volr']:.2f}"
        corr_txt = 'n/a' if r['corr_avg'] is None else f"{r['corr_avg']:+.2f}"
        pos60_txt = 'n/a' if r['pos60'] is None else f"{r['pos60'] * 100:+.1f}%"
        pos250_txt = 'n/a' if r['pos250'] is None else f"{r['pos250'] * 100:.0f}%"
        _orows.append(
            f"<tr><td>{r['name']} <span class='note'>{r['code']}</span></td>"
            f"<td>{r['close']:.3f}</td><td>{r['chg1'] * 100:+.2f}%</td>"
            f"<td>{_rs_bar(r['rs20'])}</td>"
            f"<td>{'站上 MA20' if r['above_ma20'] else '跌破 MA20'}"
            f"{' · MA20&gt;MA60' if r['ma20_gt_ma60'] else ' · MA20&lt;MA60'}</td>"
            f"<td>{volr_txt}</td><td>{corr_txt}</td><td>{pos60_txt}</td><td>{pos250_txt}</td>"
            f"<td><b>{r['score']:.2f}</b></td><td>{r['grade']}</td></tr>")
    opp_rows_html = ''.join(_orows)
    _att = '、'.join(x['name'] for x in opp['rows'] if x['grade'] == '关注')
    opp_headline = (f"机会：{'、'.join(opp['strong']) if opp['strong'] else '今日无'}　|　"
                    f"关注：{_att or '无'}　|　"
                    f"相对弱势（仅对照）：{'、'.join(opp['weak']) if opp['weak'] else '无'}")
else:
    opp_rows_html = ''
    opp_headline = '数据不足，未生成'

# ---- 机会侧行业下钻（宽泛方向 → 一级/二级/三级行业 → 个股）----
drill = t.get('drilldown') or {}


def _t(v, spec, dft='n/a'):
    return dft if v is None else format(v, spec)


drill_cards = []
if drill.get('directions'):
    for b in drill['directions']:
        crumbs = (f"<b>{b['direction']}</b>"
                  + ''.join(f" <span class='note'>→</span> {p['level']} <b>{p['name']}</b>"
                            for p in b.get('path', [])))
        lv_html = ''
        for lv in b.get('levels', []):
            cand = '、'.join(f"{c['name']} <b>{c['score']:.2f}</b>" for c in lv['candidates'])
            lv_html += (
                f"<div class='note' style='margin:6px 0'><b>{lv['level']}行业</b>（{lv['data_date']}，"
                f"{len(lv['candidates'])} 个候选）：{cand}"
                f"　<span style='color:#7b2d26'><b>→ 胜出 {lv['winner']['name']}</b></span>"
                f"（20日超额 {lv['winner']['rs20'] * 100:+.2f}%，量能比 {lv['winner']['volr']:.2f}，"
                f"趋势 {lv['winner']['trend']}/2）</div>")
        st = b.get('stocks') or {}
        srows = ''
        for sx in st.get('selected', []):
            srows += (
                f"<tr><td>{sx['rank']}</td>"
                f"<td>{sx['name']} <span class='note'>{sx['code']}</span></td>"
                f"<td><b>{sx['score']:.2f}</b></td>"
                f"<td>{_t(sx['rs20'], '+.2%')}</td>"
                f"<td>{_t(sx['volr'], '.2f')}</td>"
                f"<td>{sx['trend']}/2</td>"
                f"<td>{_t(sx['mktcap'], '.0f')}</td>"
                f"<td>{_t(sx['pe_ttm'], '.1f')}</td>"
                f"<td>{_t(sx['np_growth'], '+.1f')}</td>"
                f"<td class='note'>{sx['basis']}</td></tr>")
        st_note = ''
        if st:
            st_note = (f"该三级行业共 {st['total']} 只成分股，上表为综合分前 {st['top_n']} 只；"
                       f"成分股等权 20 日超额 {_t(st['agg']['equal_weight_rs20'], '+.2%')}"
                       f"（数据日 {st['data_date']}，其中 {st['realtime_filled']} 只当日 K 线由实时行情补齐）。")
        drill_cards.append(
            "<div class='card'>"
            f"<h2>行业下钻 · {b['direction']}：宽泛方向 → 三级行业 → 个股</h2>"
            f"<div class='note'>路径：{crumbs}</div>"
            f"{lv_html}"
            "<table><tr><th>排名</th><th>个股</th><th>综合分</th><th>20日超额</th><th>量能比 5/20</th>"
            "<th>趋势</th><th>市值(亿)</th><th>PE(TTM)</th><th>净利增速%</th><th>入选依据</th></tr>"
            f"{srows}</table>"
            f"<div class='note' style='margin-top:8px'>{st_note}</div>"
            "</div>")
    drill_section = ''.join(drill_cards)
elif drill.get('error'):
    drill_section = ("<div class='card'><h2>行业下钻 · 机会侧宽泛方向 → 三级行业 → 个股</h2>"
                     f"<div class='note'>本次执行失败：{drill['error']}（如实记录，不用旧值顶替）</div></div>")
else:
    drill_section = ("<div class='card'><h2>行业下钻 · 机会侧宽泛方向 → 三级行业 → 个股</h2>"
                     "<div class='note'>当日机会档无行业方向（宽基/红利不参与下钻），本次跳过。</div></div>")

# ---- 图上“能预警到哪一步”的实测结论（口径唯一来源：cgap_warning_power）----
if WP.get('ok'):
    _ws = WP['sample']
    WP_DAYS = _ws['days']
    WP_CRIT = WP['criterion']
    _r90 = next((r for r in WP['rows'] if r['key'] == 'q250>90'), None) or {}
    _rg3 = next((r for r in WP['rows'] if r['key'] == 'Gap<3'), None) or {}

    def _pp(v):
        return 'n/a' if v is None else f'{v * 100:+.2f}pp'

    R90_NON, R90_NOFF = _r90.get('n_on'), _r90.get('n_off')
    R90_FWD_ON, R90_DD_ON = _pp(_r90.get('fwd20_on')), _pp(_r90.get('dd20_on'))
    R90_FWD_OFF, R90_DD_OFF = _pp(_r90.get('fwd20_off')), _pp(_r90.get('dd20_off'))
    G3_D_FWD = _pp(_rg3.get('d_fwd20'))
    WP_TBL = ''.join(
        f"<tr><td>{r['label']}</td><td>{r['kind']}</td><td>{r['trigger_pct']:.1f}%</td>"
        f"<td>{r['n_on']}/{r['n_off']}</td><td>{_pp(r['d_fwd20'])}</td><td>{_pp(r['d_dd20'])}</td>"
        f"<td>{'<b>保留</b>' if r['keep'] else '移除'}</td><td class='note'>{r['reason']}</td></tr>"
        for r in WP['rows'])
else:
    WP_DAYS = WP_CRIT = G3_D_FWD = 'n/a'
    R90_NON = R90_NOFF = R90_FWD_ON = R90_DD_ON = R90_FWD_OFF = R90_DD_OFF = 'n/a'
    WP_TBL = f"<tr><td colspan='8' class='note'>{WP.get('note', '不可判')}</td></tr>"
STREAK = R['level_history']['streak']

# ---- 图上三项展示指标（是什么 / 怎么算 / 临界 / 现值与差额 / 偏多偏空）----
SI = t.get('show_indicators') or {}
SI_ROWS = ''
for _it in SI.get('items', []):
    _val = _it.get('value')
    _pc = _it.get('percentile')
    _val_txt = ('—' if _val is None else
                (f"{_val:.1f}%" if _it.get('unit') == '%' else
                 (f"{_val:+.4f}" if _it.get('key') == 'gap_narrow' else f"{_val:.4f}")))
    _pc_txt = '—' if _pc is None else f"{_pc:.1f}%"
    _crit_txt = f"{_it.get('crit', 90.0):.0f}%"
    _lv = _it.get('crit_level')
    _lv_txt = '' if (_lv is None or _it.get('unit') == '%') else f"（对应水平 {_lv:.4f}）"
    _gap = _it.get('gap_to_crit')
    _gap_txt = ('—' if _gap is None else
                (f"超出 {_gap:+.1f} 个百分位" if _gap >= 0 else f"差 {abs(_gap):.1f} 个百分位"))
    _cls = 'no' if (_pc is not None and _pc >= 90 and _it.get('kind') == '风险预警') else 'mid'
    SI_ROWS += (f"<tr><td>{_it.get('name', '')}<span class='sub'>{_it.get('kind', '')}</span></td>"
                f"<td class='note'>{_it.get('formula', '')}</td>"
                f"<td>{_val_txt}<span class='sub'>自身 250 日分位 {_pc_txt}</span></td>"
                f"<td>{_crit_txt}{_lv_txt}<span class='sub'>{_it.get('crit_note', '')}</span></td>"
                f"<td class='{_cls}'>{_it.get('stance', '')}<span class='sub'>{_gap_txt}</span></td></tr>")
SI_HTML = ("<div class='card'><h3>图上三项指标：是什么 / 怎么算 / 临界怎么读</h3>"
           "<table><tr><th>指标（类别）</th><th>怎么算</th><th>当日值 / 自身 250 日分位</th>"
           "<th>临界与依据</th><th>离临界多远 · 偏多偏空</th></tr>" + SI_ROWS + "</table>"
           f"<div class='note' style='margin-top:8px'>{SI.get('note', '')}　"
           "三条曲线都换算成各自 250 日滚动分位，因此可同轴比较、共用同一条 90% 临界线；"
           "全A（中证全指 000985）只作对照、不参与判定。</div></div>")

# ---- 合并：RMT 预测监测（原 rmt_daily_monitor 的指标/灯/精选）----
RP = t.get('rmt_prediction') or {}
if RP.get('error'):
    PRED_HTML = ("<div class='card'><h2>RMT 预测监测（合并自 rmt_daily_monitor）</h2>"
                 f"<div class='note'>本次执行失败：{RP['error']}（如实记录，不用旧值顶替）</div></div>")
elif not RP:
    PRED_HTML = ("<div class='card'><h2>RMT 预测监测（合并自 rmt_daily_monitor）</h2>"
                 "<div class='note'>当日未产出预测内容。</div></div>")
else:
    _ind = RP.get('indicators') or {}
    _al = RP.get('alert') or {}
    _lvl = {'red': '红灯（风险）', 'green': '绿灯（机会）',
            'none': '无红/绿灯'}.get(_al.get('level', ''), str(_al.get('level')))
    _th = RP.get('thresholds') or {}

    def _n(v):
        return 'n/a' if v is None else f'{float(v):.4f}'

    _rows = ''.join(f"<tr><td>{k}</td><td>{_n(v)}</td></tr>" for k, v in _ind.items())
    _reasons = '；'.join(_al.get('reasons') or []) or '无'
    _watch = '；'.join(_al.get('watch') or []) or '无'
    _picks_html = ''
    for _it in RP.get('top5') or []:
        _p = _it.get('picks') or []
        _ptxt = '、'.join(f"{s.get('name')}({s.get('code')}) 20 日 {(s.get('mom_20') or 0) * 100:+.2f}%"
                          for s in _p) or '未取到'
        _picks_html += (f"<tr><td>{_it.get('name')}（{_it.get('code')}）</td>"
                        f"<td>{(_it.get('mom20') or 0) * 100:+.2f}%</td><td class='note'>{_ptxt}</td></tr>")
    _cc = RP.get('cross_check') or {}
    _cc_txt = '、'.join(f"{k} {v:+.1e}" for k, v in _cc.items() if isinstance(v, float)) or 'n/a'
    _bottom = '、'.join(str(x.get('name')) for x in (RP.get('bottom5') or [])) or 'n/a'
    PRED_HTML = (
        "<div class='card'><h2>RMT 预测监测（合并自 rmt_daily_monitor）</h2>"
        f"<div class='note'><b>红/黄/绿灯：{_lvl}</b>　（阈值：ΔS ≥ +{_th.get('red_dS', 0.08):.2f} 红灯、"
        f"≥ +{_th.get('warn_dS', 0.05):.2f} 黄灯、熵 ≤ {_th.get('green_entropy', 1.45):.2f} 绿灯、"
        f"ΔS ≤ {_th.get('green_dS', -0.06):.2f} 深绿灯）<br>触发原因：{_reasons}<br>黄灯观察：{_watch}<br>"
        f"状态：<b>{RP.get('status', 'n/a')}</b> · 仓位 {RP.get('position', 'n/a')} · {RP.get('action', 'n/a')}　"
        f"｜　最弱 5 个行业：{_bottom}</div>"
        "<table><tr><th>预测指标</th><th>数值</th></tr>" + _rows + "</table>"
        "<h3 style='margin-top:12px'>Top5 动量行业与前 3 只个股</h3>"
        "<table><tr><th>行业（ETF）</th><th>20 日动量</th><th>前 3 只个股（20 日动量）</th></tr>"
        + _picks_html + "</table>"
        f"<div class='note' style='margin-top:8px'>口径：指标与红/黄/绿灯沿用原 rmt_daily_monitor"
        f"（compute_rmt / evaluate_alert，阈值原样）；个股精选沿用 stock_picker.get_top_stocks_for_etf；"
        f"与本管线全谱指标的交叉核对差值：{_cc_txt}（≈0 表示同口径）。"
        f"该分区只作展示，不改动六条阈值规则、退潮诊断、机会侧与行业下钻。</div></div>")

html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>市场临界监测 C(t) &amp; Gap · {t['date']} · 来自 DoMate</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; padding: 24px; background: #f5f6f8; color: #1f2430;
         font-family: "Noto Sans CJK SC","Source Han Sans SC","PingFang SC","Microsoft YaHei",system-ui,sans-serif; }}
  .wrap {{ max-width: 1180px; margin: 0 auto; }}
  h1 {{ font-size: 22px; margin: 0 0 6px; font-weight: 700; letter-spacing: .3px; }}
  h1 .sign {{ font-size: 13px; font-weight: 400; color: #8a93a4; margin-left: 10px; }}
  .sub {{ font-size: 13px; color: #5d6675; margin-bottom: 16px; line-height: 1.7; }}
  .card {{ background: #fff; border-radius: 10px; padding: 16px 18px; margin-bottom: 14px;
           box-shadow: 0 1px 3px rgba(20,30,50,.08); }}
  .alert {{ border-left: 6px solid {color}; }}
  .alert .tag {{ font-size: 18px; font-weight: 700; color: {color}; }}
  .alert ul {{ margin: 8px 0 0; padding-left: 20px; font-size: 13.5px; line-height: 1.8; }}
  .kpis {{ display: flex; flex-wrap: wrap; gap: 10px; }}
  .kpi {{ flex: 1 1 150px; background: #fafbfc; border: 1px solid #e8eaee; border-radius: 8px;
          padding: 10px 12px; }}
  .kpi .k {{ font-size: 12px; color: #78818f; }}
  .kpi .v {{ font-size: 19px; font-weight: 700; margin-top: 4px; }}
  .kpi .v small {{ font-size: 12px; font-weight: 400; color: #78818f; }}
  #chart {{ width: 100%; height: 850px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ border-bottom: 1px solid #eceef2; padding: 7px 8px; text-align: left; }}
  th {{ color: #78818f; font-weight: 600; background: #fafbfc; }}
  .note {{ font-size: 12.5px; color: #6b7482; line-height: 1.85; }}
  .note b {{ color: #1f2430; }}
  h2 {{ font-size: 15.5px; margin: 0 0 10px; font-weight: 700; }}
  h3 {{ font-size: 13px; margin: 14px 0 6px; color: #5d6675; font-weight: 600; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>市场临界监测 C(t) &amp; Gap<span class="sign">来自 DoMate</span></h1>
  <div class="sub">
    数据日 <b>{t['date']}</b>（真实行情，收盘后取数）　|　标的池 <b>{t['n_assets']} 只</b>行业/宽基 ETF　|　
    窗口 <b>{t['window']} 交易日</b>滚动相关矩阵　|　叠加对照 <b>{idx_name}</b>（不进相关矩阵）　|　
    序列共 {len(hist)} 天，图上展示近 {n} 天
  </div>

  <div class="card alert">
    <div class="tag">{tag}</div>
    <ul>{''.join(f'<li>{r}</li>' for r in reasons)}</ul>
  </div>

  <div class="card">
    <div class="kpis">
      <div class="kpi"><div class="k">λ₁ 第一特征值</div><div class="v">{t['lambda1']:.4f}</div></div>
      <div class="kpi"><div class="k">λ₂ 第二特征值</div><div class="v">{t['lambda2']:.4f}</div></div>
      <div class="kpi"><div class="k">C(t) 市场协同度</div><div class="v">{t['C']:.2f}<small>%</small></div></div>
      <div class="kpi"><div class="k">Gap 主线集中度</div><div class="v">{t['Gap']:.3f}</div></div>
      <div class="kpi"><div class="k">Δλ₁ 一致性日变化</div><div class="v">{d_l1:+.4f}</div></div>
      <div class="kpi"><div class="k">IPR 龙头拥挤度</div><div class="v">{t['IPR']:.4f}</div></div>
      <div class="kpi"><div class="k">λ₁ 250日滚动分位</div><div class="v">{q250:.1f}<small>%</small></div></div>
      <div class="kpi"><div class="k">成交量变化率</div><div class="v">{vol_txt.split('（')[0]}<small>{('（' + vol_txt.split('（',1)[1]) if '（' in vol_txt else ''}</small></div></div>
      <div class="kpi"><div class="k">全A 指数收盘</div><div class="v">{idx_close_txt}<small> 当日 {idx_chg_txt}</small></div></div>
      <div class="kpi"><div class="k">全A 20日涨跌</div><div class="v">{idx_mom_txt}</div></div>
    </div>
  </div>

  <div class="card">
    <div id="chart"></div>
    <div class="note" style="margin-top:10px">
      <b>图上什么情况才算“能预警”</b>（对 {WP_DAYS} 个交易日逐项实测；判据：{WP_CRIT}）：<br>
      · <b>唯一具备前瞻区分度的一步 = λ₁（C(t)）250 日滚动分位 &gt; 90%</b>
        （即橙警条件中的分位项；因 C&gt;30% 在本池恒真，这一步等价于“橙警条件被满足”）。
        历史 <b>{R90_NON}/{WP_DAYS}</b> 天满足，其后 20 个交易日的全A 平均收益 <b>{R90_FWD_ON}</b>、
        前瞻 20 日最大回撤 <b>{R90_DD_ON}</b>；其余 {R90_NOFF} 天为 {R90_FWD_OFF} / {R90_DD_OFF}。
        因此图上只保留这条分位曲线与其 90% 分位线（另附 10% 低位参考线）。<br>
      · 全A（中证全指 000985）只作对照（市场本身），不参与任何阈值判定。<br>
      <b>已从图上移除、不再出现的指标</b>：C(t) 的 15/25/35% 三条线在样本内 <b>恒定触发</b>、
      Gap 的 10/5 线同样恒定触发、Gap 的 3 线触发后 20 日差异仅 {G3_D_FWD}、
      Δλ₁ 的 −5 线与 IPR 的 0.15 线 <b>从未触发</b>。这些指标仍逐条列在下方“预警条件逐条核对”
      与卡片指标区——<b>只有画图取舍，没有改口径</b>。<br>
      另注：因 C(t)&gt;35% 长期恒真，等级自序列起点即锁在红警（{STREAK}/{WP_DAYS} 天），
      真正携带增量信息的是上面那一步分位信号。
    </div>
    <h3 style="margin-top:14px">逐项检验明细（全部候选信号，可复算）</h3>
    <table>
      <tr><th>候选信号</th><th>类型</th><th>触发占比</th><th>触发/未触发（天）</th>
      <th>触发后 20 日全A 收益差</th><th>触发后 20 日回撤差</th><th>结论</th><th>依据</th></tr>
      {WP_TBL}
    </table>
  </div>

  {SI_HTML}

  <div class="card">
    <h2>预警条件逐条核对</h2>
    {group_html}
  </div>

  <div class="card">
    <h2>预警历史与连续天数</h2>
    <div class="note">
      当前等级 <b>{tag}</b> 已连续 <b>{lh['streak']}</b> 个交易日（自 <b>{lh['since']}</b> 起）{hist_note}；
      近 20 个交易日等级序列：{recent_seq}
    </div>
    <table>
      <tr><th>日期</th><th>C(t)</th><th>Gap</th><th>Δλ₁</th><th>等级</th></tr>
      {hist10_html}
    </table>
  </div>

  <div class="card">
    <h2>全A 与 C(t) 背离提示</h2>
    <div class="note">{dv_txt}</div>
  </div>

  <div class="card">
    <h2>退潮诊断详情</h2>
    <div class="note">{rd_txt}</div>
  </div>

  <div class="card">
    <h2>机会侧 · 池内多维排序（相对动量 / 趋势 / 量能扩张）</h2>
    <div class="note" style="margin-bottom:8px">{opp_headline}</div>
    <table>
      <tr><th>标的</th><th>现价</th><th>当日</th><th>20日超额（相对全A）</th><th>趋势结构</th>
      <th>量能比 5/20</th><th>池内协动</th><th>距60日高</th><th>250日位置</th><th>综合分</th><th>等级</th></tr>
      {opp_rows_html}
    </table>
    <div class="note" style="margin-top:8px">
      口径（同一批行情上的规则化排序，<b>非预测模型</b>，与风险侧并列展示、不改动任何预警口径）：
      综合分 = 0.40×池内百分位(RS20) + 0.30×(趋势分/2) + 0.30×池内百分位(量能比)；
      RS20 = 标的近 20 个交易日收益 − 全A 同期收益；趋势分 = 站上 MA20（+1）+ MA20&gt;MA60（+1）；
      量能比 = 近 5 日平均成交量 / 前 20 日平均成交量；池内协动 = 与池内其余 17 只在 60 日窗口的平均相关系数，越低越走独立行情。
      等级：综合分 ≥0.75 且 RS20&gt;0 且站上 MA20 为「机会」，≥0.55「关注」、≥0.40「观察」，其余「弱势」。
      「弱势」仅表示 20 日相对弱且未站上 MA20，<b>不是看空判断</b>；当日反弹（如半导体今日 +3.5%）不改变 20 日口径。
    </div>
  </div>

  {PRED_HTML}

  {drill_section}

  <div class="card note">
    <b>行业下钻口径</b>：宽泛方向 → 申万一级 → 二级 → 三级行业 → 该三级行业成分股。各层候选按
    0.40×层内百分位(20 日超额，相对全A) + 0.30×(趋势分/2，站上 MA20 与 MA20&gt;MA60 各 1 分) + 0.30×层内百分位(量能比 5/20)
    打分（层内百分位在该层候选集合内计算），胜出者进入下一层。行业层数据来自申万 2021 一级/二级/三级行业指数日线
    （akshare，源端最新可得日），因此行业层数据日期可能滞后一个交易日；个股层为腾讯日线，当日 K 线未发布时
    用实时行情补齐最后一根（价格与成交量同源同单位），故个股层为当日收盘口径。申万三级已是官方最细层级，
    下钻终点为「三级行业 → 其成分股」。个股层的市值、PE(TTM)、净利增速等字段直接取申万成分股表并标注报告期，
    <b>不作为打分输入</b>。该部分与风险侧、机会侧并列展示，<b>不改动任何预警口径</b>，属规则化排序、
    <b>非预测模型</b>，也<b>不构成买卖建议</b>。
  </div>

  <div class="card">
    <h2>分位刻度参照（本池自身分布，非你的绝对刻度）</h2>
    <table>
      <tr><th>指标</th><th>P50</th><th>P75</th><th>P90</th><th>P95</th><th>区间</th><th>当前值</th><th>全历史 / 近250日分位</th></tr>
      {pct_rows}
    </table>
    <div class="note" style="margin-top:8px">绝对刻度 15/25/35 与 10/5/3 在本池恒触发或不可能触发，因此判断强弱请以本表的自身分位为准。</div>
  </div>

  <div class="card">
    <table>
      <tr><th>结论与行动</th><th>结果</th></tr>
      <tr><td>预警等级</td><td><b style="color:{color}">{tag}</b></td></tr>
      <tr><td>操作建议</td><td>{advice}</td></tr>
      <tr><td>明日重点观察</td><td>{watch}</td></tr>
    </table>
  </div>

  <div class="card note">
    <b>口径说明</b>：C(t) = λ₁ / Σλ × 100（Σλ = 相关矩阵迹 = 标的数 18，即第一主成分吸收率）；
    Gap = λ₁ / λ₂；Δλ₁ = λ₁(t) − λ₁(t−1)；IPR = Σ(v₁ᵢ)⁴（第一特征向量参与率）；
    λ₁ 分位 = 当日 λ₁ 在最近 250 个交易日 λ₁ 中的百分位；
    成交量变化率 = 池内合计成交量（今日）/ 前 20 日均值（盘后日线未更新时，当日成交量由同一批标的的实时接口补齐，
    单位与日线一致；仍不足 80% 覆盖则标为不可用，不填 0、不用旧值顶替）；
    全A = {idx_name} 收盘价，只做叠加对照、不进入相关矩阵，与 C(t)/Gap 按同一交易日在位对齐
    （本窗口内缺失 {iv_missing}/{n} 天，缺失处断开不补值）；
    机会侧 = 同一批行情上的派生排序（相对动量 RS20 40% + 趋势 30% + 量能扩张 30%，各项取池内百分位加权），
    与风险侧并列展示、不改动预警口径，也<b>不构成买卖建议</b>。<br>
    <b>刻度提示</b>：15/25/35 三条 C 线与 10/5/3 三条 Gap 线在样本内恒触发或从未触发（见上方逐项检验），
    已从图上与图例中移除。现在图上是三个子图：1) λ₁（C(t)）250 日滚动分位（临界固定 90%，值 ≥ 90% → 看空）；
    2) 复杂度缺口（值 ≥ 近 250 日第 90 百分位水平 → 看空）；3) Δ收窄（值 ≥ 同一临界水平 → 看多，风格切换窗口）；
    每个子图右轴都是全A（中证全指 000985）对照。判断强弱请以子图内的临界线与下方分位刻度参照为准。
    本池 C(t) 全历史区间 {Cfull[0]:.2f}%–{Cfull[2]:.2f}%（中位 {Cfull[1]:.2f}%），
    Gap 全历史区间 {Gfull[0]:.3f}–{Gfull[2]:.3f}（中位 {Gfull[1]:.3f}）；
    当前 C 处全历史 {Crank:.1f} 分位 / 近 250 日 {Crank250:.1f} 分位，Gap 处全历史 {Grank:.1f} 分位 / 近 250 日 {Grank250:.1f} 分位。
    因标的池只有 {t['n_assets']} 只宽基/行业 ETF，第一主成分天然吸收约 42%–71%，
    "C>35%""Gap<5" 在本池恒成立、"Gap≥10" 不可能出现，六条线在本池的判别力有限；
    若要沿用原始绝对刻度，需把标的池换成更大横截面（如个股宽池或全部行业指数）。<br>
    <b>怎么看全A</b>：每个子图右侧的青色线都是全A，与左侧指标共用同一时间轴，可直接对照——指标抬升且全A同步走强/走弱，
    说明是全市场同向；指标抬升而全A走弱，则是"资金抱团少数标的、其余失血"的典型结构。
  </div>
  <div class="sub" style="text-align:center;color:#9aa2af">数据来源：腾讯行情（前复权日线 + 盘后实时兜底）　生成时间 {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
</div>

<script>{open(ECHARTS, encoding='utf-8').read()}</script>
<script>
var dates = {json.dumps(x, ensure_ascii=False)};
var qv = {json.dumps([None if r.get('q250') is None else round(r['q250'], 1) for r in seg])};
var ev = {json.dumps([None if r.get('entropy_gap') is None else round(r['entropy_gap'], 4) for r in seg])};
var ec = {json.dumps([None if r.get('crit_eg') is None else round(r['crit_eg'], 4) for r in seg])};
var dv = {json.dumps([None if r.get('d_gap1_5') is None else round(r['d_gap1_5'], 4) for r in seg])};
var dc = {json.dumps([None if r.get('crit_dg') is None else round(r['crit_dg'], 4) for r in seg])};
var iv = {json.dumps(IV)};
var chart = echarts.init(document.getElementById('chart'));
var CL = {{ color: '#e67e22', type: 'dashed', width: 2 }};
chart.setOption({{
  title: [
    {{ text: '1) λ₁（C(t)）250 日滚动分位（风险预警）', subtext: '看空：值 ≥ 90%（临界固定 90%）',
       left: 8, top: 2, textStyle: {{ fontSize: 14, color: '#1f4e79' }},
       subtextStyle: {{ fontSize: 11.5, color: '#5d6675' }} }},
    {{ text: '2) 复杂度缺口（ln(18) − 熵）（风险预警）',
       subtext: '看空：值 ≥ 临界水平（近 250 日第 90 百分位，随窗口滚动）',
       left: 8, top: '34%', textStyle: {{ fontSize: 14, color: '#7d3c98' }},
       subtextStyle: {{ fontSize: 11.5, color: '#5d6675' }} }},
    {{ text: '3) Δ收窄（gap₁ 的 5 交易日变化）（监控/机会）',
       subtext: '看多（机会）：值 ≥ 临界水平（近 250 日第 90 百分位，随窗口滚动）',
       left: 8, top: '67%', textStyle: {{ fontSize: 14, color: '#c2410c' }},
       subtextStyle: {{ fontSize: 11.5, color: '#5d6675' }} }}
  ],
  tooltip: {{ trigger: 'axis', axisPointer: {{ link: [{{ xAxisIndex: 'all' }}] }} }},
  grid: [
    {{ left: 74, right: 104, top: 86, height: 158 }},
    {{ left: 74, right: 104, top: 356, height: 158 }},
    {{ left: 74, right: 104, top: 626, height: 158 }}
  ],
  xAxis: [
    {{ type: 'category', gridIndex: 0, data: dates, boundaryGap: false,
       axisLabel: {{ show: false }}, axisTick: {{ show: false }} }},
    {{ type: 'category', gridIndex: 1, data: dates, boundaryGap: false,
       axisLabel: {{ show: false }}, axisTick: {{ show: false }} }},
    {{ type: 'category', gridIndex: 2, data: dates, boundaryGap: false,
       axisLabel: {{ fontSize: 10 }} }}
  ],
  yAxis: [
    {{ gridIndex: 0, min: 0, max: 100, name: '分位 %', nameTextStyle: {{ color: '#1f4e79' }},
       axisLabel: {{ formatter: '{{value}}%', color: '#1f4e79' }}, splitLine: {{ lineStyle: {{ color: '#f0f1f4' }} }} }},
    {{ gridIndex: 0, position: 'right', scale: true, name: '全A', nameTextStyle: {{ color: '#0f766e' }},
       axisLabel: {{ color: '#0f766e' }}, splitLine: {{ show: false }} }},
    {{ gridIndex: 1, scale: true, name: '缺口', nameTextStyle: {{ color: '#7d3c98' }},
       axisLabel: {{ color: '#7d3c98' }}, splitLine: {{ lineStyle: {{ color: '#f0f1f4' }} }} }},
    {{ gridIndex: 1, position: 'right', scale: true, name: '全A', nameTextStyle: {{ color: '#0f766e' }},
       axisLabel: {{ color: '#0f766e' }}, splitLine: {{ show: false }} }},
    {{ gridIndex: 2, scale: true, name: 'Δ收窄', nameTextStyle: {{ color: '#c2410c' }},
       axisLabel: {{ color: '#c2410c' }}, splitLine: {{ lineStyle: {{ color: '#f0f1f4' }} }} }},
    {{ gridIndex: 2, position: 'right', scale: true, name: '全A', nameTextStyle: {{ color: '#0f766e' }},
       axisLabel: {{ color: '#0f766e' }}, splitLine: {{ show: false }} }}
  ],
  series: [
    {{ name: 'λ₁ 250 日分位', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: qv, showSymbol: false,
       lineStyle: {{ width: 2.4, color: '#1f4e79' }}, itemStyle: {{ color: '#1f4e79' }},
       markArea: {{ silent: true, itemStyle: {{ opacity: .30 }}, data: [
          [{{ yAxis: 90, itemStyle: {{ color: 'rgba(255,165,0,.35)' }} }}, {{ yAxis: 100 }}] ] }},
       markLine: {{ silent: true, symbol: 'none', label: {{ position: 'insideEndTop', fontSize: 10.5 }}, data: [
          {{ yAxis: 90, lineStyle: {{ color: '#e67e22', type: 'dashed' }},
             label: {{ formatter: '临界 90%（看空线）', color: '#e67e22' }} }} ] }} }},
    {{ name: '全A 对照（子图1）', type: 'line', xAxisIndex: 0, yAxisIndex: 1, data: iv, showSymbol: false,
       connectNulls: false, lineStyle: {{ width: 1.6, color: '#0f766e' }}, itemStyle: {{ color: '#0f766e' }} }},
    {{ name: '复杂度缺口', type: 'line', xAxisIndex: 1, yAxisIndex: 2, data: ev, showSymbol: false,
       lineStyle: {{ width: 2.4, color: '#7d3c98' }}, itemStyle: {{ color: '#7d3c98' }} }},
    {{ name: '缺口临界水平', type: 'line', xAxisIndex: 1, yAxisIndex: 2, data: ec, showSymbol: false,
       lineStyle: CL, itemStyle: {{ color: '#e67e22' }} }},
    {{ name: '全A 对照（子图2）', type: 'line', xAxisIndex: 1, yAxisIndex: 3, data: iv, showSymbol: false,
       connectNulls: false, lineStyle: {{ width: 1.6, color: '#0f766e' }}, itemStyle: {{ color: '#0f766e' }} }},
    {{ name: 'Δ收窄', type: 'line', xAxisIndex: 2, yAxisIndex: 4, data: dv, showSymbol: false,
       lineStyle: {{ width: 2.4, color: '#c2410c' }}, itemStyle: {{ color: '#c2410c' }} }},
    {{ name: 'Δ收窄临界水平', type: 'line', xAxisIndex: 2, yAxisIndex: 4, data: dc, showSymbol: false,
       lineStyle: CL, itemStyle: {{ color: '#e67e22' }} }},
    {{ name: '全A 对照（子图3）', type: 'line', xAxisIndex: 2, yAxisIndex: 5, data: iv, showSymbol: false,
       connectNulls: false, lineStyle: {{ width: 1.6, color: '#0f766e' }}, itemStyle: {{ color: '#0f766e' }} }}
  ]
}});
window.addEventListener('resize', function () {{ chart.resize(); }});
</script>
</body>
</html>
"""

suffix = sys.argv[1] if len(sys.argv) > 1 else ''
out = os.path.join(WORK, f'market_cgap_monitor_{t["date"].replace("-", "")}{suffix}.html')
with open(out, 'w', encoding='utf-8') as f:
    f.write(html)
print(f'HTML -> {out}  ({os.path.getsize(out)/1024:.0f} KB)')
print(f'预警 {tag}  原因 {reasons}')
print(f'退潮 {retreat}')
print(f'C 分位: 全历史 {Crank:.1f}% / 近250日 {Crank250:.1f}%; Gap 分位: 全历史 {Grank:.1f}% / 近250日 {Grank250:.1f}%')
if opp and opp.get('rows'):
    print(f'机会侧 -> 机会：{"、".join(opp["strong"]) if opp["strong"] else "今日无"}；'
          f'Top5：{" | ".join("%s %.2f(%s)" % (x["name"], x["score"], x["grade"]) for x in opp["top"])}')
    print(f'           相对弱势：{"、".join(opp["weak"]) if opp["weak"] else "无"}')
else:
    print('机会侧 -> 数据不足，未生成')