#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""缠论中枢 v3 · 日频正确口径 + 中枢外 AC 判定

【修正历史】
v1（17:47 那轮）致命 bug：每完成一笔就重取最近 3 笔重叠当新中枢 → 中枢一直跟着价格漂，
   寿命只有 10.7 天、13 年切换 313 次、带宽只有日均振幅 2.15 倍。这不是中枢。
v2（20:56）修正为「成立即锁定 + 延伸」，但结束条件用「两笔不回中枢」→ 太松，
   一个中枢吃掉 8~9 笔、寿命 75~101 天，13 年只有 22~43 个 → 用户 20:59 指出「日频应该有很多个」。
v3（本轮）：结束条件改为**一笔完全离开即结束**（缠论标准：离开段成立就换中枢），
   中枢数量回到 47~56 个/13年，中枢内占比 30~33%，日频粒度。

【v3 定义】
  1. 笔：合并K线 → 顶底分型 → 笔（端点最小间隔 span=3 根 merged bar）
  2. 中枢成立：连续三笔，ZG = min(hi) > ZD = max(lo) → 锁定 [ZD, ZG]，之后 ZD/ZG 不变
  3. 中枢延伸：后续笔若与 [ZD, ZG] 有重叠 → 继续延伸
  4. 中枢结束：某一笔**完全**在 ZG 之上或 ZD 之下 → 中枢结束
     （下一中枢从这根离开笔重新起算，这是缠论「离开段即新结构起点」的近似）
  5. 严格因果：一笔要等它的端点后一根 merged bar 出现才确认

【中枢外 × AC 判定】
  L1 中枢外（above+below，不加 AC）
  L3 中枢外 + AC≥+0.10
  L4 中枢外 + AC≤−0.10
  L2 中枢外 + |AC|<0.10
  L5 above + AC≥+0.10 / L6 below + AC≥+0.10
  另给「中枢外 AC 五分位单调性」和「中枢外 AC vs 次日收益 IC」

用法：
  python3 research/chan_zs_t1/zhongshu_v3.py --diag
  python3 research/chan_zs_t1/zhongshu_v3.py --test
