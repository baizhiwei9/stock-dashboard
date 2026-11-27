#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一选模周报卡片（T+1 / T+5 × 普适 / 高精度，四张表）

读 .cache/wide_ens/unified_selected.json（unified_select.py 产出）
  + .cache/wide_ens/unified_signal.json（unified_signal.py 产出，若存在则带上本期信号）

卡片结构（观点进列、历史下移，沿用 2026-10-19 定的原则）
  ① 一句话结论：哪些槽位达标、能上多少仓
  ② T+1 普适表 / T+1 高精度表
  ③ T+5 普适表 / T+5 高精度表
  ④ 口径与坑（下移，灰色小字）

体积控制：飞书 interactive 卡片上限约 50KB，超了报 11246。
本卡片四张表 20 行，控制在 ~35KB 以内；完整明细（分组、p 值、top3）走文字消息。

用法：
  python3 bin/wide_ens/unified_card.py           # 打印
  python3 bin/wide_ens/unified_card.py --push    # 推飞书
  python3 bin/wide_ens/unified_card.py --push --chat oc_xxx
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
SEL = os.path.join(WS, '.cache/wide_ens/unified_selected.json')
SIG = os.path.join(WS, '.cache/wide_ens/unified_signal.json')
CARD_FILE = os.path.join(WS, '.cache/wide_ens/unified_card.json')

ORDER = ['000300_SH', '000905_SH', '000852_SH', '000688_SH', '399006_SZ']
NM = {'000300_SH': '沪深300', '000905_SH': '中证500', '000852_SH': '中证1000',
      '000688_SH': '科创50', '399006_SZ': '创业板'}
SIGN = '📎 来自钉钉OpenClaw'


def _md(c):
    return {'tag': 'markdown', 'content': c}


def _hr():
    return {'tag': 'hr'}


def _cols(cells, weights=None):
    """表格式一行（column_set）"""
    n = len(cells)
    weights = weights or [1] * n
    cols = []
    for c, w in zip(cells, weights):
        cols.append({'tag': 'column', 'width': 'weighted', 'weight': w,
                     'vertical_align': 'top',
                     'elements': [{'tag': 'markdown', 'content': c}]})
    return {'tag': 'column_set', 'flex_mode': 'none',
            'background_style': 'default', 'columns': cols}


def _sig_of(name, h, mode, sigmap):
    """从 unified_signal 取本期信号（可能没有）"""
    return sigmap.get((name, h, mode))


def fmt_row(code, h, mode, r, sig):
    nm = NM[code]
    if r.get('status') not in ('ok', 'below_bar'):
        return '%s\n　—' % nm, '选不出', '—', '—', '—', '—'
    i, o = r['is_stat'], r['oos'] or {}
    hit = (o.get('hit') or 0) * 100
    n = o.get('n') or 0
    net = (o.get('net') or 0)
    mark = '✅' if r['status'] == 'ok' else '·'
    base = (o.get('base_up') or 0) * 100
    # 信号
    if sig:
        s = sig.get('state', '-')
        pc = sig.get('pct')
        pcs = '%.2f' % pc if pc is not None else '-'
    else:
        s, pcs = '—', '—'
    model = r['cand'].replace('lgb_tri_', 'tri').replace('lgb_cls_', 'cls')
    lo = ' 只多' if r.get('longonly') else ''
    return (nm,
            '%s\n<font color=\'grey\'>%s</font>' % (mark, model),
            '%s\n分位 %s' % (pcs, ('%.2f' % r['thr'])),
            '%.0f%%\n<font color=\'grey\'>n=%d</font>' % (hit, n) if n else '—',
            '%+.2f%%%s' % (net, lo) if n else '—',
            '%s\n<font color=\'grey\'>基准%.0f%%</font>' % (s, base) if n else s)


def build_table(sel, h, mode, sigmap):
    rows = [
        _cols(['**指数**', '**模型**', '**本期分位**', '**OOS胜率**', '**净收益**', '**本期信号**'],
              [3, 4, 3, 3, 3, 3]),
        {'tag': 'hr'},
    ]
    for code in ORDER:
        r = sel['selected'][code][str(h)][mode]
        cells = fmt_row(code, h, mode, r, _sig_of(NM[code], h, mode, sigmap))
        rows.append(_cols(list(cells), [3, 4, 3, 3, 3, 3]))
    return rows


