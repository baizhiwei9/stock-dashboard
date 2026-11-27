#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""笔间隔敏感性分析（缠论口径稳健性）

背景：上一轮 outside_ac.py 用的是「简化分型版」缠论——合并K线 → 分型 → 笔
（笔端点分型最小间隔 = 3 根 merged bar）→ 中枢直接建在笔上，没有「段」这一层。
用户 2026-10-21 17:48 要求做笔间隔敏感性分析，看结论是否随口径变化。

本脚本固定预测分（panel 里的 pred，来自原 walk-forward 模型，与缠论无关），
只改变「中枢」的构造口径，重算中枢状态，然后重跑同一套 AC 阶梯：

  L0 全样本 ｜ L1 中枢外(上+下) ｜ L3 中枢外+AC≥+0.10
  L4 中枢外+AC≤−0.10 ｜ L5 above+AC≥+0.10 ｜ L6 below+AC≥+0.10
  L7 中枢内+AC≤−0.10（上一轮标记的「该关掉不做」）

口径变体：
  span=2/3/4/5      笔端点分型的最小 merged-bar 间隔（默认 3）
  nomerge          不做K线包含处理，直接在原始K线上找分型
  merge5           包含处理 + 5 根一段的粗略「段」聚合（用相邻笔端点距离近似更大级别）

关键看：L1 vs L0、L3 vs L1、L5 的命中率是否在所有口径下同向。

用法：python3 research/chan_zs_t1/span_sens.py
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
OUT = os.path.join(WS, '.cache/chan_zs_t1')
RAW = os.path.join(WS, '.cache/weekly_forecast/raw')

import numpy as np
import pandas as pd
from scipy.stats import binomtest

IDX = [('idx_hs300.csv', '沪深300'),
       ('idx_zz500.csv', '中证500'),
       ('idx_zz1000.csv', '中证1000'),
       ('idx_kc50.csv', '科创50'),
       ('idx_cyb.csv', '创业板')]

SEGS = [('全期', '2016-01-01'), ('OOS', '2023-01-01')]
LBL = ['L0 全样本', 'L1 中枢外(上+下)', 'L3 中枢外+AC≥+0.10',
       'L4 中枢外+AC≤−0.10', 'L5 above+AC≥+0.10', 'L6 below+AC≥+0.10',
       'L7 inside+AC≤−0.10']


# ───────────── 缠论构造（可配 span / merge） ─────────────
def merge_bars(h, l):
    out = []
    for i, (hi, lo) in enumerate(zip(h, l)):
        if not out:
            out.append([hi, lo, i, i])
            continue
        ph, pl, _, pe = out[-1]
        if (hi <= ph and lo >= pl) or (hi >= ph and lo <= pl):
            up = len(out) < 2 or out[-1][0] >= out[-2][0]
            if up:
                out[-1] = [max(ph, hi), max(pl, lo), out[-1][2], i]
            else:
                out[-1] = [min(ph, hi), min(pl, lo), out[-1][2], i]
        else:
            out.append([hi, lo, i, i])
    return out


def fractals(mb):
    fr = []
    for i in range(1, len(mb) - 1):
        a, b, c = mb[i - 1], mb[i], mb[i + 1]
        if b[0] > a[0] and b[0] > c[0] and b[1] > a[1] and b[1] > c[1]:
            fr.append((i, 1, b[0]))
        elif b[0] < a[0] and b[0] < c[0] and b[1] < a[1] and b[1] < c[1]:
            fr.append((i, -1, b[1]))
    return fr


def build_bi(mb, span=3):
    fr = fractals(mb)
    bi = []
    for idx, t, px in fr:
        if not bi:
            bi.append([t, px, idx])
        elif bi[-1][0] == t:
            if (t == 1 and px > bi[-1][1]) or (t == -1 and px < bi[-1][1]):
                bi[-1] = [t, px, idx]
        else:
            if abs(idx - bi[-1][2]) >= span:
                bi.append([t, px, idx])
    return bi


def segs_from_bi(bi):
    segs = []
    for k in range(len(bi) - 1):
        a, b = bi[k], bi[k + 1]
        segs.append({"up": b[0] == 1,
                     "lo": min(a[1], b[1]), "hi": max(a[1], b[1]),
                     "i0": a[2], "i1": b[2]})
    return segs


def pivot_series(segs, mb, n_raw, need=3):
    """need = 构成中枢所需的重叠笔数（3 为标称三笔重叠）"""
    zg = np.full(n_raw, np.nan)
    zd = np.full(n_raw, np.nan)
    cur = None
    done_at = []
    for k, s in enumerate(segs):
        j1 = s["i1"] + 1
        raw_end = mb[j1][3] if j1 < len(mb) else n_raw
        done_at.append((raw_end, k))

    j = 0
    for i in range(n_raw):
        while j < len(done_at) and done_at[j][0] <= i:
            k = done_at[j][1]
            if k >= need - 1:
                w = segs[k - need + 1:k + 1]
                _zg = min(x["hi"] for x in w)
                _zd = max(x["lo"] for x in w)
                if _zg > _zd:
                    cur = (_zd, _zg, k - need + 1)
            j += 1
        if cur is not None:
            zd[i], zg[i] = cur[0], cur[1]
    return zd, zg


