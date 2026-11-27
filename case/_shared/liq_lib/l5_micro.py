#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L5 股市微观池（含两融杠杆子层）—— 单一数据源，供周报复用

大佬 2026-10-19 定稿的 L5 规格（7 指标 + 3 条叠加规则）：

| 指标                    | 监控对象    | 一年分位阈值                                  | 标星 |
|-------------------------|-------------|-----------------------------------------------|------|
| 全A周成交额             | 总量水      | ≤20% → ⭐地量                                 | ⭐   |
| 两融余额                | 杠杆存量    | ≥80% → ⭐饱和；≤20% → ⭐冰点                  | ⭐   |
| 两融周净买入(融资净买)  | 杠杆边际    | ≤20% 分位且为负 → ⭐⭐撤退                    | ⭐⭐ |
| 两融成交占全A比         | 杠杆话语权  | ≥80% → ⭐过热；≤20% → ⭐退潮                  | ⭐   |
| 股票ETF周净申赎         | 机构托底/抽水 | ≥80%净申购 → ⭐托底；≤20% → ⭐⭐抽水         | ⭐/⭐⭐ |
| 北向/陆股通净流入       | 外资        | ≤20% → ⭐撤                                   | ⭐   |
| 重要股东净减持          | 供给抽血    | ≥80% → ⭐抽血大                               | ⭐   |

叠加规则：
  R1 两融净买入连续2周负 + 两融余额分位≥80%      → 🔴 杠杆见顶回落，成长/小票脆弱
  R2 成交额≤20%分位 + 两融余额≤20%分位           → 🔴 微观池全面干涸
  R3 两融净买入正但ETF净赎回                     → 🟡 散户杠杆顶上、机构撤退（博弈市）

数据：data/market.db（由 bin/l5_fetch.py 采集）
  cn.eq.amt_total / margin_balance / margin_buy / margin_repay / margin_net
  cn.eq.margin_amt_share / etf_net_flow / holder_net_reduce
  cn.eq.north_flow（bin/l3_fetch.py）
  cn.repo.vol_on_share（bin/l3_fetch.py）
