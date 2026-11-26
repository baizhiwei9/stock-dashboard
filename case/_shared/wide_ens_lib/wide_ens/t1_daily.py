#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T+1 日报（统一选模 · 日频）

大佬 2026-10-19 需求：
  「每天发 T+1 预测，告诉我历史胜率、看多看空、看多看空的概率。有高确定性机会发邮件。」

内容（每个指数一行）
--------------------
  方向（看多/看空/观望）｜模型分→**看多概率 / 看空概率**｜触发分位 vs 门槛
  ｜该档历史胜率 + 样本量｜据此建议仓位

关键口径
--------
1. **只报 OOS 达标的槽位**（unified_selected.json 里 status=ok）。
   未达标的指数照样列出，但明确写「无达标模型 → 观望」，不给概率（避免把噪声当信号）。
2. 概率 = 模型原始 score 换算。三分类 score = P(涨)−P(跌)：
       P(涨) = (1+score)/2,  P(跌) = (1−score)/2
   这是**模型自报值，不是校准概率**；校准概率看「历史胜率」那一列。
3. 历史胜率 = 该档位 OOS 真实命中率（unified_select 已去重叠、已扣成本）。

高确定性邮件
------------
  条件：仓位 ≥ 50%（即 OOS 胜率 ≥ 60%）且本期触发。
  发到 MAIL_TO，一次一封，含全部达标指数。

用法：
  python3 bin/wide_ens/t1_daily.py             # 打印
  python3 bin/wide_ens/t1_daily.py --push      # 推飞书卡片
  python3 bin/wide_ens/t1_daily.py --push --email