def build_card(sel, sig, updated):
    sigmap = {}
    if sig:
        for r in sig.get('signals', []):
            sigmap[(r['name'], r['h'], r['mode'])] = r

    # 统计达标槽位
    ok_rows = []
    for mode in ('broad', 'precise'):
        for h in (1, 5):
            for code in ORDER:
                r = sel['selected'][code][str(h)][mode]
                if r.get('status') == 'ok':
                    o = r['oos']
                    ok_rows.append((NM[code], h, mode, r['cand'], o['hit'], o['n'],
                                    o.get('net'), r.get('stable')))

    el = [_md(SIGN), _md('**统一口径后，20 个槽位（5 指数 × 2 周期 × 2 档）里只有 %d 个真正达标。**' % len(ok_rows))]

    if ok_rows:
        lines = []
        for nm, h, mode, cand, hit, n, net, stable in ok_rows:
            tag = '普适' if mode == 'broad' else '高精度'
            st = '' if stable else '　<font color=\'grey\'>（两段不稳）</font>'
            lines.append('· **%s T+%d · %s**　%s　胜率 **%.1f%%**(n=%d)　净 %+.2f%%%s'
                         % (nm, h, tag, cand, hit * 100, n, net or 0, st))
        el.append(_md('\n'.join(lines)))
        el.append(_md('<font color=\'grey\'>未列出的槽位一律观望 —— 普适档全部未过 60%/65% 线，'
                      '高精度档多数样本不足或覆盖率塌缩。</font>'))
    else:
        el.append(_md('**本期无任何槽位达标，全部观望。**'))

    el.append(_hr())
    for h in (1, 5):
        for mode in ('broad', 'precise'):
            tag = '普适（覆盖 35~65%）' if mode == 'broad' else '高精度（覆盖 3~22%）'
            el.append(_md('**%s · T+%d · %s**' % ('📈' if h == 1 else '📊', h, tag)))
            el += build_table(sel, h, mode, sigmap)
            el.append(_hr())

    el.append(_md(
        '<font color=\'grey\'>口径：四家族（ms 多尺度 / enh 增强 / train lgb / wd 五种子 bagging）'
        '的 |score| 一律换成**自身滚动 500 日百分位**，门槛用分位 → 跨家族才可比。'
        'IS 2019~2022 挑（Wilson 下限排序），OOS 2023 起只做验收，零后视。'
        'wd 与 ENS 的 T+5 逐日重叠样本已去重叠（名义 n 虚高 5 倍会抬高显著性）。'
        '达标线：T+1 ≥60%、T+5 ≥65%，且 n≥25、p≤0.10、净收益>0。'
        '净收益已扣单边 0.20% 成本。以上为量化信号，不构成投资建议。</font>'))

    card = {
        'config': {'wide_screen_mode': True},
        'header': {'template': 'blue',
                   'title': {'tag': 'plain_text',
                             'content': '🎯 统一选模周报 · T+1 / T+5 × 普适/高精度 · %s' % updated}},
        'elements': el,
    }
    return card, ok_rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--push', action='store_true')
    ap.add_argument('--chat', default=None)
    a = ap.parse_args()

    if not os.path.exists(SEL):
        print('缺 %s' % SEL)
        return 2
    sel = json.load(open(SEL, encoding='utf-8'))
    sig = json.load(open(SIG, encoding='utf-8')) if os.path.exists(SIG) else None
    card, ok_rows = build_card(sel, sig, sel.get('updated', '')[:10])

    body = json.dumps({'msg_type': 'interactive', 'card': card}, ensure_ascii=False)
    os.makedirs(os.path.dirname(CARD_FILE), exist_ok=True)
    json.dump(card, open(CARD_FILE, 'w', encoding='utf-8'), ensure_ascii=False)
    print('[card] elements=%d bytes=%d 达标槽位=%d' % (
        len(card['elements']), len(body.encode()), len(ok_rows)))
    for r in ok_rows:
        print('   %s T+%d %s %s %.1f%%(n=%d)' % (r[0], r[1], r[2], r[3], r[4] * 100, r[5]))

    if a.push:
        cmd = ['python3', os.path.join(WS, 'bin/feishu_push_card.py'), '--card-file', CARD_FILE]
        if a.chat:
            cmd += ['--chat', a.chat]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        print('[push]', (r.stdout or r.stderr).strip()[:300])
        return 0 if r.returncode == 0 else 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
