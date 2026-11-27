#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T+5 周报（统一选模 · 周末发）

大佬 2026-10-19 需求：
  「第二个是每周发 T+5 的，每周末。」

与 t1_daily.py 的分工
---------------------
  t1_daily.py  每天  T+1  明日方向 + 概率 + 历史胜率，高确定性发邮件
  t5_weekly.py 周末  T+5  下周方向 + 概率 + 历史胜率 + 两段稳定性（周报允许更细）

T+5 相比 T+1 多看两列
--------------------
  · 两段稳定：OOS 前半(2023~24)与后半(2025~)是否都过半 —— T+5 样本少，稳定性比单点胜率可信
  · 覆盖率塌缩：OOS 触发率不足 IS 一半 → 判废（门槛是按分位定的，分布变了门槛就失效）

用法：
  python3 bin/wide_ens/t5_weekly.py            # 打印
  python3 bin/wide_ens/t5_weekly.py --push     # 推飞书卡片
  python3 bin/wide_ens/t5_weekly.py --push --email
"""
import os
import sys
import json
import argparse
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
try:
    from wsenv import WS                     # case/_shared/wide_ens_lib/wsenv.py
except ImportError:                              # 单独拷出去跑时退到文件所在仓根
    WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from t1_daily import (ORDER, NM, SIGN, empirical_prob, pos_from_hit,
                      SEL_JSON, SIG_JSON)
import feats_note as FN

CARD_FILE = os.path.join(WS, '.cache/wide_ens/t5_card.json')
MODE_FIRST = ('precise', 'broad')


def pick_t5(sel, sigmap):
    rows = []
    for code in ORDER:
        best = None
        for mode in MODE_FIRST:
            r = sel['selected'][code]['5'][mode]
            if r.get('status') not in ('ok', 'below_bar'):
                continue
            key = (1 if r['status'] == 'ok' else 0, (r['oos'] or {}).get('hit') or 0)
            if best is None or key > best[0]:
                best = (key, dict(r, mode=mode))
        if best is None:
            rows.append(dict(code=code, name=NM[code], status='no_model',
                             state='无模型', hit=None, n=0, pos=0.0, pos_txt='观望'))
            continue
        r = best[1]
        sig = sigmap.get((NM[code], 5, r['mode'])) or {}
        sc = sig.get('score')
        oos = r['oos'] or {}
        hit = oos.get('hit')
        reliable = bool(r['status'] == 'ok' and not r.get('collapsed'))
        p_up, p_dn = empirical_prob(code, 5, r['cand'], sc) if sc is not None else (None, None)

        if sc is None:
            state, dirn = '无信号', 0
        elif not reliable:
            state, dirn = ('塌缩' if r.get('collapsed') else '观望（模型未达标）'), 0
        elif not sig.get('triggered'):
            state, dirn = '未触发（观望）', 0
        elif sc > 0:
            state, dirn = '看多', 1
        else:
            state, dirn = '看空（回避）', -1
        # 两段不稳 → 仓位砍半
        pos, pos_txt = pos_from_hit(hit) if (reliable and dirn == 1) else (0.0, '观望')
        if pos > 0 and r.get('stable') is False:
            pos = round(pos * 0.5, 2)
            pos_txt += '（两段不稳）'
        rows.append(dict(code=code, name=NM[code], status=r['status'], mode=r['mode'],
                         cand=r['cand'], thr=r['thr'], score=sc,
                         p_up=p_up, p_dn=p_dn, pct=sig.get('pct'),
                         sig_date=sig.get('sig_date'), triggered=sig.get('triggered'),
                         hit=hit, n=oos.get('n'), net=oos.get('net'),
                         base_up=oos.get('base_up'), is_cov=(r['is_stat'] or {}).get('cov'),
                         oos_cov=oos.get('cov'), reliable=reliable,
                         stable=r.get('stable'), collapsed=r.get('collapsed'),
                         longonly=r.get('longonly'),
                         state=state, dirn=dirn, pos=pos, pos_txt=pos_txt))
    return rows


def build_card(rows, date):
    def _md(c):
        return {'tag': 'markdown', 'content': c}

    def _hr():
        return {'tag': 'hr'}

    def _cols(cells, weights):
        return {'tag': 'column_set', 'flex_mode': 'none', 'background_style': 'default',
                'columns': [{'tag': 'column', 'width': 'weighted', 'weight': w,
                             'vertical_align': 'top',
                             'elements': [{'tag': 'markdown', 'content': c}]}
                            for c, w in zip(cells, weights)]}

    longs = [r for r in rows if r['dirn'] == 1]
    hi = [r for r in longs if r['pos'] >= 0.5]
    shorts = [r for r in rows if r['dirn'] == -1]

    el = [_md(SIGN)]
    if longs:
        el.append(_md('**下周看多：%s**' % '、'.join(
            '%s(%.0f%%)' % (r['name'], (r['p_up'] or 0) * 100) for r in longs)))
    else:
        el.append(_md('**下周无看多信号。**'))
    if hi:
        el.append(_md('🎯 **高确定性**：' + '　'.join(
            '**%s** 胜率 %.1f%%(n=%d) 建议 %s' % (r['name'], (r['hit'] or 0) * 100, r['n'] or 0, r['pos_txt'])
            for r in hi)))
    if shorts:
        el.append(_md('（看空回避：%s）' % '、'.join(r['name'] for r in shorts)))

    el.append(_md('**📊 下周（T+5）· %s 收盘信号**' % date))
    el.append(_cols(['**指数**', '**方向**', '**看多概率**', '**看空概率**',
                     '**历史胜率**', '**两段**', '**仓位**'], [3, 4, 3, 3, 3, 3, 3]))
    el.append(_hr())
    for r in rows:
        if r['status'] == 'no_model':
            el.append(_cols([r['name'], '无达标模型', '—', '—', '—', '—', '观望'],
                            [3, 4, 3, 3, 3, 3, 3]))
            continue
        pu = '%.0f%%' % (r['p_up'] * 100) if r['p_up'] is not None else '—'
        pd_ = '%.0f%%' % (r['p_dn'] * 100) if r['p_dn'] is not None else '—'
        hv = '%.1f%% %s' % ((r['hit'] or 0) * 100,
                            ('<font color=\'grey\'>n=%d</font>' % r['n']) if r['n'] else '')
        if r.get('collapsed'):
            st = '塌缩'
        elif r.get('stable') is True:
            st = '稳'
        elif r.get('stable') is False:
            st = '不稳'
        else:
            st = '—'
        # 指数名后面挂特征标签（只对白名单里的两个有内容）
        nm = r['name']
        if FN.enabled(r['code']):
            nm = '%s <font color=\'grey\'>%s</font>' % (nm, FN.feat_tag(r['code']))
        el.append(_cols([nm, r['state'], pu, pd_, hv, st, r['pos_txt']],
                        [3, 4, 3, 3, 3, 3, 3]))

    el.append(_hr())
    el.append(_md(
        '<font color=\'grey\'>口径：四家族 |score| 统一换成滚动 500 日分位，门槛用分位。'
        'IS 2019~2022 挑（Wilson 下限），OOS 2023 起验收。历史胜率 = OOS 真实命中（wd/ENS 的 T+5 已去重叠，'
        '名义 n 虚高 5 倍会抬高显著性）。两段 = OOS 前段(2023~24)与后段(2025~)是否都过半，不稳则仓位砍半。'
        '概率为**经验校准**（该模型历史上同方向、强度相近信号的_real 涨/跌频率），不是模型自报值。'
        '达标线 T+5 ≥65%、n≥25、p≤0.10、净>0。以上为量化信号，不构成投资建议。'
        '</font>'))
    # 特征版本（带 A/B 实测增益，不写“已启用”这种空话）
    el.append(_md('<font color=\'grey\'>%s</font>' % FN.feat_footer(ORDER)))

    card = {'config': {'wide_screen_mode': True},
            'header': {'template': 'turquoise' if longs else 'grey',
                       'title': {'tag': 'plain_text',
                                 'content': '📊 下周预测 · T+5 · %s' % date}},
            'elements': el}
    return card, hi


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--push', action='store_true')
    ap.add_argument('--email', action='store_true')
    ap.add_argument('--chat', default=None)
    a = ap.parse_args()

    if not os.path.exists(SEL_JSON):
        print('缺 %s' % SEL_JSON)
        return 2
    sel = json.load(open(SEL_JSON, encoding='utf-8'))
    sig = json.load(open(SIG_JSON, encoding='utf-8')) if os.path.exists(SIG_JSON) else {}
    sigmap = {(r['name'], r['h'], r['mode']): r for r in sig.get('signals', [])}

    rows = pick_t5(sel, sigmap)
    dates = sorted({r['sig_date'] for r in rows if r.get('sig_date')})
    date = dates[-1] if dates else (sel.get('updated', '')[:10])

    card, hi = build_card(rows, date)
    body = json.dumps({'msg_type': 'interactive', 'card': card}, ensure_ascii=False)
    json.dump(card, open(CARD_FILE, 'w', encoding='utf-8'), ensure_ascii=False)
    print('[card] bytes=%d 信号日=%s 看多=%d 高确定性=%d' % (
        len(body.encode()), date, len([r for r in rows if r['dirn'] == 1]), len(hi)))
    for r in rows:
        if r['status'] == 'no_model':
            print('  %-8s 无达标模型 → 观望' % r['name'])
            continue
        print('  %-8s %-18s P涨=%s P跌=%s 胜率=%.1f%%(n=%d) 稳定=%s 仓位=%s' % (
            r['name'], r['state'],
            ('%.0f%%' % (r['p_up'] * 100)) if r['p_up'] is not None else '-',
            ('%.0f%%' % (r['p_dn'] * 100)) if r['p_dn'] is not None else '-',
            (r['hit'] or 0) * 100, r['n'] or 0,
            '稳' if r.get('stable') is True else ('不稳' if r.get('stable') is False else '—'),
            r['pos_txt']))

    if a.push:
        cmd = ['python3', os.path.join(WS, 'bin/feishu_push_card.py'), '--card-file', CARD_FILE]
        if a.chat:
            cmd += ['--chat', a.chat]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        print('[push]', (r.stdout or r.stderr).strip()[:200])

    if a.email and hi:
        lines = ['# 🎯 下周高确定性机会 · T+5 · %s' % date, '']
        for r in hi:
            lines.append('- **%s**　看多概率 %.0f%%　历史胜率 %.1f%%(n=%d)　仓位 %s　净 %+.2f%%'
                         % (r['name'], (r['p_up'] or 0) * 100, (r['hit'] or 0) * 100,
                            r['n'] or 0, r['pos_txt'], (r['net'] or 0)))
        lines += ['', '> 量化信号，不构成投资建议。']
        import tempfile
        with tempfile.NamedTemporaryFile('w', suffix='.md', delete=False, encoding='utf-8') as f:
            f.write('\n'.join(lines))
            bf = f.name
        r = subprocess.run([sys.executable, os.path.join(WS, 'scripts/send_email.py'),
                            '--subject', '🎯 T+5 下周高确定性机会 · %s' % date,
                            '--body-file', bf, '--html'],
                           capture_output=True, text=True, timeout=180)
        print('[email]', (r.stdout or r.stderr).strip()[:200])
    elif a.email:
        print('[email] 本期无高确定性机会，不发邮件')
    return 0


if __name__ == '__main__':
    sys.exit(main())