def segs_from_bi_seg(bi, per=3):
    """简化「段」层：每 per 笔合成一段（段内取所有端点极值），中枢建在段上"""
    segs = []
    for k in range(0, len(bi) - per + 1, per):
        w = bi[k:k + per]
        segs.append({"up": w[-1][0] == 1,
                     "lo": min(x[1] for x in w), "hi": max(x[1] for x in w),
                     "i0": w[0][2], "i1": w[-1][2]})
    return segs


def chan_states(close, high, low, span=3, merge=True, need=3, seg=0):
    if merge:
        mb = merge_bars(high, low)
    else:
        mb = [[h, l, i, i] for i, (h, l) in enumerate(zip(high, low))]
    n = len(close)
    if len(mb) < 12:
        return pd.Series('none', index=close.index)
    bi = build_bi(mb, span)
    if len(bi) < need + 1:
        return pd.Series('none', index=close.index)
    segs = segs_from_bi_seg(bi, seg) if seg else segs_from_bi(bi)
    zd, zg = pivot_series(segs, mb, n, need)
    c = close.values
    st = np.where(np.isnan(zd), 'none',
                  np.where((c >= zd) & (c <= zg), 'inside',
                           np.where(c > zg, 'above', 'below')))
    return pd.Series(st, index=close.index)


# ───────────── 评估 ─────────────
def ev(sub):
    v = sub[(sub['y'] != 0) & sub['y'].notna() & sub['pred'].notna()]
    n = len(v)
    if n == 0:
        return dict(n=0, k=0, hit=float('nan'), base=float('nan'), p=float('nan'))
    k = int(((v['y'] > 0) == (v['pred'] > 0)).sum())
    return dict(n=n, k=k, hit=k / n, base=float((v['y'] > 0).mean()),
                p=float(binomtest(k, n, 0.5).pvalue))


def masks(df):
    out = df['chan'].isin(['above', 'below'])
    return {
        'L0 全样本': pd.Series(True, index=df.index),
        'L1 中枢外(上+下)': out,
        'L3 中枢外+AC≥+0.10': out & (df['ac1_40'] >= 0.10),
        'L4 中枢外+AC≤−0.10': out & (df['ac1_40'] <= -0.10),
        'L5 above+AC≥+0.10': (df['chan'] == 'above') & (df['ac1_40'] >= 0.10),
        'L6 below+AC≥+0.10': (df['chan'] == 'below') & (df['ac1_40'] >= 0.10),
        'L7 inside+AC≤−0.10': (df['chan'] == 'inside') & (df['ac1_40'] <= -0.10),
    }


VARIANTS = [
    ('基准 span=3 有包含', dict(span=3, merge=True)),
    ('span=2 有包含', dict(span=2, merge=True)),
    ('span=4 有包含', dict(span=4, merge=True)),
    ('span=5 有包含', dict(span=5, merge=True)),
    ('span=3 无包含处理', dict(span=3, merge=False)),
    ('span=5 + 4笔重叠中枢', dict(span=5, merge=True, need=4)),
    ('段层: 3笔成段 span=3', dict(span=3, merge=True, seg=3)),
    ('段层: 3笔成段 span=5', dict(span=5, merge=True, seg=3)),
]