"""
from __future__ import annotations

import os
import sys

_BIN = os.path.dirname(os.path.abspath(__file__))
if _BIN not in sys.path:
    sys.path.insert(0, _BIN)

try:
    import marketdb as M
except Exception:                                    # pragma: no cover
    M = None

from ascii_chart import chart, spark          # noqa: E402

WEEK = 5
Q_WINDOW = 52          # 周频一年
D_WINDOW = 250         # 日频一年


# ------------------------------------------------------------------ 工具
def _rows(sid, days=520):
    """升序 [(date, value)]，去掉 None"""
    if M is None:
        return []
    try:
        r = M.get_series(sid, days=days, ascending=True)
    except Exception:
        return []
    return [(str(d), float(v)) for d, v in r if v is not None]


def _wsum(rows, n=WEEK):
    """**不重叠**的周块合计（新→旧）。

    以最新一天为锚向前切 5 日一块 → 「本周 / 上周 / 上上周」语义严格，
    且可直接用连续负周数判断叠加规则 R1。
    """
    vals = [v for _, v in rows]
    blocks = []
    end = len(vals)
    while end - n >= 0:
        blocks.append(sum(vals[end - n:end]))
        end -= n
    if end > 0:                      # 头部不足 5 日的残块丢弃（不完整周）
        pass
    return blocks


def _wmean(rows, n=WEEK):
    vals = [v for _, v in rows]
    blocks = []
    end = len(vals)
    while end - n >= 0:
        blocks.append(sum(vals[end - n:end]) / n)
        end -= n
    return blocks


def pct(cur, series, window):
    """分位（0~100），series 新→旧；样本不足返回 (None, n)"""
    s = [v for v in series if isinstance(v, (int, float))][:window]
    n = len(s)
    if cur is None or n < 12:
        return None, n
    below = sum(1 for v in s if v < cur)
    eq = sum(1 for v in s if v == cur)
    return round((below + 0.5 * eq) / n * 100, 1), n


def _q(cur, series, window):
    q, n = pct(cur, series, window)
    return q


def _star_lo(q, lo=20):
    return '⭐' if (q is not None and q <= lo) else ''


def _star_hi(q, hi=80):
    return '⭐' if (q is not None and q >= hi) else ''


def _f(v, fmt='.1f', suffix=''):
    return '—' if v is None else format(v, fmt) + suffix


def _recent(rows, n=10):
    """取最后 n 个点，升序（旧→新）"""
    return rows[-n:] if len(rows) >= n else rows


def clean_ratio(rows, floor_ratio=0.65, ceil_ratio=None):
    """剔除比例型序列的明显数据洞（如隔夜占比突然整到 30%）。

    规则：以全样本中位数为基准，低于 floor_ratio×median（或高于 ceil_ratio×median）
    视为采集异常，剔除并在调用方标注。
    返回 (clean_rows, dropped_rows)
    """
    vals = sorted([v for _, v in rows if v is not None])
    if len(vals) < 10:
        return rows, []
    med = vals[len(vals) // 2]
    if not med:
        return rows, []
    keep, drop = [], []
    for d, v in rows:
        if v is None:
            continue
        if v < med * floor_ratio or (ceil_ratio and v > med * ceil_ratio):
            drop.append((d, v))
        else:
            keep.append((d, v))
    return keep, drop


def onshare_rows(n=10):
    """隔夜回购占比（已剔数据洞）升序，供第一层风险图用"""
    rows = _rows('cn.repo.vol_on_share', days=520)
    clean, _ = clean_ratio(rows)
    return _recent(clean, n)


# ------------------------------------------------------------------ 主计算
def compute_l5():
    """返回 {items, stars, layer_star, overlay, charts, as_of, meta}"""
    items, charts, meta = [], {}, {}

    amt = _rows('cn.eq.amt_total')
    bal = _rows('cn.eq.margin_balance')
    net = _rows('cn.eq.margin_net')
    share = _rows('cn.eq.margin_amt_share')
    etf = _rows('cn.eq.etf_net_flow', days=900)
    north = _rows('cn.eq.north_flow')
    hold = _rows('cn.eq.holder_net_reduce')
    onsh = _rows('cn.repo.vol_on_share')

    def add(name, value, wow, quantile, thresh, star, track, note='', extra=None):
        it = {'name': name, 'value': value, 'wow': wow, 'quantile': quantile,
              'threshold': thresh, 'star': star, 'track': track, 'note': note}
        if extra:
            it.update(extra)
        items.append(it)
        return it

    # ① 全A周成交额（总量水）
    amt_q = amt_last = None
    if amt:
        wk = _wsum(amt)
        amt_last = wk[0]
        amt_q = _q(wk[0], wk, Q_WINDOW)
        add('全A周成交额', f'{wk[0] / 10000:.2f}万亿',
            f'{(wk[0] - wk[1]) / 10000:+.2f}万亿' if len(wk) > 1 else '—',
            _f(amt_q, '.1f', '%'), '≤20% → ⭐地量', _star_lo(amt_q),
            '总量水', f'近5交易日合计（源 cn.eq.amt_total）',
            {'_q': amt_q})
        charts['amt'] = [(d, v) for d, v in _recent(amt, 10)]
    else:
        add('全A周成交额', '本周期暂缺', '—', '数据不足，未计算分位',
            '≤20% → ⭐地量', '', '总量水', '跑 bin/l5_fetch.py 补数')

    # ② 两融余额（杠杆存量，双侧）
    bal_q = None
    if bal:
        _bal_desc = [v for _, v in bal][::-1]        # 新→旧（pct 取前 window 个）
        bal_q = _q(_bal_desc[0], _bal_desc, D_WINDOW)
        star = ''
        if bal_q is not None and bal_q >= 80:
            star = '⭐'
        elif bal_q is not None and bal_q <= 20:
            star = '⭐'
        add('两融余额', f'{bal[-1][1] / 10000:.2f}万亿',
            f'{bal[-1][1] - bal[-6][1]:+.0f}亿' if len(bal) > 6 else '—',
            _f(bal_q, '.1f', '%'), '≥80% → ⭐饱和；≤20% → ⭐冰点', star,
            '杠杆存量', f'截至 {bal[-1][0]}',
            {'_q': bal_q})
        charts['balance'] = _recent(bal, 10)
    else:
        add('两融余额', '本周期暂缺', '—', '数据不足，未计算分位',
            '≥80% → ⭐饱和；≤20% → ⭐冰点', '', '杠杆存量', '跑 bin/l5_fetch.py 补数')

    # ③ 两融周净买入（杠杆边际）
    net_q = net_last = None
    neg_weeks = 0
    if net:
        wk = _wsum(net)
        net_last = wk[0]
        net_q = _q(wk[0], wk, Q_WINDOW)
        for v in wk:
            if v < 0:
                neg_weeks += 1
            else:
                break
        star = '⭐⭐' if (net_q is not None and net_q <= 20 and net_last < 0) else ''
        add('两融周净买入(融资净买)', f'{net_last:+.0f}亿',
            f'{net_last - wk[1]:+.0f}亿' if len(wk) > 1 else '—',
            _f(net_q, '.1f', '%'), '≤20%分位且为负 → ⭐⭐撤退', star,
            '杠杆边际', f'近5交易日 买入−偿还；已连续 {neg_weeks} 周为负',
            {'_q': net_q})
        charts['net'] = _recent(net, 10)
    else:
        add('两融周净买入(融资净买)', '本周期暂缺', '—', '数据不足，未计算分位',
            '≤20%分位且为负 → ⭐⭐撤退', '', '杠杆边际', '跑 bin/l5_fetch.py 补数')

    # ④ 两融成交占全A比（杠杆话语权）
    share_q = None
    if share:
        wk = _wmean(share)
        share_q = _q(wk[0], wk, Q_WINDOW)
        star = ''
        if share_q is not None and share_q >= 80:
            star = '⭐'
        elif share_q is not None and share_q <= 20:
            star = '⭐'
        add('两融成交占全A比', _f(wk[0], '.2f', '%'),
            f'{wk[0] - wk[1]:+.2f}pct' if len(wk) > 1 else '—',
            _f(share_q, '.1f', '%'), '≥80% → ⭐过热；≤20% → ⭐退潮', star,
            '杠杆话语权', '融资买入额 / 全A成交额（周均值）',
            {'_q': share_q})
        charts['share'] = _recent(share, 10)
    else:
        add('两融成交占全A比', '本周期暂缺', '—', '数据不足，未计算分位',
            '≥80% → ⭐过热；≤20% → ⭐退潮', '', '杠杆话语权', '跑 bin/l5_fetch.py 补数')

    # ⑤ 股票ETF周净申赎（机构托底/抽水）—— 周频，最新在末尾
    etf_q = etf_last = None
    if etf:
        vals = [v for _, v in etf][::-1]        # 反转为新→旧，供分位取最近52周
        etf_last = vals[0]
        etf_q = _q(etf_last, vals, Q_WINDOW)
        star = ''
        if etf_q is not None and etf_q >= 80 and etf_last > 0:
            star = '⭐'
        elif etf_q is not None and etf_q <= 20 and etf_last < 0:
            star = '⭐⭐'
        add('股票ETF周净申赎', f'{etf_last:+.0f}亿',
            f'{etf_last - vals[1]:+.0f}亿' if len(vals) > 1 else '—',
            _f(etf_q, '.1f', '%'), '≤20%净赎回 → ⭐⭐抽水；≥80%净申购 → ⭐托底', star,
            '机构托底/抽水', f'Δ份额×净值；截至 {etf[-1][0]}',
            {'_q': etf_q})
        charts['etf'] = _recent(etf, 10)
    else:
        add('股票ETF周净申赎', '本周期暂缺', '—', '数据不足，未计算分位',
            '≥80%净申购 → ⭐托底；≤20% → ⭐⭐抽水', '', '机构托底/抽水',
            '跑 bin/l5_fetch.py（--etf-top 40）补数')

    # ⑥ 北向/陆股通净流入（外资）
    north_q = None
    if north:
        wk = _wsum(north)
        north_q = _q(wk[0], wk, Q_WINDOW)
        add('北向/陆股通净流入', f'{wk[0]:+.0f}亿',
            f'{wk[0] - wk[1]:+.0f}亿' if len(wk) > 1 else '—',
            _f(north_q, '.1f', '%'), '≤20% → ⭐撤', _star_lo(north_q),
            '外资', '近5交易日合计（cn.eq.north_flow）',
            {'_q': north_q})
        charts['north'] = _recent(north, 10)
    else:
        add('北向/陆股通净流入', '本周期暂缺', '—', '数据不足，未计算分位',
            '≤20% → ⭐撤', '', '外资', '跑 bin/l3_fetch.py 补数')

    # ⑦ 重要股东净减持（供给抽血）——序列取「净减持额」= −净增减持
    hold_q = None
    if hold:
        wk = [-x for x in _wsum(hold)]          # 正数 = 净减持（抽血）
        hold_q = _q(wk[0], wk, Q_WINDOW)
        add('重要股东净减持(周)', f'{wk[0]:+.2f}亿',
            f'{wk[0] - wk[1]:+.2f}亿' if len(wk) > 1 else '—',
            _f(hold_q, '.1f', '%'), '≥80% → ⭐抽血大', _star_hi(hold_q),
            '供给抽血', '减持−增持（正=净减持）；按公告日聚合',
            {'_q': hold_q})
        charts['hold'] = [(d, -v) for d, v in _recent(hold, 10)]
    else:
        add('重要股东净减持(周)', '本周期暂缺', '—', '数据不足，未计算分位',
            '≥80% → ⭐抽血大', '', '供给抽血', '跑 bin/l5_fetch.py 补数')

    # ---------------- 叠加规则 ----------------
    overlay = []
    if net_last is not None and bal_q is not None and neg_weeks >= 2 and bal_q >= 80:
        overlay.append('🔴 **杠杆见顶回落**：两融净买入连续 2 周为负 + 两融余额'
                       f'{bal_q:.0f}% 分位 → 成长/小票脆弱')
    if amt_q is not None and bal_q is not None and amt_q <= 20 and bal_q <= 20:
        overlay.append('🔴 **微观池全面干涸**：成交额'
                       f'{amt_q:.0f}% 分位 + 两融余额 {bal_q:.0f}% 分位')
    if net_last is not None and net_last > 0 and etf_last is not None and etf_last < 0:
        overlay.append('🟡 **典型博弈市**：两融净买入为正、ETF 净赎回 → '
                       '散户杠杆顶上、机构撤退')
    # 补充观察（不在原三条规则内，但方向相反那面也要看）
    if net_last is not None and net_last < 0 and etf_last is not None and etf_last > 0:
        overlay.append('⚪ **补充观察（非原规则）**：两融净买入为负、ETF 净申购 → '
                       '**杠杆在退、机构在接**；属于结构性换手，'
                       '不是全面失血，但意味着弹性会下降（机构接盘偏好宽基，不接题材）')
    if not overlay:
        miss = [k for k, v in (('净买入', net_last), ('余额分位', bal_q),
                               ('成交额分位', amt_q), ('ETF', etf_last)) if v is None]
        overlay.append('⚪ 三条叠加规则均未触发' +
                       (f'（{"/".join(miss)} 数据不足无法判定）' if miss else ''))

    stars = sum(1 for it in items if it['star'])
    n2 = sum(1 for it in items if it['star'] == '⭐⭐')
    layer_star = '⭐⭐' if (n2 or stars >= 2) else ('⭐' if stars == 1 else '')

    # ---------------- 隔夜占比（第一层风险，附 10 日图） ----------------
    onsh_q = None
    if onsh:
        _on_clean, _on_drop = clean_ratio(onsh)
        _desc = [v for _, v in _on_clean][::-1]
        if _desc:
            onsh_q = _q(_desc[0], _desc, D_WINDOW)
        charts['onshare'] = _recent(_on_clean, 10)
        if _on_drop:
            meta['onshare_dropped'] = len(_on_drop)

    as_of = max([r[-1][0] for r in (amt, bal, net) if r] or [''])

    return {
        'items': items,
        'stars': stars,
        'layer_star': layer_star,
        'overlay': overlay,
        'charts': charts,
        'as_of': as_of,
        'meta': {'amt_q': amt_q, 'bal_q': bal_q, 'net_q': net_q,
                 'share_q': share_q, 'etf_q': etf_q, 'north_q': north_q,
                 'hold_q': hold_q, 'onshare_q': onsh_q,
                 'net_last': net_last, 'etf_last': etf_last,
                 'neg_weeks': neg_weeks},
    }


# ------------------------------------------------------------------ 渲染
def render_md_table(L5) -> str:
    L = ['| 指标 | 监控对象 | 本周值 | 周环比 | 一年分位 | 阈值触法 | 标记 |',
         '|---|---|---|---|---|---|---|']
    for it in L5['items']:
        L.append(f"| {it['name']} | {it['track']} | {it['value']} | {it['wow']} | "
                 f"{it['quantile']} | {it['threshold']} | {it['star']} |")
    return '\n'.join(L)


def render_card_charts(L5) -> str:
    """卡片版：紧凑「一屏看全 7 项」折线 + 两融关键两张详图"""
    C = L5['charts']
    m = L5['meta']
    E = []
    try:
        from ascii_chart import chart, multi
    except Exception:
        return ''

    # 一屏全览：7 项各自的近10日折线（块字符）
    items = []
    if C.get('amt'):
        items.append(('成交额(日)', C['amt'], '亿', '.0f'))
    if C.get('balance'):
        items.append(('两融余额', C['balance'], '亿', '.0f'))
    if C.get('net'):
        items.append(('两融净买入', C['net'], '亿', '+.0f'))
    if C.get('share'):
        items.append(('两融占成交', C['share'], '%', '.2f'))
    if C.get('etf'):
        items.append(('ETF净申赎', C['etf'], '亿', '+.0f'))
    if C.get('north'):
        items.append(('北向(日)', C['north'], '亿', '+.0f'))
    if C.get('hold'):
        items.append(('股东净减持(日)', C['hold'], '亿', '+.2f'))
    if items:
        E.append(multi(items, head="**📈 近10个交易日一屏看（🔹折线左旧右新）**"))

    # 两张详图（带竖条）：两融余额 + 两融净买入
    if C.get('balance'):
        E.append(chart('两融余额（杠杆存量）', C['balance'], unit='亿', fmt='.0f',
                       width=12, extra=f"一年分位 {_f(m.get('bal_q'), '.1f', '%')}" +
                       ('  ⭐饱和' if (m.get('bal_q') or 0) >= 80 else
                        ('  ⭐冰点' if (m.get('bal_q') or 100) <= 20 else ''))))
    if C.get('net'):
        E.append(chart('两融日净买入（杠杆边际）', C['net'], unit='亿', fmt='+.0f',
                       width=12, zero_base=True,
                       extra=f"周合计 {_f(m.get('net_last'), '+.0f', '亿')}"
                             f"｜一年分位 {_f(m.get('net_q'), '.1f', '%')}"
                             f"｜连续为负 {m.get('neg_weeks', 0)} 周"))
    return '\n'.join(E)


def render_md_charts(L5, onshare_only=False) -> str:
    """ASCII 10 日图（Markdown 代码块外的纯文本，飞书/邮件都能显示）"""
    C = L5['charts']
    L = []
    if onshare_only:
        if C.get('onshare'):
            L.append('**🌙 第一层风险 · 隔夜回购占比（近 10 个交易日）**')
            L.append('')
            L.append(chart('隔夜回购占比', C['onshare'], unit='%', fmt='.1f',
                           extra=f"一年分位 {_f(L5['meta']['onshare_q'], '.1f', '%')}"
                                 f"（≥95% = 极致 carry，扰动即砸长端）"))
        return '\n'.join(L)

    if C.get('balance'):
        L.append(chart('两融余额（杠杆存量）', C['balance'], unit='亿', fmt='.0f',
                       extra=f"一年分位 {_f(L5['meta']['bal_q'], '.1f', '%')}"))
        L.append('')
    if C.get('net'):
        L.append(chart('两融日净买入（杠杆边际）', C['net'], unit='亿', fmt='+.0f',
                       zero_base=True,
                       extra=f"周合计 {_f(L5['meta']['net_last'], '+.0f', '亿')}"
                             f"｜一年分位 {_f(L5['meta']['net_q'], '.1f', '%')}"))
        L.append('')
    if C.get('amt'):
        L.append(chart('全A日成交额（总量水）', C['amt'], unit='亿', fmt='.0f',
                       extra=f"周合计分位 {_f(L5['meta']['amt_q'], '.1f', '%')}"))
        L.append('')
    if C.get('share'):
        L.append(chart('两融买入占全A比（杠杆话语权）', C['share'], unit='%', fmt='.2f',
                       extra=f"一年分位 {_f(L5['meta']['share_q'], '.1f', '%')}"))
        L.append('')
    if C.get('etf'):
        L.append(chart('股票ETF周净申赎（周频）', C['etf'], unit='亿', fmt='+.0f',
                       zero_base=True, extra='正=净申购（机构托底）'))
    return '\n'.join([x for x in L if x != '']).strip()


def render_md_overlay(L5) -> str:
    return '\n'.join('- ' + x for x in L5['overlay'])


def render_card_elements(L5, with_charts=True):
    """飞书卡片元素（markdown 元素列表，控制在体积内）"""
    E = []

    def md(t):
        E.append({'tag': 'markdown', 'content': t})

    # 表格用 column_set 太占体积，这里用紧凑文本行
    head = ('**指标｜监控对象｜本周｜周环比｜一年分位｜标记**')
    md(head)
    for it in L5['items']:
        md(f"- {it['star'] + ' ' if it['star'] else ''}**{it['name']}**（{it['track']}）"
           f"　{it['value']}　`{it['wow']}`　`{it['quantile']}`")
    md('**🚦 叠加规则**')
    md('\n'.join('- ' + x for x in L5['overlay']))
    if with_charts:
        t = render_md_charts(L5)
        if t:
            md(t)
    return E


if __name__ == '__main__':
    d = compute_l5()
    print(f"== L5 层标 {d['layer_star']}（{d['stars']} 个 ⭐）—— 数据 {d['as_of']} ==")
    print(render_md_table(d))
    print()
    print(render_md_overlay(d))
    print()
    print(render_md_charts(d))
    print()
    print('--- 隔夜占比 ---')
    print(render_md_charts(d, onshare_only=True))