"""
import os
import sys
import json
import argparse
import warnings

warnings.filterwarnings('ignore')
try:
    from wsenv import WS                     # case/_shared/wide_ens_lib/wsenv.py
except ImportError:                              # 单独拷出去跑时退到文件所在仓根
    WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(WS, 'research/chan_zs_t1'))

import numpy as np
import pandas as pd
from scipy.stats import binomtest, spearmanr

from span_sens import merge_bars, build_bi, segs_from_bi, IDX, RAW

OUT = os.path.join(WS, '.cache/chan_zs_t1')


def pivots_v3(segs, mb, n_raw):
    """中枢：三笔重叠成立 → 锁定 → 延伸 → 一笔完全离开即结束"""
    done = []
    for k, s in enumerate(segs):
        j1 = s['i1'] + 1
        done.append(mb[j1][3] if j1 < len(mb) else n_raw)

    plist = []
    k = 0
    while k + 2 < len(segs):
        w = segs[k:k + 3]
        zg = min(x['hi'] for x in w)
        zd = max(x['lo'] for x in w)
        if zg <= zd:
            k += 1
            continue
        end = k + 2
        m = k + 3
        while m < len(segs):
            s = segs[m]
            if (s['lo'] <= zg) and (s['hi'] >= zd):
                end = m          # 仍重叠 → 延伸
                m += 1
            else:
                break            # 一笔完全离开 → 中枢结束
        plist.append((k, end, zd, zg))
        k = end                  # 离开笔作为下一中枢的起点

    zd_a = np.full(n_raw, np.nan)
    zg_a = np.full(n_raw, np.nan)
    pid = np.full(n_raw, -1, dtype=int)
    for idx, (s0, s1, zd, zg) in enumerate(plist):
        t_on = done[s0 + 2]
        t_off = done[s1] if s1 < len(segs) else n_raw
        lo_i = min(max(t_on, 0), n_raw)
        hi_i = min(max(t_off, 0), n_raw)
        if hi_i > lo_i:
            zd_a[lo_i:hi_i] = zd
            zg_a[lo_i:hi_i] = zg
            pid[lo_i:hi_i] = idx

    # ⚠️ 关键修正（2026-10-21 23:05）：中枢结束到下一个中枢成立之间有空窗，
    # 这段价格仍是「相对上一个中枢」的离开段，不能标成「无中枢」。
    # 用上一个中枢的 ZD/ZG 前向填充，直到新中枢成立。
    if len(plist):
        for i in range(1, n_raw):
            if not np.isfinite(zd_a[i]) and np.isfinite(zd_a[i - 1]):
                zd_a[i] = zd_a[i - 1]
                zg_a[i] = zg_a[i - 1]
                pid[i] = pid[i - 1]
    return zd_a, zg_a, pid, plist


def chan_v3(close, high, low, span=3):
    mb = merge_bars(high.values if hasattr(high, 'values') else high,
                    low.values if hasattr(low, 'values') else low)
    n = len(close)
    if len(mb) < 12:
        return pd.DataFrame({'zd': np.nan, 'zg': np.nan, 'chan': 'none', 'pid': -1}, index=close.index)
    bi = build_bi(mb, span)
    if len(bi) < 4:
        return pd.DataFrame({'zd': np.nan, 'zg': np.nan, 'chan': 'none', 'pid': -1}, index=close.index)
    segs = segs_from_bi(bi)
    zd, zg, pid, _ = pivots_v3(segs, mb, n)
    c = close.values
    st = np.where(~np.isfinite(zd), 'none',
                  np.where((c >= zd) & (c <= zg), 'inside',
                           np.where(c > zg, 'above', 'below')))
    return pd.DataFrame({'zd': zd, 'zg': zg, 'chan': st, 'pid': pid}, index=close.index)


def load():
    raws, panels = {}, {}
    for fn, nm in IDX:
        d = pd.read_csv(os.path.join(RAW, fn), dtype={'trade_date': str})
        d['dt'] = pd.to_datetime(d['trade_date'], format='%Y%m%d', errors='coerce')
        d = d.dropna(subset=['dt']).set_index('dt').sort_index()
        d = d[~d.index.duplicated(keep='last')]
        raws[nm] = dict(close=pd.to_numeric(d['close'], errors='coerce').astype(float),
                        high=pd.to_numeric(d['high'], errors='coerce').astype(float),
                        low=pd.to_numeric(d['low'], errors='coerce').astype(float))
        p = os.path.join(OUT, 'panel_%s.csv' % nm)
        if os.path.exists(p):
            panels[nm] = pd.read_csv(p, index_col=0, parse_dates=True)
    return raws, panels


def ev(sub):
    v = sub[(sub['y'] != 0) & sub['y'].notna() & sub['pred'].notna()]
    n = len(v)
    if n == 0:
        return dict(n=0, k=0, hit=float('nan'), base=float('nan'), p=float('nan'), net=float('nan'))
    k = int(((v['y'] > 0) == (v['pred'] > 0)).sum())
    return dict(n=n, k=k, hit=k / n, base=float((v['y'] > 0).mean()),
                p=float(binomtest(k, n, 0.5).pvalue),
                net=float((np.sign(v['pred']) * v['y']).mean()))


def diag():
    raws, panels = load()
    print('=' * 108)
    print('v3 中枢诊断：日频粒度是否合理（对比 v1 的假中枢）')
    print('=' * 108)
    print('%-8s %8s %12s %10s %9s %9s %9s %9s' % (
        '指数', '中枢个数', '平均寿命(日)', '中枢内天数', 'above', 'inside', 'below', '无中枢'))
    print('-' * 108)
    for nm, r in raws.items():
        cs = chan_v3(r['close'], r['high'], r['low'])
        npiv = int(cs['pid'].max() + 1) if cs['pid'].max() >= 0 else 0
        n = len(cs)
        vc = cs['chan'].value_counts(normalize=True)
        n_ins = int((cs['chan'] == 'inside').sum())
        print('%-8s %8d %12.1f %10d %8.1f%% %8.1f%% %8.1f%% %8.1f%%' % (
            nm, npiv, n / max(npiv, 1), n_ins,
            vc.get('above', 0) * 100, vc.get('inside', 0) * 100,
            vc.get('below', 0) * 100, vc.get('none', 0) * 100))
    print('\n参考：v1 假中枢 = 22~313 个 / 寿命 10.7 日 ｜ v3 目标 = 每指数 40+ 个、inside 覆盖 30%+')


LBL = ['L0 全样本', 'L1 中枢外(上+下)', 'L2 中枢外+|AC|<0.10',
       'L3 中枢外+AC≥+0.10', 'L4 中枢外+AC≤−0.10',
       'L5 above+AC≥+0.10', 'L6 below+AC≥+0.10', 'L7 中枢内+AC≤−0.10']


def masks(df):
    out = df['chan'].isin(['above', 'below'])
    ins = df['chan'] == 'inside'
    return {
        'L0 全样本': pd.Series(True, index=df.index),
        'L1 中枢外(上+下)': out,
        'L2 中枢外+|AC|<0.10': out & (df['ac1_40'].abs() < 0.10),
        'L3 中枢外+AC≥+0.10': out & (df['ac1_40'] >= 0.10),
        'L4 中枢外+AC≤−0.10': out & (df['ac1_40'] <= -0.10),
        'L5 above+AC≥+0.10': (df['chan'] == 'above') & (df['ac1_40'] >= 0.10),
        'L6 below+AC≥+0.10': (df['chan'] == 'below') & (df['ac1_40'] >= 0.10),
        'L7 中枢内+AC≤−0.10': ins & (df['ac1_40'] <= -0.10),
    }


def test(span=3):
    raws, panels = load()
    store = {}
    for nm, pn in panels.items():
        r = raws[nm]
        cs = chan_v3(r['close'], r['high'], r['low'], span=span)
        df = pn.copy()
        df['chan'] = cs['chan'].reindex(df.index).fillna('none')
        df['idx'] = nm
        store[nm] = df

    for tag, lo in [('全期 2016+', '2016-01-01'), ('OOS 2023+', '2023-01-01')]:
        print('\n' + '=' * 108)
        print('## v3 · %s（span=%d）' % (tag, span))
        print('=' * 108)
        agg, per = {}, {}
        for nm, df in store.items():
            df = df[df.index >= lo]
            if len(df) < 60:
                continue
            rows = {}
            for lbl, m in masks(df).items():
                rows[lbl] = ev(df[m])
                if rows[lbl]['n'] > 0:
                    agg.setdefault(lbl, []).append(rows[lbl])
            per[nm] = rows
        print('%-24s %7s %8s %8s %8s %9s %8s' % ('方案', 'n', '命中率', '基准', '超额', 'p值', '净收益'))
        print('-' * 90)
        for lbl in LBL:
            lst = agg.get(lbl, [])
            if not lst:
                continue
            n = sum(x['n'] for x in lst); k = sum(x['k'] for x in lst)
            hit = k / n
            base = sum(x['base'] * x['n'] for x in lst) / n
            net = sum(x['net'] * x['n'] for x in lst) / n - 0.20
            pv = binomtest(k, n, 0.5).pvalue
            sig = '★' if pv < 0.05 else ('☆' if pv < 0.10 else ' ')
            print('%-24s %7d %7.2f%% %7.2f%% %+7.2f%% %9.4f %+7.3f%% %s' % (
                lbl, n, hit * 100, base * 100, (hit - base) * 100, pv, net, sig))

        # AC 对中枢外的增量
        def g(l):
            lst = agg.get(l, [])
            if not lst: return None
            n = sum(x['n'] for x in lst)
            return sum(x['k'] for x in lst) / n
        l1, l3, l4, l2 = g(LBL[1]), g(LBL[3]), g(LBL[4]), g(LBL[2])
        print('\n▸ 中枢外再加 AC 的增量')
        if l1 and l3:
            print('   AC≥+0.10  vs 纯中枢外 : %+.2f pp（%.2f%% → %.2f%%）' % ((l3 - l1) * 100, l1 * 100, l3 * 100))
        if l1 and l4:
            print('   AC≤−0.10  vs 纯中枢外 : %+.2f pp（%.2f%% → %.2f%%）' % ((l4 - l1) * 100, l1 * 100, l4 * 100))
        if l1 and l2:
            print('   |AC|<0.10 vs 纯中枢外 : %+.2f pp（%.2f%% → %.2f%%）' % ((l2 - l1) * 100, l1 * 100, l2 * 100))
        if l3 and l4:
            print('   AC正 − AC负（中枢外内）: %+.2f pp' % ((l3 - l4) * 100))

        print('\n▸ 逐指数（中枢外 × AC 三档）')
        print('   %-8s %-16s %-16s %-16s %s' % ('指数', 'AC≥+0.10', 'AC≤−0.10', '|AC|<0.10', 'AC正−AC负'))
        for nm, rows in per.items():
            def f(x):
                return '-' if x['n'] < 20 else '%.1f%%(n=%d)' % (x['hit'] * 100, x['n'])
            a, b, c = rows[LBL[3]], rows[LBL[4]], rows[LBL[2]]
            d = '%+.1f pp' % ((a['hit'] - b['hit']) * 100) if a['n'] >= 20 and b['n'] >= 20 else '-'
            print('   %-8s %-16s %-16s %-16s %s' % (nm, f(a), f(b), f(c), d))

    # AC 五分位单调性（中枢外）
    print('\n' + '=' * 108)
    print('## 中枢外：AC 状态值五分位 → 命中率（单调性检验）')
    print('=' * 108)
    print('%-8s %-8s %8s %8s %8s %8s %8s %10s %8s' % (
        '指数', '区间', 'Q1最负', 'Q2', 'Q3', 'Q4', 'Q5最正', 'Spearman', 'p'))
    print('-' * 92)
    for nm, df in store.items():
        for tag, lo in [('全期', '2016-01-01'), ('OOS', '2023-01-01')]:
            d = df[(df.index >= lo) & df['chan'].isin(['above', 'below'])]
            d = d[(d['y'] != 0) & d['pred'].notna() & d['ac1_40'].notna()]
            if len(d) < 150:
                continue
            d = d.copy()
            d['q'] = pd.qcut(d['ac1_40'], 5, labels=False, duplicates='drop')
            hits, acs = [], []
            for q in range(5):
                s = d[d['q'] == q]
                if len(s) < 20:
                    hits.append(float('nan')); acs.append(float('nan')); continue
                hits.append(((s['y'] > 0) == (s['pred'] > 0)).mean() * 100)
                acs.append(s['ac1_40'].mean())
            ok = [i for i in range(5) if np.isfinite(hits[i])]
            sp = spearmanr([acs[i] for i in ok], [hits[i] for i in ok]) if len(ok) >= 4 else None
            print('%-8s %-8s %s %10s %8s' % (
                nm, tag,
                ' '.join('%7.1f%%' % h if np.isfinite(h) else '      -' for h in hits),
                ('%+.3f' % sp.statistic) if sp else '-',
                ('%.3f' % sp.pvalue) if sp else '-'))
    print('\n（越单调 = AC 状态值在中枢外越有信息量）')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--diag', action='store_true')
    ap.add_argument('--test', action='store_true')
    ap.add_argument('--span', type=int, default=3)
    a = ap.parse_args()
    if a.diag: diag()
    if a.test: test(a.span)
    if not (a.diag or a.test):
        diag(); test(a.span)


if __name__ == '__main__':
    main()