"""
import os
import sys
import json
import argparse
import subprocess
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
try:
    from wsenv import WS                     # case/_shared/wide_ens_lib/wsenv.py
except ImportError:                              # 单独拷出去跑时退到文件所在仓根
    WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

SEL_JSON = os.path.join(WS, '.cache/wide_ens/unified_selected.json')
SIG_JSON = os.path.join(WS, '.cache/wide_ens/unified_signal.json')
CARD_FILE = os.path.join(WS, '.cache/wide_ens/t1_card.json')
SIGN = '📎 来自钉钉OpenClaw'

ORDER = ['000300_SH', '000905_SH', '000852_SH', '000688_SH', '399006_SZ']
NM = {'000300_SH': '沪深300', '000905_SH': '中证500', '000852_SH': '中证1000',
      '000688_SH': '科创50', '399006_SZ': '创业板'}
# T+1 用哪一档：优先高精度（命中更高），样本不足时退回普适
MODE_FIRST = ('precise', 'broad')


def prob_from_score(score, cand):
    """把各家族的 score 换算成概率。

    ⚠️ 不能一律用 (1+score)/2：
       · tri（ms/enh/train 三分类）  score = P(涨)−P(跌) ∈[-1,1] → (1+s)/2 成立
       · ENS（z 均值）/ reg / wd    score 无上下界（实测到 ±3）→ 用正态 CDF
       · cls（train 二分类）        score = P(涨) ∈[0,1]      → 直接用
    混用会把 ENS 的 2.5 截成 100%，把回归的 4.18 截成 100%。
    """
    if score is None:
        return None, None
    s = float(score)
    name = (cand or '')
    if 'cls' in name:
        p_up = max(0.0, min(1.0, s))
        return p_up, 1.0 - p_up
    if 'tri' in name:
        p_up = max(0.0, min(1.0, (1 + s) / 2))
        return p_up, 1.0 - p_up
    # ENS / reg / wd：无界分数，当 z 值处理
    try:
        from scipy.stats import norm
        p_up = float(norm.cdf(s))
    except Exception:
        import math
        p_up = 0.5 * (1 + math.erf(s / math.sqrt(2)))
    return p_up, 1.0 - p_up


_POOL = {}


def get_pool():
    """懒加载候选曲线（用于经验概率校准）"""
    global _POOL
    if not _POOL:
        import unified_select as U
        _POOL = U.build_pool()
    return _POOL


def empirical_prob(code, h, cand, score):
    """经验校准概率 = 历史上「同方向 + 相似强度」信号的真实上涨/下跌频率。

    ⚠️ 为什么不用 (1+score)/2 或正态 CDF：四个家族分数含义不同。
       · tri  : score=P(涨)−P(跌)，(1+s)/2 才行
       · cls  : score=P(涨) 本身
       · ENS  : z 均值，可到 ±3
       · wd   : **回归输出，单位是预测收益 %**（实测 |score| 到 6）
       后面三种套公式会把 6.1 报成 100% 概率。而「历史上预测过 6% 涨的 213 次里有几次真涨」
       才是人能用的数字。

    做法：取该候选全部**已实现**样本，按同方向筛 + |score| 落在 [0.7x, 1.3x] 区间，
    统计真实命中频率；样本不够就逐步放宽区间。
    """
    if score is None or not cand:
        return None, None
    try:
        pool = get_pool()
        df = ((pool.get(code) or {}).get(h) or {}).get(cand)
    except Exception:
        df = None
    if df is None:
        return None, None
    d = df.dropna(subset=['ret']).copy()
    if not len(d):
        return None, None
    s = float(score)
    sign = 1 if s > 0 else -1
    a = d['score'].abs()
    cur = abs(s)
    # 同方向优先；先窄后宽找样本
    for width in (0.30, 0.50, 1.00):
        lo, hi = cur * (1 - width), cur * (1 + width)
        sel = d[(a >= lo) & (a <= hi) & (d['score'] * sign > 0)]
        if len(sel) >= 20:
            break
    else:
        # 仍不够 → 用同方向的全部样本
        sel = d[d['score'] * sign > 0]
        if len(sel) < 10:
            return None, None
    if sign > 0:
        p_up = float((sel['ret'] > 0).mean())
        return p_up, 1.0 - p_up
    p_dn = float((sel['ret'] < 0).mean())
    return 1.0 - p_dn, p_dn


def pos_from_hit(hit):
    """OOS 胜率 → 建议仓位（口径与五指数卡片一致）"""
    if hit is None:
        return 0.0, '无效'
    if hit >= 0.70:
        return 1.00, '满仓'
    if hit >= 0.65:
        return 0.70, '七成'
    if hit >= 0.60:
        return 0.50, '五成'
    if hit >= 0.55:
        return 0.30, '三成'
    return 0.0, '不做'


def _next_trade(d10):
    """信号日（上一交易日）→ 该信号适用的交易日（下一个交易日）

    大佬 2026-10-19 口径：**次日早晨发**，用上一交易日收盘数据预测当天（T+1）。
    所以卡片上要写清「依据 X 收盘 · 适用 Y」，不能只写信号日，否则早上看卡的人
    分不清这是昨天收盘的观点还是今天的。
    """
    try:
        sys.path.insert(0, os.path.join(WS, 'bin'))
        import trade_day as TD
        days = TD.cal_days()
        s8 = str(d10).replace('-', '')
        opens = sorted(k for k, v in days.items() if v == 1 and k > s8)
        if opens:
            return '%s-%s-%s' % (opens[0][:4], opens[0][4:6], opens[0][6:])
    except Exception:
        pass
    return ''


def pick_t1(sel, sigmap):
    """给每个指数挑一个 T+1 槽位：优先达标的，其次 precision 更高的"""
    rows = []
    for code in ORDER:
        best = None
        for mode in MODE_FIRST:
            r = sel['selected'][code]['1'][mode]
            if r.get('status') not in ('ok', 'below_bar'):
                continue
            cand = dict(r, mode=mode)
            # 排序：达标优先 → OOS 胜率高的优先
            key = (1 if r['status'] == 'ok' else 0, (r['oos'] or {}).get('hit') or 0)
            if best is None or key > best[0]:
                best = (key, cand)
        if best is None:
            rows.append(dict(code=code, name=NM[code], status='no_model',
                             state='无模型', hit=None, n=0, pos=0.0, pos_txt='观望'))
            continue
        r = best[1]
        sig = sigmap.get((NM[code], 1, r['mode'])) or {}
        sc = sig.get('score')
        pct = sig.get('pct')
        oos = r['oos'] or {}
        hit = oos.get('hit')
        reliable = r['status'] == 'ok'
        # 概率：经验校准（历史同方向相似强度的真实频率）
        if sc is not None:
            p_up, p_dn = empirical_prob(code, 1, r['cand'], sc)
        else:
            p_up = p_dn = None
        # 方向
        if sc is None:
            state, dirn = '无信号', 0
        elif not reliable:
            state, dirn = '观望（模型未达标）', 0
        elif not sig.get('triggered'):
            state, dirn = '未触发（观望）', 0
        elif sc > 0:
            state, dirn = '看多', 1
        else:
            state, dirn = '看空（回避）', -1
        pos, pos_txt = pos_from_hit(hit) if (reliable and dirn == 1) else (0.0, '观望')
        rows.append(dict(code=code, name=NM[code], status=r['status'],
                         mode=r['mode'], cand=r['cand'], thr=r['thr'],
                         score=sc, p_up=p_up, p_dn=p_dn, pct=pct,
                         sig_date=sig.get('sig_date'), triggered=sig.get('triggered'),
                         hit=hit, n=oos.get('n'), net=oos.get('net'),
                         base_up=oos.get('base_up'), reliable=reliable,
                         state=state, dirn=dirn, pos=pos, pos_txt=pos_txt,
                         stable=r.get('stable'), longonly=r.get('longonly')))
    return rows


def build_card(rows, date, stats_md):
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

    hi = [r for r in rows if r['dirn'] == 1 and r['pos'] >= 0.5]
    longs = [r for r in rows if r['dirn'] == 1]
    shorts = [r for r in rows if r['dirn'] == -1]

    el = [_md(SIGN)]
    if hi:
        el.append(_md('**🎯 高确定性机会（已发邮件）**\n' + '　'.join(
            '**%s** 看多 %.0f%%｜历史胜率 %.1f%%｜建议 %s' % (
                r['name'], (r['p_up'] or 0) * 100, (r['hit'] or 0) * 100, r['pos_txt'])
            for r in hi)))
    elif longs:
        el.append(_md('**本期无高确定性机会**（触发但胜率未过 60%，或指数未达标）'))
    else:
        el.append(_md('**本期无看多信号。**'))
    if shorts:
        el.append(_md('（看空回避：%s）' % '、'.join(r['name'] for r in shorts)))

    nxt = _next_trade(date)
    el.append(_md('**📈 依据 %s 收盘（上一交易日）· 适用 %s**' % (
        date, nxt or '下一交易日')))
    el.append(_cols(['**指数**', '**方向**', '**看多概率**', '**看空概率**',
                     '**历史胜率**', '**仓位**'], [3, 4, 3, 3, 3, 3]))
    el.append(_hr())
    for r in rows:
        if r['status'] == 'no_model':
            el.append(_cols(['%s' % r['name'], '无达标模型', '—', '—', '—', '观望'],
                            [3, 4, 3, 3, 3, 3]))
            continue
        pu = '%.0f%%' % (r['p_up'] * 100) if r['p_up'] is not None else '—'
        pd_ = '%.0f%%' % (r['p_dn'] * 100) if r['p_dn'] is not None else '—'
        hv = ('%.1f%%' % (r['hit'] * 100) if r['hit'] is not None else '—')
        nv = ('<font color=\'grey\'>n=%d</font>' % r['n']) if r['n'] else ''
        el.append(_cols([
            r['name'],
            r['state'],
            pu,
            pd_,
            '%s %s' % (hv, nv),
            r['pos_txt'],
        ], [3, 4, 3, 3, 3, 3]))

    el.append(_hr())
    el.append(_md(
        ("<font color='grey'>口径：概率 = <b>经验校准</b>（该模型历史上同方向、强度相近的 20+ 次信号里"
         "真实涨/跌的频率），不是模型自报值；四个家族分数含义不同（三分类=P涨−P跌，回归=预测收益%，"
         "ENS=z 均值），套公式会把回归的 6.1 报成 100%。「历史胜率」= 该档位 OOS 2023 起真实命中"
         "（已去重叠、已扣 0.2% 成本）。触发条件：|score| 的滚动 500 日分位 ≥ 门槛。"
         "仓位按历史胜率定：≥60% 五成、≥65% 七成、≥70% 满仓。未达标的模型不给仓位（宁可不做）。"
         "<b>本卡每交易日早晨发，用上一交易日收盘数据</b>，供当日开盘参考。"
         + (stats_md or '') + "以上为量化信号，不构成投资建议。</font>")))
    # 特征版本（隔夜因子带滚动失效门；周度特征只挂白名单指数）
    try:
        import feats_note as _FN
        el.append(_md("<font color='grey'>T+1 特征：隔夜跨市场（A50/恒指/中概）带滚动 250 日失效门，"
                      "失效自动降权中性。周度特征仅 T+5 卡片标注，T+1 不用。</font>"))
    except Exception:
        pass

    card = {'config': {'wide_screen_mode': True},
            'header': {'template': 'blue' if longs else 'grey',
                       'title': {'tag': 'plain_text',
                                 'content': '📈 T+1 明日预测 · 依据 %s 收盘' % date}},
            'elements': el}
    return card, hi


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--push', action='store_true')
    ap.add_argument('--email', action='store_true')
    ap.add_argument('--chat', default=None)
    a = ap.parse_args()

    if not os.path.exists(SEL_JSON):
        print('缺 %s，先跑 unified_select.py' % SEL_JSON)
        return 2
    sel = json.load(open(SEL_JSON, encoding='utf-8'))
    sig = json.load(open(SIG_JSON, encoding='utf-8')) if os.path.exists(SIG_JSON) else {}
    sigmap = {}
    for r in sig.get('signals', []):
        sigmap[(r['name'], r['h'], r['mode'])] = r

    rows = pick_t1(sel, sigmap)
    dates = sorted({r['sig_date'] for r in rows if r.get('sig_date')})
    date = dates[-1] if dates else (sel.get('updated', '')[:10])

    # 台账：先结算上一批到期，再记录本期
    try:
        import subprocess as sp
        sp.run([sys.executable, os.path.join(WS, 'bin/wide_ens/daily_ledger.py'),
                '--init', '--settle'], capture_output=True, text=True, timeout=300)
        st = sp.run([sys.executable, os.path.join(WS, 'bin/wide_ens/daily_ledger.py'),
                     '--stats'], capture_output=True, text=True, timeout=300)
        stats_md = ''
        for line in (st.stdout or '').splitlines():
            if 'T+1' in line or '合计' in line or '基准' in line:
                stats_md += line.strip() + '　'
    except Exception as e:
        stats_md = ''
        print('[ledger] 跳过 %s' % e)

    card, hi = build_card(rows, date, stats_md)
    body = json.dumps({'msg_type': 'interactive', 'card': card}, ensure_ascii=False)
    json.dump(card, open(CARD_FILE, 'w', encoding='utf-8'), ensure_ascii=False)
    print('[card] bytes=%d 信号日=%s 看多=%d 高确定性=%d' % (
        len(body.encode()), date, len([r for r in rows if r['dirn'] == 1]), len(hi)))

    for r in rows:
        if r['status'] == 'no_model':
            print('  %-8s 无达标模型 → 观望' % r['name'])
        else:
            print('  %-8s %-14s P涨=%s P跌=%s 胜率=%.1f%%(n=%d) 仓位=%s%s' % (
                r['name'], r['state'],
                ('%.0f%%' % (r['p_up'] * 100)) if r['p_up'] is not None else '-',
                ('%.0f%%' % (r['p_dn'] * 100)) if r['p_dn'] is not None else '-',
                (r['hit'] or 0) * 100, r['n'] or 0, r['pos_txt'],
                '' if r['reliable'] else ' [未达标]'))

    if a.push:
        cmd = ['python3', os.path.join(WS, 'bin/feishu_push_card.py'), '--card-file', CARD_FILE]
        if a.chat:
            cmd += ['--chat', a.chat]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        print('[push]', (r.stdout or r.stderr).strip()[:200])

    if a.email and hi:
        lines = ['# 🎯 高确定性机会 · T+1 · %s' % date, '']
        for r in hi:
            lines.append('- **%s**　看多概率 %.0f%%　历史胜率 %.1f%%(n=%d)　建议仓位 %s　净收益 %+.2f%%'
                         % (r['name'], (r['p_up'] or 0) * 100, (r['hit'] or 0) * 100,
                            r['n'] or 0, r['pos_txt'], (r['net'] or 0)))
        lines += ['', '> 历史胜率 = 该档位 2023 年起样本外真实命中率，已去重叠、已扣 0.2% 成本。',
                  '> 量化信号，不构成投资建议。']
        import tempfile
        with tempfile.NamedTemporaryFile('w', suffix='.md', delete=False, encoding='utf-8') as f:
            f.write('\n'.join(lines))
            bf = f.name
        r = subprocess.run([sys.executable, os.path.join(WS, 'scripts/send_email.py'),
                            '--subject', '🎯 T+1 高确定性机会 · %s' % date,
                            '--body-file', bf, '--html'],
                           capture_output=True, text=True, timeout=180)
        print('[email]', (r.stdout or r.stderr).strip()[:200])
    elif a.email:
        print('[email] 本期无高确定性机会，不发邮件')
    return 0


if __name__ == '__main__':
    sys.exit(main())
