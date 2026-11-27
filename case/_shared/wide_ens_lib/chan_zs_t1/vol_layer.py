#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""中枢内剔除 + 波动率三层 · 是否提高日频 T+1

回答用户 2026-10-21 21:56 的两个问题：

Q1「中枢内 + AC负 判作不做，和原来比是不是有提高？我想用它替换日频预测」
  → 关键区分两种用法：
     (a) 作为【排除过滤器】：剔除这段后，剩下样本的命中率是多少？（这才是"替换"的实际效果）
     (b) 作为【独立预测】：这段本身命中率 43.41%，低于 51% 基准 → 反向用没意义
  → 算「剩余段命中率」+ 覆盖率 + 扣成本净收益，给出"能不能替换"的答案

Q2「中枢内切割波动率后再预测，是不是也提高？科创50用高波动、其他用中低波动，对不对？」
  → 波动率口径两种都算：
     · pred_std（模型 5 种子标准差，设计文档口径）
     · 实际已实现波动率 rv20（近 20 日收益率标准差）
     · 逐指数自己算扩张窗口分位切点（绝不搬科创50的绝对区间）
  → 全网格：中枢状态 × 波动率三档 × 逐指数

用法：python3 research/chan_zs_t1/vol_layer.py
"""
import os
import sys
import json
import warnings

warnings.filterwarnings('ignore')
try:
    from wsenv import WS                     # case/_shared/wide_ens_lib/wsenv.py
except ImportError:                              # 单独拷出去跑时退到文件所在仓根
    WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(WS, 'research/chan_zs_t1'))

import numpy as np
import pandas as pd
from scipy.stats import binomtest

from zhongshu_v3 import chan_v3, load, ev

OUT = os.path.join(WS, '.cache/chan_zs_t1')
COST = 0.20


def prep(span=3):
    """建逐日面板：加 chan / rv20 / pred_std 分位 / rv20 分位（扩张窗口，只用历史）"""
    raws, panels = load()
    dfs = {}
    for nm, pn in panels.items():
        r = raws[nm]
        cs = chan_v3(r['close'], r['high'], r['low'], span=span)
        d = pn.copy()
        d['chan'] = cs['chan'].reindex(d.index).fillna('none')
        # 已实现波动率：近 20 日对数收益标准差
        lr = np.log(r['close'] / r['close'].shift(1))
        d['rv20'] = lr.rolling(20).std().reindex(d.index)
        # 扩张窗口分位（min 250，只用历史，不偷未来）
        for col, out in [('pred_std', 'psq'), ('rv20', 'rvq')]:
            s = d[col]
            q = pd.Series(np.nan, index=d.index)
            vals = []
            for i, v in enumerate(s.values):
                if np.isfinite(v):
                    vals.append(v)
                if len(vals) >= 250 and np.isfinite(v):
                    arr = np.array(vals)
                    q.iloc[i] = (arr < v).mean()
            d[out] = q
        dfs[nm] = d
    return dfs


def layer(q):
    """分位 → 三档"""
    return np.where(q < 1 / 3, '低波', np.where(q < 2 / 3, '中波', '高波'))


def evm(sub, label=None):
    r = ev(sub)
    if r['n'] > 0:
        r['net'] = r['net'] - COST
    r['label'] = label
    return r


def merge(lst):
    lst = [x for x in lst if x['n'] > 0]
    if not lst:
        return None
    n = sum(x['n'] for x in lst)
    k = sum(x['k'] for x in lst)
    base = sum(x['base'] * x['n'] for x in lst) / n
    net = sum(x['net'] * x['n'] for x in lst) / n
    return dict(n=n, hit=k / n, base=base, p=binomtest(k, n, 0.5).pvalue,
                net=net, netgross=net + COST)


def main():
    dfs = prep()
    rep = {}

    # ══════════════════════════════════════════════════════════
    # Q1：中枢内 + AC负 剔除后，剩余样本是什么水平
    # ══════════════════════════════════════════════════════════
    print('=' * 110)
    print('Q1 · 把「中枢内 + AC≤−0.10」判作不做，剩余样本命中率是多少（OOS 2023+）')
    print('=' * 110)

    for tag, lo in [('OOS 2023+', '2023-01-01'), ('全期 2016+', '2016-01-01')]:
        print('\n▸ %s' % tag)
        print('%-30s %7s %6s %9s %9s %9s %9s %8s' % (
            '方案', 'n', '覆盖率', '命中率', '基准', '超额', 'p值', '净收益'))
        print('-' * 96)

        A = {'l0': [], 'drop': [], 'dropin': [], 'keep': [], 'neg': []}
        for nm, df in dfs.items():
            d = df[df.index >= lo]
            if len(d) < 60:
                continue
            bad = (d['chan'] == 'inside') & (d['ac1_40'] <= -0.10)
            A['l0'].append(evm(d))
            A['bad'] = A.get('bad', [])
            A['bad'].append(evm(d[bad]))
            A['drop'].append(evm(d[~bad]))                       # 只剔除这一个格子
            A['dropin'].append(evm(d[d['chan'] != 'inside']))     # 剔除整个中枢内
            A['neg'].append(evm(d[d['ac1_40'] <= -0.10]))         # 只剔除所有 AC负

        rows = [
            ('L0 原来（全样本）', 'l0'),
            ('剔除 中枢内+AC负（单格）', 'drop'),
            ('剔除 全部中枢内', 'dropin'),
            ('剔除 所有 AC≤−0.10', 'neg'),
            ('[对照] 被剔除的那段本身', 'bad'),
        ]
        base_l0 = merge(A['l0'])
        for lbl, key in rows:
            m = merge(A[key])
            if not m:
                continue
            cov = m['n'] / base_l0['n'] if key != 'bad' else -1
            sig = '★' if m['p'] < 0.05 else ('☆' if m['p'] < 0.10 else ' ')
            print('%-30s %7d %5s %8.2f%% %8.2f%% %+8.2f%% %9.4f %+7.3f%% %s' % (
                lbl, m['n'], ('%.1f%%' % (cov * 100)) if cov >= 0 else '-',
                m['hit'] * 100, m['base'] * 100, (m['hit'] - m['base']) * 100,
                m['p'], m['net'], sig))
        rep[tag] = {k: merge(v) for k, v in A.items()}

        # 核心结论数字
        l0 = merge(A['l0'])
        dr = merge(A['drop'])
        if l0 and dr:
            print('\n   ▸ 剔除法效果：%.2f%% → %.2f%% （%+.2f pp），覆盖率 %.1f%%' % (
                l0['hit'] * 100, dr['hit'] * 100, (dr['hit'] - l0['hit']) * 100,
                dr['n'] / l0['n'] * 100))
            print('   ▸ 被剔除段命中 %.2f%%（n=%d）→ 剔对了' % (
                merge(A['bad'])['hit'] * 100, merge(A['bad'])['n']) if merge(A['bad'])['hit'] < l0['hit']
                  else '   ▸ 被剔除段命中反而更高 → 剔错了')

    # ══════════════════════════════════════════════════════════
    # Q2：波动率三层 × 中枢状态
    # ══════════════════════════════════════════════════════════
    for vcol, vname in [('psq', 'pred_std（模型5种子标准差）'), ('rvq', 'rv20（已实现波动率）')]:
        print('\n' + '=' * 110)
        print('Q2 · 波动率三层 × 中枢状态 ｜口径：%s ｜OOS 2023+' % vname)
        print('=' * 110)
        print('%-16s %-8s %7s %9s %9s %9s %8s' % ('中枢状态', '波动档', 'n', '命中率', '基准', '超额', 'p值'))
        print('-' * 76)
        A = {}
        for nm, df in dfs.items():
            d = df[df.index >= '2023-01-01']
            if len(d) < 60:
                continue
            d = d.copy()
            d['vl'] = layer(d[vcol])
            for st, sm in [('中枢内', d['chan'] == 'inside'),
                           ('中枢外', d['chan'].isin(['above', 'below'])),
                           ('全部', pd.Series(True, index=d.index))]:
                for vl in ['低波', '中波', '高波']:
                    m = sm & (d['vl'] == vl)
                    r = evm(d[m])
                    if r['n'] > 0:
                        A.setdefault((st, vl), []).append(r)
        for st in ['中枢内', '中枢外', '全部']:
            for vl in ['低波', '中波', '高波']:
                m = merge(A.get((st, vl), []))
                if not m:
                    continue
                sig = '★' if m['p'] < 0.05 else ('☆' if m['p'] < 0.10 else ' ')
                print('%-16s %-8s %7d %8.2f%% %8.2f%% %+8.2f%% %9.4f %s' % (
                    st, vl, m['n'], m['hit'] * 100, m['base'] * 100,
                    (m['hit'] - m['base']) * 100, m['p'], sig))
            print('-' * 76)

        # 中枢内 波动档 单调性
        ins = [(vl, merge(A.get(('中枢内', vl), []))) for vl in ['低波', '中波', '高波']]
        vals = [(vl, m['hit'] * 100) for vl, m in ins if m]
        if len(vals) >= 2:
            print('   ▸ 中枢内 波动档走势: ' + ' → '.join('%s %.2f%%' % v for v in vals))
            best = max(vals, key=lambda x: x[1])
            print('   ▸ 中枢内最优档: %s (%.2f%%)' % best)

    # ══════════════════════════════════════════════════════════
    # Q2b：逐指数 中枢内 波动档（用户问「科创50高波动、其他中低」对不对）
    # ══════════════════════════════════════════════════════════
    print('\n' + '=' * 110)
    print('Q2b · 逐指数 × 中枢内 × 波动三档（OOS 2023+，pred_std 口径）')
    print('=' * 110)
    print('%-10s %-18s %-18s %-18s %s' % ('指数', '低波', '中波', '高波', '最优档'))
    print('-' * 92)
    best_map = {}
    for nm, df in dfs.items():
        d = df[df.index >= '2023-01-01'].copy()
        d['vl'] = layer(d['psq'])
        ins = d[d['chan'] == 'inside']
        cells, bestv, bestl = [], -1, '-'
        for vl in ['低波', '中波', '高波']:
            r = evm(ins[ins['vl'] == vl])
            if r['n'] >= 20:
                cells.append('%.1f%%(n=%d)' % (r['hit'] * 100, r['n']))
                if r['hit'] > bestv:
                    bestv, bestl = r['hit'], vl
            else:
                cells.append('-')
        best_map[nm] = bestl
        print('%-10s %-18s %-18s %-18s %s' % (nm, cells[0], cells[1], cells[2], bestl))

    print('\n' + '=' * 110)
    print('Q2c · 逐指数 × 中枢内 × 波动三档（rv20 口径，对照）')
    print('=' * 110)
    print('%-10s %-18s %-18s %-18s %s' % ('指数', '低波', '中波', '高波', '最优档'))
    print('-' * 92)
    for nm, df in dfs.items():
        d = df[df.index >= '2023-01-01'].copy()
        d['vl'] = layer(d['rvq'])
        ins = d[d['chan'] == 'inside']
        cells, bestv, bestl = [], -1, '-'
        for vl in ['低波', '中波', '高波']:
            r = evm(ins[ins['vl'] == vl])
            if r['n'] >= 20:
                cells.append('%.1f%%(n=%d)' % (r['hit'] * 100, r['n']))
                if r['hit'] > bestv:
                    bestv, bestl = r['hit'], vl
            else:
                cells.append('-')
        print('%-10s %-18s %-18s %-18s %s' % (nm, cells[0], cells[1], cells[2], bestl))

    # ══════════════════════════════════════════════════════════
    # Q3：组合方案对比 —— 「中枢内按波动率做」vs「日中剔除」
    # ══════════════════════════════════════════════════════════
    print('\n' + '=' * 110)
    print('Q3 · 三种方案横向对比（OOS 2023+，5 指数合计）')
    print('=' * 110)
    print('%-38s %7s %6s %9s %9s %8s' % ('方案', 'n', '覆盖率', '命中率', '超额', '净收益'))
    print('-' * 84)
    A = {'l0': [], 'A': [], 'B': [], 'C': []}
    for nm, df in dfs.items():
        d = df[df.index >= '2023-01-01'].copy()
        d['vl'] = layer(d['psq'])
        A['l0'].append(evm(d))
        # 方案A：全部保留，只剔除 中枢内+AC负
        bad = (d['chan'] == 'inside') & (d['ac1_40'] <= -0.10)
        A['A'].append(evm(d[~bad]))
        # 方案B：中枢内用最优波动档，中枢外全保留
        #   逐指数最优档不同，这里用「中枢内只留中波」的通用规则做对照
        keepB = (~(d['chan'] == 'inside')) | (d['vl'] == '中波')
        A['B'].append(evm(d[keepB]))
        # 方案C：中枢内全部剔除（= 纯中枢外）
        keepC = d['chan'] != 'inside'
        A['C'].append(evm(d[keepC]))
    base = merge(A['l0'])
    for lbl, key in [('L0 原来（全样本）', 'l0'),
                     ('方案A：剔除 中枢内+AC负', 'A'),
                     ('方案B：中枢内只留中波', 'B'),
                     ('方案C：中枢内全部剔除（=纯中枢外）', 'C')]:
        m = merge(A[key])
        if not m:
            continue
        print('%-38s %7d %5.1f%% %8.2f%% %+8.2f%% %+7.3f%%' % (
            lbl, m['n'], m['n'] / base['n'] * 100, m['hit'] * 100,
            (m['hit'] - m['base']) * 100, m['net']))

    with open(os.path.join(OUT, 'vol_layer.json'), 'w', encoding='utf-8') as f:
        json.dump({k: v for k, v in rep.items()}, f, ensure_ascii=False, indent=1, default=float)
    print('\n-> .cache/chan_zs_t1/vol_layer.json')


if __name__ == '__main__':
    main()
