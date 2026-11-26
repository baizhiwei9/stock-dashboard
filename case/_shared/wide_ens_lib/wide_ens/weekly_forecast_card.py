#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""周日 · 下周预测周报（T+1 与 T+5 双周期，含各指数「哪个周期更准」）

大佬 2026-10-19 需求：
  「建立任务，每周日晚上执行，预测下一周的情况。有的那个模型预测下一天比较好，
    你也提示一下是 n 加一或者是 t 加一。」

核心设计：
  · 每个指数都跑两套周期：T+1（下一交易日）与 T+5（下一周 5 个交易日）
  · **每个指数标出「哪个周期历史更可靠」**，并按该周期的历史胜率给仓位
  · 观点/置信度进表格列；历史胜率/样本数全部下移到表格下方的段落

判定口径：
  · 历史校准表 → .cache/wide_ens/ms/<code>_lgb_tri_d<depth>_T<h>.json
  · OOS 从 2023-01-01 起；每档给 n / hit / p / raw(日均超额收益)
  · 「该周期是否可靠」用硬门槛：oos_n>=25 且 hit>=60% 且 p<=0.10 且 raw>0
  · 仓位规则沿用五指数卡片：<55% 不做；55-60% 三成；60-65% 五成；65-70% 七成；>=70% 满

用法：
  python3 bin/wide_ens/weekly_forecast_card.py            # 打印
  python3 bin/wide_ens/weekly_forecast_card.py --push     # 推飞书