def main():
    # 预载 raw
    raws = {}
    for fn, nm in IDX:
        d = pd.read_csv(os.path.join(RAW, fn), dtype={'trade_date': str})
        d['dt'] = pd.to_datetime(d['trade_date'], format='%Y%m%d', errors='coerce')
        d = d.dropna(subset=['dt']).set_index('dt').sort_index()
        d = d[~d.index.duplicated(keep='last')]
        raws[nm] = dict(close=pd.to_numeric(d['close'], errors='coerce').astype(float),
                        high=pd.to_numeric(d['high'], errors='coerce').astype(float),
                        low=pd.to_numeric(d['low'], errors='coerce').astype(float))

    panels = {}
    for fn, nm in IDX:
        p = os.path.join(OUT, 'panel_%s.csv' % nm)
        if os.path.exists(p):
            panels[nm] = pd.read_csv(p, index_col=0, parse_dates=True)

    allrep = {}
    for vname, kw in VARIANTS:
        print('\n' + '#' * 108)
        print('## 口径变体：%s' % vname)
        print('#' * 108)
        # 先算各指数中枢分布
        dist = {}
        dfs = {}
        for nm, pn in panels.items():
            r = raws[nm]
            st = chan_states(r['close'], r['high'], r['low'], **kw)
            df = pn.copy()
            df['chan'] = st.reindex(df.index).fillna('none')
            dist[nm] = df['chan'].value_counts(normalize=True).to_dict()
            dfs[nm] = df
        print('\n▸ 中枢状态分布（占有效预测日比例）')
        print('   %-8s %8s %8s %8s %8s' % ('指数', 'above', 'inside', 'below', '无中枢'))
        for nm in panels:
            d = dist[nm]
            print('   %-8s %7.1f%% %7.1f%% %7.1f%% %7.1f%%' % (
                nm, d.get('above', 0) * 100, d.get('inside', 0) * 100,
                d.get('below', 0) * 100, d.get('none', 0) * 100))

        rep = {}
        for tag, lo in SEGS:
            agg = {}
            for nm, df in dfs.items():
                sub = df[df.index >= lo]
                if len(sub) < 60:
                    continue
                for lbl, m in masks(sub).items():
                    r = ev(sub[m])
                    if r['n'] == 0:
                        continue
                    agg.setdefault(lbl, []).append(r)
            print('\n▸ %s' % tag)
            print('   %-22s %6s %8s %8s %8s %9s' % ('方案', 'n', '命中率', '基准', '超额', 'p值'))
            print('   ' + '-' * 74)
            rows = {}
            for lbl in LBL:
                lst = agg.get(lbl, [])
                if not lst:
                    continue
                n = sum(x['n'] for x in lst)
                k = sum(x['k'] for x in lst)
                hit, base = k / n, sum(x['base'] * x['n'] for x in lst) / n
                pv = binomtest(k, n, 0.5).pvalue
                sig = '★' if pv < 0.05 else ('☆' if pv < 0.10 else ' ')
                rows[lbl] = dict(n=n, hit=hit, base=base, p=pv)
                print('   %-22s %6d %7.2f%% %7.2f%% %+7.2f%% %9.4f %s' % (
                    lbl, n, hit * 100, base * 100, (hit - base) * 100, pv, sig))
            # 提升链条
            def g(l):
                return rows[l]['hit'] if l in rows else None
            l0, l1, l3, l4 = g(LBL[0]), g(LBL[1]), g(LBL[2]), g(LBL[3])
            print('   ▸ 链:', end=' ')
            if l0 and l1:
                print('中枢外 %+.2fpp' % ((l1 - l0) * 100), end='  ')
            if l1 and l3:
                print('+AC正 %+.2fpp' % ((l3 - l1) * 100), end='  ')
            if l1 and l4:
                print('+AC负 %+.2fpp' % ((l4 - l1) * 100), end='')
            print()
            rep[tag] = rows
        allrep[vname] = rep

    # ─── 跨口径一致性 ───
    print('\n' + '#' * 108)
    print('## 跨口径一致性（同一指标在各变体下的命中率）')
    print('#' * 108)
    for tag in ['全期', 'OOS']:
        print('\n▸ %s' % tag)
        print('   %-22s %-22s %s' % ('变体', '样本区间', '  '.join('%-11s' % l.split(' ')[0] for l in LBL)))
        print('   ' + '-' * 100)
        for vname, _ in VARIANTS:
            r = allrep[vname].get(tag, {})
            cells = []
            for l in LBL:
                cells.append('%-11s' % ('%6.2f%%' % (r[l]['hit'] * 100) if l in r else '-'))
            print('   %-22s %-22s %s' % (vname, tag, ' '.join(cells)))

    # ─── 稳健性汇总（相对 L0 / 相对 L1 的提升，跨口径统计）───
    print('\n' + '#' * 108)
    print('## 稳健性汇总：各口径相对提升（pp），看符号一致性')
    print('#' * 108)
    for tag in ['全期', 'OOS']:
        print('\n▸ %s' % tag)
        diffs = {'L1-L0': [], 'L3-L1': [], 'L4-L1': [], 'L5-L0': [], 'L6-L0': [], 'L7-L0': []}
        for vname, _ in VARIANTS:
            r = allrep[vname].get(tag, {})
            if not r:
                continue
            def h(l):
                return r[l]['hit'] * 100 if l in r else None
            pairs = [('L1-L0', LBL[1], LBL[0]), ('L3-L1', LBL[2], LBL[1]),
                     ('L4-L1', LBL[3], LBL[1]), ('L5-L0', LBL[4], LBL[0]),
                     ('L6-L0', LBL[5], LBL[0]), ('L7-L0', LBL[6], LBL[0])]
            for key, a, b in pairs:
                ha, hb = h(a), h(b)
                if ha is not None and hb is not None:
                    diffs[key].append(ha - hb)
        print('   %-10s %8s %9s %8s %10s' % ('对比', '均值', '中位', '正/总', '结论'))
        print('   ' + '-' * 60)
        for key, v in diffs.items():
            if not v:
                continue
            pos = sum(1 for x in v if x > 0)
            verdict = '✅ 稳健同向' if pos == len(v) else ('⚠️ 多数同向' if pos >= len(v) - 1 else '❌ 不稳')
            print('   %-10s %+7.2fpp %+8.2fpp %7s %10s' % (
                key, float(np.mean(v)), float(np.median(v)), '%d/%d' % (pos, len(v)), verdict))

    with open(os.path.join(OUT, 'span_sens.json'), 'w', encoding='utf-8') as f:
        json.dump(allrep, f, ensure_ascii=False, indent=1, default=float)
    print('\n-> .cache/chan_zs_t1/span_sens.json')


if __name__ == '__main__':
    main()
