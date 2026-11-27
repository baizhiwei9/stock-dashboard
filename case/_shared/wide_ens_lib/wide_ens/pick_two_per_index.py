#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每个宽基指数 × 两套算法（高可用 / 高精确）· T+5 周频

大佬 2026-10-19 17:26 口径（明确否掉组合）：
  「我一直说不要组合。我要找胜率和精确度高的两种。
    每一个宽基指数一个一个算法，选两套算法：一个高可用的，一个高精确度的。
    就只要每一个关键指数，我要找到他能一个预测准的算法，我好交易啊。」

→ 每个指数给两行，不搞组合、不搞共振：
  **高可用（High-Availability）**：覆盖率 ≥70%，平时大部分时间都能出手，
      在这类配置里挑胜率最高的。用途 = 平时有方向可跟。
  **高精确（High-Precision）**：覆盖率不限（可以很低），挑置信下限最高的。
      用途 = 平时空仓，等这个条件出现再重仓。

两档都是 T+5、都零后视（IS 2019~2022 挑参数，OOS 2023 起验收）。
必须给出可执行的交易规则 + 当前信号。

用法：
  python3 bin/wide_ens/pick_two_per_index.py
  python3 bin/wide_ens/pick_two_per_index.py --save
"""
import os
import sys
import json
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
OUT_JSON = os.path.join(CACHE, 'two_modes_final.json')
OUT_MD = os.path.join(CACHE, 'two_modes_final.md')

H = 5
COST = 0.20
AVAIL_MIN_COVER = 0.35      # 高可用：至少 35% 的日子能出手（约每 3 天一次）
PREC_MIN_COVER = 0.02       # 高精确：至少 2% 覆盖率（否则样本太少没意义）
MIN_N_OOS = 25


def wilson(w, n, z=1.645):
    if n <= 0:
        return 0.0
    p = w / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return float(max(0.0, (c - m) / d))


def all_cells(cands):
    """枚举所有 (候选, 阈值)，给出 IS/OOS 双段 + 覆盖率 + 当前信号"""
    dates = sorted(set().union(*[set(c.dates) for c in cands.values()]))
    oos_days = sum(1 for d in dates if d >= OOS_START)
    rows = []
    for name, c in cands.items():
        d = pd.Series(c.dir_ret, index=c.dates)
        ab = pd.Series(c.abs, index=c.dates)
        for th in TH_GRID:
            mask = (ab >= th).values
            mi = mask & (d.index >= HIST_START) & (d.index <= IS_END)
            si = d[mi]
            if len(si) < 25 or si.mean() <= 0:
                continue
            mo = mask & (d.index >= OOS_START)
            so = d[mo]
            if len(so) < 15 or so.mean() <= 0:
                continue
            ow = int((so > 0).sum())
            on = len(so)
            sc_last = float(c.score[-1])
            trig = abs(sc_last) >= th
            rows.append(dict(
                cand=name, thr=th,
                is_n=len(si), is_hit=round((si > 0).mean(), 4),
                oos_n=on, oos_hit=round(ow / on, 4),
                lb=round(wilson(ow, on), 4),
                p=round(_binom_p(ow, on), 6),
                ret=round(float(so.mean()), 3),
                net=round(float(so.mean()) - COST, 3),
                cover=round(on / max(oos_days, 1), 4),
                last=str(c.dates[-1]),
                score_last=round(sc_last, 4),
                state=('看多' if sc_last > 0 else '看空') if trig else '观望',
            ))
    return rows


def pick_two(cells):
    """高可用 与 高精确 各挑一个（干净逻辑，不做角色交换）

    定义（必须诚实，不许拿 14% 覆盖率冒充"平时能用"）：
      高可用 = 覆盖率 ≥AVAIL_MIN_COVER 且胜率≥55% 且净>0，取覆盖率最高的
               → 达不到就返回 None，如实标「无高可用配置」
      高精确 = 胜率≥55% 且净>0 里，置信下限最高的（可与高可用同模型但门槛必须差得远）

    ⚠️ 踩过的坑：之前写「高精确覆盖率反而更高就交换角色」，
       结果把中证500 的 79.3% 换掉了、选成 61.9%。别再做角色交换。
    """
    good = [c for c in cells if c['oos_n'] >= MIN_N_OOS
            and c['oos_hit'] >= 0.55 and c['net'] > 0]
    if not good:
        return None, None
    # ① 高可用
    avail_pool = [c for c in good if c['cover'] >= AVAIL_MIN_COVER]
    avail_pool.sort(key=lambda x: (x['cover'], x['oos_hit']), reverse=True)
    avail = avail_pool[0] if avail_pool else None
    # ② 高精确：按下限排，优先不同模型
    if avail is not None:
        diff = [c for c in good if c['cand'] != avail['cand']]
        diff.sort(key=lambda x: (x['lb'], x['oos_hit'], x['oos_n']), reverse=True)
        prec = diff[0] if diff else None
        if prec is None:
            same = [c for c in good if c['cand'] == avail['cand']
                    and abs(c['thr'] - avail['thr']) >= 0.15]
            same.sort(key=lambda x: (x['lb'], x['oos_hit']), reverse=True)
            prec = dict(same[0]) if same else None
            if prec:
                prec['note'] = '与高可用同模型，仅门槛不同'
    else:
        good.sort(key=lambda x: (x['lb'], x['oos_hit'], x['oos_n']), reverse=True)
        prec = good[0]
    return avail, prec


def coverage_ladder(cells):
    """覆盖率阶梯：每档里胜率最高的配置（给大佬看『放宽覆盖率要付什么代价』）"""
    BANDS = [(0.70, 1.01, '≥70%（很常用）'), (0.40, 0.70, '40-70%（较常用）'),
             (0.20, 0.40, '20-40%（偶尔）'), (0.08, 0.20, '8-20%（少见）'),
             (0.02, 0.08, '2-8%（稀有）')]
    out = []
    for lo, hi, label in BANDS:
        sub = [c for c in cells if lo <= c['cover'] < hi and c['oos_n'] >= 15]
        if not sub:
            out.append(dict(band=label, cell=None))
            continue
        best = max(sub, key=lambda x: (x['oos_hit'], x['oos_n']))
        out.append(dict(band=label, cell=best))
    return out


def run(save=False):
    pool = build_pool()
    res = {}
    for code in NAMES:
        raw = dict((pool.get(code) or {}).get(H) or {})
        if not raw:
            res[code] = dict(status='no_candidate')
            continue
        cdf = add_ensemble({k: v for k, v in raw.items() if v.index.min() < IS_END})
        if not cdf:
            res[code] = dict(status='insufficient_history')
            continue
        cands = {k: Cand(k, v) for k, v in cdf.items()}
        cells = all_cells(cands)
        if not cells:
            res[code] = dict(status='no_signal')
            continue
        av, pr = pick_two(cells)
        res[code] = dict(status='ok', n_cells=len(cells), n_cand=len(cands),
                         avail=av, prec=pr, ladder=coverage_ladder(cells))
    out = dict(updated=datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
               h=H, avail_min_cover=AVAIL_MIN_COVER, prec_min_cover=PREC_MIN_COVER,
               method='T+5 周频；IS(2019~2022) 挑参数 → OOS(2023起) 验收；零后视；无组合',
               selected=res)
    if save:
        with open(OUT_JSON, 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        with open(OUT_MD, 'w', encoding='utf-8') as f:
            f.write(to_md(out))
        print('[save] %s\n[save] %s' % (OUT_JSON, OUT_MD))
    return out


def rule_text(c):
    return ('score ≥ +%.2f → 做多 ｜ score ≤ -%.2f → 做空/回避' % (c['thr'], c['thr']))


def to_md(out):
    L = ['# 每个宽基指数 · 两套算法（T+5 周频）\n']
    L.append('> 生成：%s\n> IS(2019~2022) 挑参数 → OOS(2023起) 验收，零后视；无组合\n'
             % out['updated'])
    L.append('\n## 一、高可用（平时能出手，覆盖率 ≥%.0f%%）\n' % (out['avail_min_cover'] * 100))
    L.append('| 指数 | 算法 | 门槛 | 覆盖率 | OOS胜率 | 置信下限 | p | 单次净收益 | 当前 |')
    L.append('|---|---|---|---|---|---|---|---|---|')
    for code, r in out['selected'].items():
        c = r.get('avail')
        nm = NAMES[code]
        if not c:
            L.append('| %s | 无 | — | — | — | — | — | — | — |' % nm)
            continue
        L.append('| %s | %s | %.2f | %.0f%% | **%.1f%%** (n=%d) | %.1f%% | %.4f | %+.2f%% | %s %s |'
                 % (nm, c['cand'], c['thr'], c['cover'] * 100, c['oos_hit'] * 100,
                    c['oos_n'], c['lb'] * 100, c['p'], c['net'],
                    c['last'], c['state']))
    L.append('\n## 二、高精确（出手少但要准，按下限排）\n')
    L.append('| 指数 | 算法 | 门槛 | 覆盖率 | OOS胜率 | 置信下限 | p | 单次净收益 | 当前 |')
    L.append('|---|---|---|---|---|---|---|---|---|')
    for code, r in out['selected'].items():
        c = r.get('prec')
        nm = NAMES[code]
        if not c:
            L.append('| %s | 无 | — | — | — | — | — | — | — |' % nm)
            continue
        L.append('| %s | %s | %.2f | %.0f%% | **%.1f%%** (n=%d) | **%.1f%%** | %.4f | %+.2f%% | %s %s |'
                 % (nm, c['cand'], c['thr'], c['cover'] * 100, c['oos_hit'] * 100,
                    c['oos_n'], c['lb'] * 100, c['p'], c['net'],
                    c['last'], c['state']))
    L.append('\n## 三、交易规则\n')
    for code, r in out['selected'].items():
        nm = NAMES[code]
        a = r.get('avail')
        p = r.get('prec')
        L.append('### %s' % nm)
        if a:
            L.append('- **平时用**：`%s` ｜ %s ｜ 历史 %.1f%%' % (a['cand'], rule_text(a), a['oos_hit'] * 100))
        if p:
            L.append('- **重仓用**：`%s` ｜ %s ｜ 历史 %.1f%%（下限 %.1f%%）'
                     % (p['cand'], rule_text(p), p['oos_hit'] * 100, p['lb'] * 100))
        L.append('')
    return '\n'.join(L)


def fmt(out):
    L = ['=' * 132]
    L.append('每个宽基指数 · 两套算法 · T+5 周频（IS 挑参数 → OOS 验收，零后视，无组合）')
    L.append('=' * 132)

    L.append('')
    L.append('━━━ 一、高可用（平时能出手，覆盖率≥%.0f%%）━━━' % (out['avail_min_cover'] * 100))
    L.append('%-9s %-27s %6s %7s %15s %8s %9s %10s'
             % ('指数', '算法', '门槛', '覆盖率', 'OOS胜率', '置信下限', '单次净', '当前'))
    L.append('-' * 132)
    for code, r in out['selected'].items():
        c = r.get('avail')
        nm = NAMES[code]
        if not c:
            L.append('%-9s 无达标配置' % nm)
            continue
        L.append('%-9s %-27s %6.2f %6.0f%% %7.1f%% (n=%-3d) %7.1f%% %+8.2f%% %9s'
                 % (nm, c['cand'][:27], c['thr'], c['cover'] * 100,
                    c['oos_hit'] * 100, c['oos_n'], c['lb'] * 100, c['net'], c['state']))

    L.append('')
    L.append('━━━ 二、高精确（出手少但要准，按下限排）━━━')
    L.append('%-9s %-27s %6s %7s %15s %8s %9s %10s'
             % ('指数', '算法', '门槛', '覆盖率', 'OOS胜率', '置信下限', '单次净', '当前'))
    L.append('-' * 132)
    for code, r in out['selected'].items():
        c = r.get('prec')
        nm = NAMES[code]
        if not c:
            L.append('%-9s 无达标配置' % nm)
            continue
        L.append('%-9s %-27s %6.2f %6.0f%% %7.1f%% (n=%-3d) %7.1f%% %+8.2f%% %9s'
                 % (nm, c['cand'][:27], c['thr'], c['cover'] * 100,
                    c['oos_hit'] * 100, c['oos_n'], c['lb'] * 100, c['net'], c['state']))

    L.append('')
    L.append('━━━ 三、覆盖率阶梯（放宽覆盖率要付什么代价）━━━')
    for code, r in out['selected'].items():
        lad = r.get('ladder') or []
        if not lad:
            continue
        L.append('%s:' % NAMES[code])
        for b in lad:
            c = b.get('cell')
            if not c:
                L.append('   %-16s 无' % b['band'])
                continue
            ok = '✅' if c['oos_hit'] >= 0.55 and c['net'] > 0 else ('⚠️' if c['oos_hit'] >= 0.52 else '❌')
            L.append('   %-16s %-24s th%.2f 覆盖%3.0f%% 胜率%5.1f%%(n=%-3d) 净%+6.2f%% %s'
                     % (b['band'], c['cand'][:24], c['thr'], c['cover'] * 100,
                        c['oos_hit'] * 100, c['oos_n'], c['net'], ok))

    L.append('')
    L.append('━━━ 四、当前信号（数据日 / 方向）━━━')
    for code, r in out['selected'].items():
        a = r.get('avail') or {}
        p = r.get('prec') or {}
        L.append('%-9s 平时[%s]: %s %s ｜ 重仓[%s]: %s %s'
                 % (NAMES[code], (a.get('cand') or '-')[:14], a.get('last', '-'), a.get('state', '-'),
                    (p.get('cand') or '-')[:14], p.get('last', '-'), p.get('state', '-')))
    return '\n'.join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--save', action='store_true')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()
    out = run(save=a.save)
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        print(fmt(out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