"""
import os
import sys
import json
import argparse
import subprocess

try:
    from wsenv import WS                     # case/_shared/wide_ens_lib/wsenv.py
except ImportError:                              # 单独拷出去跑时退到文件所在仓根
    WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MS_DIR = os.path.join(WS, '.cache/wide_ens/ms')
WK_DIR = os.path.join(WS, '.cache/wide_ens/weekly')
CARD_FILE = os.path.join(WS, '.cache/wide_ens/weekly_forecast_card.json')

# (code, 名称, depth)  —— h=1 与 h=5 都会跑
IDX = [
    ('000688_SH', '科创50', 3),
    ('399006_SZ', '创业板', 3),
    ('000852_SH', '中证1000', 3),
    ('000300_SH', '沪深300', 5),
    ('000905_SH', '中证500', 5),
]
CAP = {'科创50': 0.50, '创业板': 0.80, '中证1000': 1.00, '沪深300': 0.70, '中证500': 0.40}
HS = (1, 5)

# 选档规则：OOS 样本够、胜率够、p 够、日均超额为正 → 取最低（最保守）的达标档
MIN_N = 25
MIN_HIT = 0.60
MAX_P = 0.10
MIN_TH = 0.20   # 门槛必须真能过滤（th0.00 = 永远在场，等于 beta，不算信号）


def jload(p):
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:
        return None


def conf_from_hit(hit):
    if not hit:
        return '—'
    if hit < 0.55:
        return '低（噪声）'
    if hit < 0.60:
        return '偏低'
    if hit < 0.65:
        return '中等'
    if hit < 0.70:
        return '较高'
    return '高'


def pos_from_hit(hit, cap):
    if not hit or hit < 0.55:
        return 0.0
    if hit < 0.60:
        return cap * 0.3
    if hit < 0.65:
        return cap * 0.5
    if hit < 0.70:
        return cap * 0.7
    return cap * 1.0


def pick_band(bands, anchor_thr=None):
    """在一条校准曲线里挑出「用来下注」的档位。

    优先按硬门槛（n>=25 且 hit>=60% 且 p<=0.10 且 raw>0）挑最低档；
    若全都不达标，退回「样本足够里胜率最高」的那档，但标记 reliable=False。
    """
    if not bands:
        return None
    parsed = []
    for k, b in bands.items():
        try:
            th = float(k.replace('th', ''))
        except Exception:
            continue
        o = b.get('oos') or {}
        if o.get('n', 0) < MIN_N:
            continue
        parsed.append(dict(th=th, key=k, n=o.get('n', 0), hit=o.get('hit') or 0,
                           p=o.get('p'), raw=o.get('raw') or 0,
                           all_n=(b.get('all') or {}).get('n'),
                           all_hit=(b.get('all') or {}).get('hit')))
    if not parsed:
        return None
    parsed.sort(key=lambda x: x['th'])
    ok = [x for x in parsed
          if x['th'] >= MIN_TH and x['hit'] >= MIN_HIT
          and (x['p'] is not None and x['p'] <= MAX_P) and x['raw'] > 0]
    if ok:
        r = dict(ok[0])
        r['reliable'] = True
        return r
    # 退回：th 达标里胜率最高档
    pool = [x for x in parsed if x['th'] >= MIN_TH] or parsed
    best = max(pool, key=lambda x: x['hit'])
    r = dict(best)
    r['reliable'] = False
    return r


def load_curve(code, depth, h):
    d = jload(os.path.join(MS_DIR, '%s_lgb_tri_d%d_T%d.json' % (code, depth, h)))
    if not d:
        return None
    return d


def load_today(code, h):
    d = jload(os.path.join(WK_DIR, 'T%d_%s.json' % (h, code)))
    return d


def build_rows():
    rows = []
    for code, name, depth in IDX:
        item = dict(code=code, name=name, depth=depth, cap=CAP[name], hs={})
        for h in HS:
            curve = load_curve(code, depth, h)
            today = load_today(code, h)
            band = pick_band((curve or {}).get('bands'))
            entry = dict(h=h, band=band, curve_ok=bool(curve), today=today, missing=None)
            if not curve:
                entry['missing'] = '缺校准表'
            elif not today:
                entry['missing'] = '缺当日预测'
            if band and today:
                score = today['score']
                thr = band['th']
                if score >= thr:
                    act, dirn = '看多', 1
                elif score <= -thr:
                    act, dirn = '看空', -1
                else:
                    act, dirn = '观望', 0
                entry.update(score=score, thr=thr, act=act, dirn=dirn,
                             hit=band['hit'], n=band['n'], p=band['p'], raw=band['raw'],
                             reliable=band['reliable'],
                             # 不可靠的周期不给仓位（宁可空仓）
                             pos=(pos_from_hit(band['hit'], item['cap'])
                                  if (dirn > 0 and band['reliable']) else 0.0),
                             date=today['date'], up=today['up'], down=today['down'])
            item['hs'][h] = entry
        rows.append(item)
    return rows


def best_period(item):
    """该指数哪个周期更可靠：优先可靠档里胜率高的；都不可靠则比胜率"""
    cand = [(h, e) for h, e in item['hs'].items() if e.get('hit')]
    if not cand:
        return None
    rel = [(h, e) for h, e in cand if e.get('reliable')]
    pool = rel or cand
    h, e = max(pool, key=lambda x: (x[1]['hit'], -x[0]))
    return h, e


# ---------------- 卡片渲染 ----------------
def _md(t):
    return {'tag': 'markdown', 'content': t}


def _hr():
    return {'tag': 'hr'}


def _row(cells, weights=None, head=False):
    n = len(cells)
    w = weights or [1] * n
    cols = []
    for i, c in enumerate(cells):
        txt = ('**%s**' % c) if head else str(c)
        cols.append({'tag': 'column', 'width': 'weighted', 'weight': w[i],
                     'elements': [{'tag': 'markdown', 'content': txt}]})
    return {'tag': 'column_set', 'flex_mode': 'none', 'background_style': 'default',
            'columns': cols}


def h_label(h):
    return {1: 'T+1（次日）', 5: 'T+5（一周）'}.get(h, 'T+%d' % h)


def build_card(rows):
    ok_items = [r for r in rows if any(e.get('act') for e in r['hs'].values())]
    date = next((e.get('date') for r in rows for e in r['hs'].values() if e.get('date')), '')

    # 组合仓位：各指数按「它最可靠的周期」给仓，取并集上限
    per_idx = []
    for r in rows:
        bp = best_period(r)
        if not bp:
            per_idx.append((r['name'], None, None, 0.0))
            continue
        h, e = bp
        per_idx.append((r['name'], h, e, e.get('pos', 0.0) if e.get('dirn', 0) > 0 else 0.0))
    combo_raw = sum(p for _, _, _, p in per_idx)
    combo = min(1.0, combo_raw)
    scale = (combo / combo_raw) if combo_raw > 1.0 else 1.0

    longs = [(n, h, e) for n, h, e, p in per_idx if e and e.get('dirn', 0) > 0 and p > 0]

    if len(longs) >= 2:
        flag, tpl = '🚨', 'red'
    elif len(longs) == 1:
        flag, tpl = '⚠️', 'orange'
    else:
        flag, tpl = '💤', 'blue'

    el = []
    el.append(_md('📎 来自钉钉OpenClaw'))

    # ① 结论
    el.append(_md('**① 下周怎么看（结论先行）**'))
    el.append(_row(['建议总仓位', '看多的指数', '用什么周期'], [2, 3, 3], head=True))
    el.append(_row(['**%.0f%%**' % (combo * 100),
                    ('、'.join(n for n, _, _ in longs) if longs else '无（全部观望）'),
                    ('、'.join('%s %s' % (n, h_label(h)) for n, h, _ in longs)
                     if longs else '—')], [2, 3, 3]))
    if scale < 1.0:
        el.append(_md('<font color=\'grey\'>各指数上限之和为 %.0f%%，已按比例缩到 100%%（'
                      '实际建议：%s）</font>'
                      % (combo_raw * 100,
                         '、'.join('%s %.0f%%' % (n, p * scale * 100)
                                  for n, _, _, p in per_idx if p > 0))))
    for r in rows:
        bp = best_period(r)
        if not bp:
            el.append(_md('<font color=\'grey\'>⚪ **%s**：数据缺失</font>' % r['name']))
            continue
        h, e = bp
        if e.get('dirn', 0) > 0:
            if e.get('reliable'):
                el.append(_md('<font color=\'green\'>🔴★ **%s：看多**</font>（按 %s），'
                              '置信度 %s，建议仓位 **%.0f%%**'
                              % (r['name'], h_label(h), conf_from_hit(e['hit']), e['pos'] * 100)))
            else:
                el.append(_md('<font color=\'green\'>🔴 **%s：偏多**</font>（按 %s），但该周期'
                              '**统计不达标**，只当参考、不给仓位'
                              % (r['name'], h_label(h))))
        elif e.get('dirn', 0) < 0:
            el.append(_md('<font color=\'red\'>🟢★ **%s：看空**</font>（按 %s，做空/清仓），'
                          '置信度 %s' % (r['name'], h_label(h), conf_from_hit(e['hit']))))
        else:
            el.append(_md('<font color=\'grey\'>⚪ **%s：观望**</font>（按 %s，'
                          'score %+.2f 未过阈 %.2f，不进场；已持仓则平掉）'
                          % (r['name'], h_label(h), e.get('score') or 0, e.get('thr') or 0)))

    el.append(_hr())

    # ② 双周期主表
    el.append(_md('**② 每个指数：T+1 与 T+5 分别是什么观点**'))
    el.append(_row(['指数', '周期', '观点', 'score/阈值', '置信度', '仓位', '该周期可靠？'],
                   [2, 2, 1, 2, 1, 1, 2], head=True))
    for r in rows:
        for h in HS:
            e = r['hs'][h]
            if e.get('missing') or not e.get('act'):
                el.append(_row([r['name'], h_label(h), '—', '—', e.get('missing', '—'), '—', '—']))
                continue
            cl = 'green' if e['act'] == '看多' else ('red' if e['act'] == '看空' else 'grey')
            if e['dirn'] == 0:
                cf = '<font color=\'grey\'>未触发</font>'
                pos_s = '<font color=\'grey\'>0%</font>'
            else:
                cf = '**%s**' % conf_from_hit(e['hit'])
                pos_s = '**%.0f%%**' % (e['pos'] * 100) if e['pos'] else '<font color=\'grey\'>0%</font>'
            rel = ('<font color=\'green\'>✔ 可用</font>' if e['reliable']
                   else '<font color=\'red\'>✘ 不可靠</font>')
            el.append(_row([r['name'], h_label(h),
                            '<font color=\'%s\'>**%s**</font>' % (cl, e['act']),
                            '%+.2f / 阈%.2f' % (e['score'], e['thr']),
                            cf, pos_s, rel]))
    el.append(_md('<font color=\'grey\'>**「该周期可靠？」怎么判**：这个指数在这个周期上，'
                  '历史样本外必须同时满足 样本数≥%d、胜率≥%.0f%%、p≤%.2f、日均超额>0。'
                  '全周期都不达标的指数（如中证1000 的 T+1）标 ✘，'
                  '**只当参考，不下注**。</font>' % (MIN_N, MIN_HIT * 100, MAX_P)))

    el.append(_hr())

    # ③ 哪个周期更准（这是大佬特别要的提示）
    el.append(_md('**③ 每个指数：哪个周期更准（用哪个做决策）**'))
    el.append(_row(['指数', '推荐周期', '为什么'], [2, 2, 5], head=True))
    for r in rows:
        bp = best_period(r)
        if not bp:
            continue
        h, e = bp
        other_h = 5 if h == 1 else 1
        oe = r['hs'].get(other_h) or {}
        parts = []
        parts.append('%s 胜率 %.1f%%（n=%d）' % (h_label(h), e['hit'] * 100, e['n']))
        if oe.get('hit'):
            parts.append('%s 只有 %.1f%%（n=%d）'
                         % (h_label(other_h), oe['hit'] * 100, oe.get('n') or 0))
        if not e['reliable']:
            parts.append('⚠️ 两周期都不达标，只能看方向不能下注')
        el.append(_row([r['name'], '**%s**' % h_label(h), '；'.join(parts)], [2, 2, 5]))

    el.append(_hr())

    # ④ 历史胜率（下移段落）
    el.append(_md('**④ 历史胜率（上面置信度的依据，样本外 2023 起）**'))
    el.append(_row(['指数', '周期', '选用档位', '同档胜率', '样本 n', 'p 值', '日均超额'],
                   [2, 2, 2, 2, 1, 1, 2], head=True))
    for r in rows:
        for h in HS:
            e = r['hs'][h]
            if e.get('missing') or not e.get('act'):
                continue
            p_s = ('%.3f' % e['p']) if e.get('p') is not None else '—'
            el.append(_row([r['name'], h_label(h), 'th%.2f' % e['thr'],
                            '**%.1f%%**' % (e['hit'] * 100), str(e['n']),
                            p_s, '%+.3f%%' % e['raw']]))
    el.append(_md('<font color=\'grey\'>「选用档位」= 该周期里满足硬门槛的最低（最保守）分位档；'
                  '「日均超额」= 该档位按信号方向持有 1 个周期的日均收益，已扣 0.2% 交易成本前的毛值。'
                  '基础上涨率见各指数校准文件。</font>'))

    el.append(_hr())

    # ⑤ 下周看什么
    el.append(_md('**⑤ 下周盯什么（SHAP 归因，各取前 3）**'))
    for r in rows:
        bp = best_period(r)
        if not bp or not bp[1].get('today'):
            continue
        h, e = bp
        t = e['today']
        el.append(_md('**%s**（%s，score %+.2f）' % (r['name'], h_label(h), e['score'])))
        el.append(_md('　⬆ 支撑：%s' % '、'.join((t.get('up') or '').split('、')[:3])))
        el.append(_md('　⬇ 压制：%s' % '、'.join((t.get('down') or '').split('、')[:3])))

    el.append(_hr())

    # ⑥ 今年预测准确度（台账基准表：每周预测入表，到期结算，滚动统计本年）
    try:
        import weekly_ledger as L
        for tag, payload in L.build_md():
            if tag == 'md':
                el.append(_md(payload))
            elif tag == 'row':
                cells, weights = payload
                el.append(_row(cells[0], weights, head=True))
                for c in cells[1:]:
                    el.append(_row(c, weights))
    except Exception as e:
        el.append(_md('<font color=\'grey\'>⑥ 今年准确度台账读取失败：%s</font>' % e))

    el.append(_hr())
    el.append(_md('<font color=\'grey\'>口径：LightGBM 三分类（跌/平/涨），滚动 750 日训练、每 20 日重训；'
                  'score = P(涨) − P(跌)，是模型内部分数，**不是胜率**。'
                  'T+1 = 下一交易日收盘，T+5 = 下一周 5 个交易日后收盘。'
                  '以上为量化信号，不构成投资建议。</font>'))

    card = {
        'config': {'wide_screen_mode': True},
        'header': {'template': tpl,
                   'title': {'tag': 'plain_text',
                             'content': '%s 下周预测 · T+1 与 T+5 · %s' % (flag, date)}},
        'elements': el,
    }
    return card, dict(date=date, combo=combo, longs=[n for n, _, _ in longs],
                      per_idx=per_idx)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--push', action='store_true')
    ap.add_argument('--chat', default=None)
    a = ap.parse_args()

    rows = build_rows()
    card, meta = build_card(rows)
    body = json.dumps({'msg_type': 'interactive', 'card': card}, ensure_ascii=False)
    os.makedirs(os.path.dirname(CARD_FILE), exist_ok=True)
    json.dump(card, open(CARD_FILE, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

    print('[card] elements=%d bytes=%d date=%s combo=%.0f%% longs=%s'
          % (len(card['elements']), len(body.encode()), meta['date'],
             meta['combo'] * 100, ','.join(meta['longs']) or '-'))
    for name, h, e, pos in meta['per_idx']:
        if not e:
            print('  %-8s 数据缺失' % name)
            continue
        print('  %-8s 用 %-10s %-4s score=%+.2f 胜率=%.1f%%(n=%d, p=%s, %s) 仓位=%.0f%%'
              % (name, h_label(h), e.get('act'), e.get('score') or 0, e['hit'] * 100, e['n'],
                 e.get('p'), '可用' if e['reliable'] else '不可靠', pos * 100))

    if a.push:
        cmd = ['python3', os.path.join(WS, 'bin/feishu_push_card.py'), '--card-file', CARD_FILE]
        if a.chat:
            cmd += ['--chat', a.chat]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        print('[push]', (r.stdout or r.stderr).strip()[:300])
        sys.exit(0 if r.returncode == 0 else 1)


if __name__ == '__main__':
    main()
