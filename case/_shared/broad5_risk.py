# -*- coding: utf-8 -*-
"""五大宽基风险监控（共享模块，2026-10-22 为日更报告统一补齐口径而建）

覆盖：沪深300 / 中证500 / 中证1000 / 科创50 / 创业板指
用途：给缺少宽基风险结论的日频报告，统一补上「明确看空/偏空/中性/看多」结论。

判定口径（与 RMT 宽基块一致，全部走均线与动量，不掺主观）：
  - 收盘 < MA20 且 收盘 < MA60  → 看空（空头排列）
  - 收盘 < MA60                 → 偏空
  - 收盘 > MA20 且 收盘 > MA60   → 看多
  - 其余                        → 中性
风险等级（按 60 日回撤）：<=-20% 高 / <=-10% 中 / 否则 低

数据源：腾讯行情接口（不依赖 akshare / tushare，避开 IP 限流）
"""
import datetime
import json
import os
import sys

import requests

BROAD5 = [
    ('sh000300', '沪深300'),
    ('sh000905', '中证500'),
    ('sh000852', '中证1000'),
    ('sh000688', '科创50'),
    ('sz399006', '创业板指'),
]

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.cache')


def _fetch_kline(code, days=400):
    """腾讯日线（前复权），返回 [(date, open, close, high, low), ...]"""
    url = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get'
    end = datetime.datetime.now().strftime('%Y-%m-%d')
    start = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime('%Y-%m-%d')
    r = requests.get(url, params={'param': '%s,day,%s,%s,800,qfq' % (code, start, end)}, timeout=20)
    js = r.json()
    data = (js.get('data') or {}).get(code) or {}
    k = data.get('qfqday') or data.get('day') or []
    out = []
    for row in k:
        try:
            out.append((str(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[4])))
        except Exception:
            continue
    return out


def _stats(rows):
    closes = [x[2] for x in rows]
    n = len(closes)
    if n < 61:
        return None
    close = closes[-1]
    ma20 = sum(closes[-20:]) / 20.0
    ma60 = sum(closes[-60:]) / 60.0
    mom_5 = close / closes[-6] - 1 if n >= 6 else None
    mom_20 = close / closes[-21] - 1 if n >= 21 else None
    mom_60 = close / closes[-61] - 1 if n >= 61 else None
    win = closes[-60:]
    high60, low60 = max(win), min(win)
    dd60 = close / high60 - 1
    rng = (close - low60) / (high60 - low60) if high60 > low60 else 0.0
    if close < ma20 and close < ma60:
        verdict = '看空'
    elif close < ma60:
        verdict = '偏空'
    elif close > ma20 and close > ma60:
        verdict = '看多'
    else:
        verdict = '中性'
    risk = '高' if dd60 <= -0.20 else ('中' if dd60 <= -0.10 else '低')
    return {
        'date': rows[-1][0], 'close': close, 'chg_1': rows[-1][2] / rows[-2][2] - 1,
        'ma20': ma20, 'ma60': ma60,
        'ma_state': '空头排列' if close < ma20 < ma60 else ('多头排列' if close > ma20 > ma60 else '纠缠'),
        'mom_5': mom_5, 'mom_20': mom_20, 'mom_60': mom_60,
        'drawdown_60': dd60, 'range_pos_60': rng,
        'verdict': verdict, 'risk': risk,
    }


def compute(days=400):
    """返回 {'sh000300': {...}, ...}；失败项 value 为 dict(missing=True)"""
    out = {}
    for code, name in BROAD5:
        try:
            rows = _fetch_kline(code, days=days)
            st = _stats(rows) if rows else None
            if not st:
                raise RuntimeError('数据不足/为空')
            st['name'] = name
            st['code'] = code
            out[code] = st
        except Exception as e:
            out[code] = {'name': name, 'code': code, 'missing': True, 'reason': str(e)[:80]}
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(os.path.join(CACHE_DIR, 'broad5_latest.json'), 'w', encoding='utf-8') as f:
            json.dump({'ts': datetime.datetime.now().isoformat(), 'data': out}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return out


def _pct(x):
    return 'n/a' if x is None else '%+.2f%%' % (x * 100)


# 方向标记（看多=🟢 绿色圆点，看空=🔴 红色圆点，中性=➖），供需要醒目提示指数/行业结论的报告统一复用
DIR_MARK_BULL = '🟢'
DIR_MARK_BEAR = '🔴'
DIR_MARK_FLAT = '➖'


def verdict_marker(verdict):
    """宽基结论 → 方向标记：看多=🟢，看空/偏空=🔴，中性=➖"""
    if verdict == '看多':
        return DIR_MARK_BULL
    if verdict in ('看空', '偏空'):
        return DIR_MARK_BEAR
    return DIR_MARK_FLAT


def detail_lines(data=None, markers=False):
    """每指数一行的明细（纯文本，飞书 text / lark_md 通用）

    markers=True 时在每行行首加方向标记（🟢/🔴/➖），默认 False 保持既有报告口径不变。
    """
    data = data or compute()
    lines = []
    for code, name in BROAD5:
        v = data.get(code) or {}
        if v.get('missing'):
            lines.append('%s：数据缺失（%s）' % (name, v.get('reason', '')))
            continue
        prefix = (verdict_marker(v['verdict']) + ' ') if markers else ''
        lines.append(prefix + '%s %s（当日%s）｜20日%s｜60日%s｜距60日高点%s｜MA20 %.1f/MA60 %.1f（%s）→ %s（风险：%s）'
                     % (name, '%.2f' % v['close'], _pct(v['chg_1']), _pct(v['mom_20']), _pct(v['mom_60']),
                        _pct(v['drawdown_60']), v['ma20'], v['ma60'], v['ma_state'], v['verdict'], v['risk']))
    return lines


def counts(data=None):
    data = data or compute()
    c = {'看空': 0, '偏空': 0, '中性': 0, '看多': 0, '缺失': 0}
    for code, _n in BROAD5:
        v = data.get(code) or {}
        if v.get('missing'):
            c['缺失'] += 1
        else:
            c[v['verdict']] = c.get(v['verdict'], 0) + 1
    return c


def summary_text(data=None, title='五大宽基风险监控', markers=False):
    """整块文本：标题 + 计数 + 明细；markers=True 时每条明细行首加方向标记（🟢/🔴/➖）"""
    data = data or compute()
    c = counts(data)
    lines = ['【%s】%s' % (title, ' '.join('%s %d' % (k, v) for k, v in c.items() if v)),
             '  ' + '看空 = 收盘低于 MA20 且低于 MA60；偏空 = 收盘低于 MA60；风险等级按 60 日回撤分档']
    for ln in detail_lines(data, markers=markers):
        lines.append('  ' + ln)
    return '\n'.join(lines)


if __name__ == '__main__':
    print(summary_text())