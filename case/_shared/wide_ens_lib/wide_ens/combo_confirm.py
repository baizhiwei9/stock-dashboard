#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""高确定模式组合验证：多个宽基同时触发时，胜率能否叠加？

大佬 2026-10-19 17:18「继续」→ 承接上一轮提议：
  「把『中证1000 高确定 + 沪深300 高确定』组一个组合，看同时触发时的胜率」

核心问题：
  ① 单独做每一个高确定档位，OOS 成绩多少
  ② 两个/多个同时触发时，胜率是否显著高于单个（共振是否有效）
  ③ 叠加后方向冲突怎么处理（一个看多一个看空）
  ④ 组合的覆盖率还剩多少（会不会共振少到没有交易机会）

⚠️ 零后视：每个信号日只用当日之前已实现的数据判断该档位是否有效，
   但档位参数本身来自 IS(2019~2022) 挑 → OOS(2023起) 只做验收。

用法：
  python3 bin/wide_ens/combo_confirm.py
  python3 bin/wide_ens/combo_confirm.py --save
"""
import os
import sys
import json
import itertools
import argparse
import datetime
import warnings

warnings.filterwarnings('ignore')

HERE = os.path.dirname(os.path.abspath(__file__))
try:
    from wsenv import WS                     # case/_shared/wide_ens_lib/wsenv.py
except ImportError:                              # 单独拷出去跑时退到文件所在仓根
    WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import numpy as np
import pandas as pd

from model_select import (NAMES, build_pool, add_ensemble, Cand, TH_GRID,
                          _binom_p, IS_END, OOS_START, HIST_START)

CACHE = os.path.join(WS, '.cache/wide_ens')
OUT_JSON = os.path.join(CACHE, 'combo_confirm.json')
H = 5                      # T+5 周频
COST = 0.20                # 单边成本 %


def wilson(w, n, z=1.645):
    if n <= 0:
        return 0.0
    p = w / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return float(max(0.0, (c - m) / d))


def load_conf_cfg():
    """从 two_mode_table.json 读各指数的高确定档位配置"""
    p = os.path.join(CACHE, 'two_mode_table.json')
    if not os.path.exists(p):
        return {}
    d = json.load(open(p, encoding='utf-8'))
    out = {}
    for code, r in (d.get('selected') or {}).items():
        c = r.get('conf')
        if c:
            out[code] = dict(cand=c['cand'], thr=c['thr'],
                             oos_hit=c['oos_hit'], oos_n=c['oos_n'],
                             lb=c['lb'], cover=c['cover'])
    return out


def series_for(code, cfg, pool):
    """取出该指数指定档位的逐日 (signal, dir_ret) 序列"""
    raw = dict((pool.get(code) or {}).get(H) or {})
    if not raw:
        return None
    cdf = add_ensemble({k: v for k, v in raw.items() if v.index.min() < IS_END})
    if cfg['cand'] not in cdf:
        return None
    c = Cand(cfg['cand'], cdf[cfg['cand']])
    sc = pd.Series(c.score, index=c.dates)
    dr = pd.Series(c.dir_ret, index=c.dates)
    abs_sc = sc.abs()
    trig = abs_sc >= cfg['thr']
    # 方向：score 正=看多(+1)，负=看空(-1)
    direction = np.sign(sc)
    return pd.DataFrame({
        'trig': trig,
        'dir': direction,
        'ret': dr,          # 已按方向调整的收益（乘过 sign）
        'raw_ret': pd.Series(c.ret, index=c.dates),  # 原始收益，用于组合等权
        'hit': (dr > 0).astype(int),
    })


def build_panel(cfgs, pool):
    """构造所有指数对齐的宽表"""
    frames = {}
    for code, cfg in cfgs.items():
        s = series_for(code, cfg, pool)
        if s is not None:
            frames[code] = s
    if not frames:
        return None
    dates = sorted(set().union(*[set(s.index) for s in frames.values()]))
    panel = {}
    for code, s in frames.items():
        panel[(code, 'trig')] = s['trig'].reindex(dates).fillna(False).astype(bool)
        panel[(code, 'dir')] = s['dir'].reindex(dates).fillna(0)
        panel[(code, 'ret')] = s['ret'].reindex(dates)
        panel[(code, 'raw')] = s['raw_ret'].reindex(dates)
        panel[(code, 'hit')] = s['hit'].reindex(dates)
    return pd.DataFrame(panel, index=pd.Index(dates, name='date'))


def combo_stats(P, codes, dates_sub=None, direction_mode='net'):
    """给定成分指数，算组合成绩

    direction_mode:
      net   = 所有触发成分的方向等权（看多+1 / 看空-1 平均），净方向下注
      all-long = 只要有触发就买（不含做空，最保守）
    """
    sub = P.loc[dates_sub] if dates_sub is not None else P
    recs = []
    for dt, row in sub.iterrows():
        dirs, raws, hits = [], [], []
        for c in codes:
            if (c, 'trig') not in row.index:
                continue
            if not row[(c, 'trig')]:
                continue
            d = row[(c, 'dir')]
            r = row[(c, 'raw')]
            if pd.isna(r):
                continue
            dirs.append(d)
            raws.append(r)
            hits.append(row[(c, 'hit')])
        if not dirs:
            continue
        if direction_mode == 'net':
            net_dir = float(np.mean(dirs))
            # 净方向接近 0 = 互相抵消，视为不出手
            if abs(net_dir) < 0.5:
                continue
            dr = float(np.mean(raws)) * (1.0 if net_dir > 0 else -1.0)
            hit = int(dr > 0)
        else:
            dr = float(np.mean(raws))
            hit = int(dr > 0)
        recs.append(dict(date=dt, n=len(dirs), net_dir=float(np.mean(dirs)),
                         dir_ret=dr, hit=hit))
    if not recs:
        return dict(n=0)
    D = pd.DataFrame(recs)
    w = int(D['hit'].sum())
    n = len(D)
    return dict(
        n=n, hit=w / n, w=w,
        lb=wilson(w, n),
        p=_binom_p(w, n),
        raw=float(D['dir_ret'].mean()),
        net=float(D['dir_ret'].mean()) - COST,
        avg_parts=float(D['n'].mean()),
        conflict=float((D['net_dir'].abs() < 1).mean()),
        dates=[str(x)[:10] for x in D['date'].tolist()],
    )


def run(save=False):
    cfgs = load_conf_cfg()
    if not cfgs:
        print('❌ 没有高确定档位配置，先跑 two_mode_table.py')
        return None
    pool = build_pool()
    P = build_panel(cfgs, pool)
    if P is None:
        print('❌ 无法构造面板')
        return None

    oos = [d for d in P.index if d >= OOS_START]
    codes = list(cfgs.keys())

    # ① 单指数
    singles = {}
    for c in codes:
        singles[c] = combo_stats(P, [c], oos)

    # ② 两两组合
    pairs = {}
    for a, b in itertools.combinations(codes, 2):
        pairs['%s+%s' % (a, b)] = combo_stats(P, [a, b], oos)

    # ③ 全组合
    allc = combo_stats(P, codes, oos)

    # ④ 只看「同向」的子集（净方向 = +1 或 -1，完全一致）
    def same_dir_stats(P, codes, oos):
        recs = []
        for dt in oos:
            row = P.loc[dt]
            dirs, raws = [], []
            for c in codes:
                if (c, 'trig') not in row.index or not row[(c, 'trig')]:
                    continue
                if pd.isna(row[(c, 'raw')]):
                    continue
                dirs.append(row[(c, 'dir')])
                raws.append(row[(c, 'raw')])
            if len(dirs) < 2:
                continue
            if len(set(dirs)) != 1:
                continue                       # 方向不一致 → 跳过
            d = dirs[0]
            dr = float(np.mean(raws)) * (1.0 if d > 0 else -1.0)
            recs.append(dict(hit=int(dr > 0), r=dr))
        if not recs:
            return dict(n=0)
        D = pd.DataFrame(recs)
        w = int(D['hit'].sum())
        n = len(D)
        return dict(n=n, hit=w / n, lb=wilson(w, n), p=_binom_p(w, n),
                    raw=float(D['r'].mean()), net=float(D['r'].mean()) - COST)

    same = same_dir_stats(P, codes, oos)
    same2 = {}
    for a, b in itertools.combinations(codes, 2):
        same2['%s+%s' % (a, b)] = same_dir_stats(P, [a, b], oos)

    out = dict(updated=datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
               h=H, oos_start=OOS_START, cost=COST,
               cfgs=cfgs, singles=singles, pairs=pairs, all_combo=allc,
               same_dir_all=same, same_dir_pairs=same2)
    if save:
        with open(OUT_JSON, 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        print('[save] %s' % OUT_JSON)
    return out


def fmt(out):
    L = ['=' * 118]
    L.append('高确定模式组合验证 · T+5 周频（OOS %s 起）' % out['oos_start'])
    L.append('=' * 118)
    L.append('')
    L.append('【各指数高确定档位（单做）】')
    L.append('%-9s %-26s %6s %8s %14s %9s %8s'
             % ('指数', '模型', '门槛', '覆盖', 'OOS胜率', '置信下限', '组合日数'))
    L.append('-' * 118)
    for code, s in out['singles'].items():
        cfg = out['cfgs'][code]
        if not s.get('n'):
            L.append('%-9s %s' % (NAMES[code], '无触发'))
            continue
        L.append('%-9s %-26s %6.2f %7.0f%% %8.1f%% (n=%-3d) %8.1f%% %8d'
                 % (NAMES[code], cfg['cand'][:26], cfg['thr'], cfg['cover'] * 100,
                    s['hit'] * 100, s['n'], s['lb'] * 100, s['n']))
    L.append('')
    L.append('【两两组合（净方向，方向冲突自动跳过）】')
    L.append('%-22s %8s %14s %9s %8s %8s'
             % ('组合', '组合日数', 'OOS胜率', '置信下限', '平均成分', '冲突率'))
    L.append('-' * 118)
    for k, s in sorted(out['pairs'].items(), key=lambda kv: -(kv[1].get('hit') or 0)):
        if not s.get('n'):
            L.append('%-22s %s' % (k, '无触发'))
            continue
        nm = '+'.join(NAMES[c] for c in k.split('+'))
        L.append('%-22s %8d %8.1f%% (n=%-3d) %8.1f%% %8.1f %7.0f%%'
                 % (nm[:22], s['n'], s['hit'] * 100, s['n'], s['lb'] * 100,
                    s['avg_parts'], s['conflict'] * 100))
    L.append('')
    a = out['all_combo']
    if a.get('n'):
        L.append('【全组合（4 个一起）】%d 日 ｜ 胜率 %.1f%% ｜ 下限 %.1f%% ｜ 平均成分 %.1f ｜ 冲突率 %.0f%%'
                 % (a['n'], a['hit'] * 100, a['lb'] * 100, a['avg_parts'], a['conflict'] * 100))
    L.append('')
    L.append('【严格同向（所有触发成分方向完全一致，≥2 个）】')
    s = out['same_dir_all']
    if s.get('n'):
        L.append('  全组合：n=%d ｜ 胜率 %.1f%% ｜ 下限 %.1f%% ｜ p=%.5f ｜ 单次 %+.2f%%'
                 % (s['n'], s['hit'] * 100, s['lb'] * 100, s['p'], s['raw']))
    else:
        L.append('  全组合：无同向样本')
    for k, v in sorted(out['same_dir_pairs'].items(), key=lambda kv: -(kv[1].get('hit') or 0)):
        if not v.get('n'):
            continue
        nm = '+'.join(NAMES[c] for c in k.split('+'))
        L.append('  %-20s n=%-3d 胜率 %5.1f%% 下限 %5.1f%% 单次 %+.2f%%'
                 % (nm[:20], v['n'], v['hit'] * 100, v['lb'] * 100, v['raw']))
    return '\n'.join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--save', action='store_true')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()
    out = run(save=a.save)
    if not out:
        return 1
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    else:
        print(fmt(out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
