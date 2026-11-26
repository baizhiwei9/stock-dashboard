#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""双模式胜率表 · T+5 周频（宽基指数）

大佬 2026-10-19 15:35 需求：
  「两种。一种是高确定的条件下给我一个胜率表；一种是普通情况下、平时能预测的胜率。
    这种应该是 55% 以上。就有两种算法，一种是高确定的条件下，一种是正常条件下。
    高确定条件下胜率我需要准、准确度高的；普通条件下我也需要准确率高的。
    说一个宽基指数预测周频的，T+5，它应该有。两种算法。」

→ 两个模式的定义
  **普通模式（Normal）**：门槛低，大部分时间都能出信号（覆盖率高），
      要求胜率稳定在 55% 以上。目的是「平时每天/每周都有方向可看」。
  **高确定模式（High-Conf）**：门槛高，出手少（覆盖率低），
      但每次胜率要高、且置信下限要硬。目的是「没把握就空仓，出手就要对」。

两模式都是 **T+5 周频**，都用 **IS 挑参数 → OOS 验收**，零后视。

产物：
  .cache/wide_ens/two_mode_table.json
  .cache/wide_ens/two_mode_table.md

用法：
  python3 bin/wide_ens/two_mode_table.py
  python3 bin/wide_ens/two_mode_table.py --save
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
                          _binom_p, IS_END, OOS_START, HIST_START, MIN_N_SEL)

CACHE = os.path.join(WS, '.cache/wide_ens')
OUT_JSON = os.path.join(CACHE, 'two_mode_table.json')
OUT_MD = os.path.join(CACHE, 'two_mode_table.md')

H = 5                      # 只做 T+5 周频
NORMAL_BAR = 0.55          # 普通模式：胜率 ≥55%
CONF_BAR = 0.55            # 高确定模式：置信下限 ≥55%
NORMAL_MIN_COVER = 0.55    # 普通模式覆盖率下限（至少 55% 的时间能出信号）
CONF_MAX_COVER = 0.35      # 高确定模式覆盖率上限（越少越精）
MIN_N_OOS = 25


def wilson_lb(w, n, z=1.645):
    """Wilson 90% 单侧置信下限"""
    if n <= 0:
        return 0.0
    p = w / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return float(max(0.0, (c - m) / d))


def cells_for(cands):
    """枚举所有 (候选, 阈值)，算 IS / OOS 双段成绩 + 覆盖率"""
    out = []
    dates = sorted(set().union(*[set(c.dates) for c in cands.values()]))
    cal = np.array(dates)
    oos_days = sum(1 for d in dates if d >= OOS_START)
    for name, c in cands.items():
        d = pd.Series(c.dir_ret, index=c.dates)
        ab = pd.Series(c.abs, index=c.dates)
        for th in TH_GRID:
            mask = (ab >= th).values
            trig_all = int(mask.sum())
            if trig_all == 0:
                continue
            # IS 段
            mi = mask & (d.index >= HIST_START) & (d.index <= IS_END)
            si = d[mi]
            if len(si) < MIN_N_SEL:
                continue
            iw = int((si > 0).sum())
            ih = iw / len(si)
            # OOS 段
            mo = mask & (d.index >= OOS_START)
            so = d[mo]
            on = len(so)
            if on < 10:
                continue
            ow = int((so > 0).sum())
            oh = ow / on
            cover = on / max(oos_days, 1)
            out.append(dict(
                cand=name, thr=th,
                is_n=len(si), is_hit=round(ih, 4),
                oos_n=on, oos_hit=round(oh, 4),
                oos_p=round(_binom_p(ow, on), 6),
                oos_raw=round(float(so.mean()), 3),
                lb=round(wilson_lb(ow, on), 4),
                cover=round(cover, 4),
                n_all=trig_all,
                last=str(c.dates[-1]),
                score_last=round(float(c.score[-1]), 4),
            ))
    return out


def pick_modes(cells):
    """挑出 普通模式 与 高确定模式 各自的最优

    ⚠️ 严格口径：达不到 55% 就**不录用**，绝不用兜底把 53% 的配置推上台。
       大佬原话「这种应该是 55% 以上」——拿 53% 冒充普通模式等于骗人。
       没达标时返回 None，由调用方单独标注「未达标」并给出参考值。
    """
    # 普通模式：覆盖率高（≥55%）+ OOS≥55% + p 显著 + 收益>0，按 (胜率, n) 排
    norm = [c for c in cells
            if c['cover'] >= NORMAL_MIN_COVER and c['oos_hit'] >= NORMAL_BAR
            and c['oos_p'] <= 0.10 and c['oos_raw'] > 0]
    norm.sort(key=lambda x: (x['oos_hit'], x['oos_n']), reverse=True)
    # 高确定模式：覆盖率低（≤35%）+ 置信下限≥55% + p 显著 + 收益>0，按下限排
    conf = [c for c in cells
            if c['cover'] <= CONF_MAX_COVER and c['lb'] >= CONF_BAR
            and c['oos_p'] <= 0.10 and c['oos_raw'] > 0]
    conf.sort(key=lambda x: (x['lb'], x['oos_hit']), reverse=True)
    return (norm[0] if norm else None), (conf[0] if conf else None)


def signal_now(cands, cell):
    """给定期望配置，读它最新一日的信号状态"""
    if not cell:
        return None
    c = cands.get(cell['cand'])
    if c is None:
        return None
    sc = float(c.score[-1])
    trig = abs(sc) >= cell['thr']
    return dict(date=str(c.dates[-1]), score=round(sc, 4), thr=cell['thr'],
                state=('看多' if sc > 0 else '看空') if trig else '未触发（观望）')


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
        cells = cells_for(cands)
        if not cells:
            res[code] = dict(status='no_signal')
            continue
        normal, conf = pick_modes(cells)
        # 没达标的：单独给参考值，明确标 not_meet，不许冒充达标
        nb = None
        if normal is None:
            alt = [c for c in cells if c['cover'] >= NORMAL_MIN_COVER and c['oos_raw'] > 0]
            alt.sort(key=lambda x: (x['oos_hit'], x['oos_n']), reverse=True)
            if alt:
                nb = dict(alt[0]); nb['not_meet'] = True
        cb = None
        if conf is None:
            alt = [c for c in cells if c['cover'] <= CONF_MAX_COVER and c['oos_raw'] > 0]
            alt.sort(key=lambda x: (x['lb'], x['oos_hit']), reverse=True)
            if alt:
                cb = dict(alt[0]); cb['not_meet'] = True
        res[code] = dict(
            status='ok' if (normal or conf) else 'none',
            n_cells=len(cells),
            n_cand=len(cands),
            normal=normal,
            conf=conf,
            normal_unmet=nb,
            conf_unmet=cb,
            normal_now=signal_now(cands, normal or nb),
            conf_now=signal_now(cands, conf or cb),
        )
    out = dict(updated=datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
               h=H, normal_bar=NORMAL_BAR, conf_bar=CONF_BAR,
               normal_min_cover=NORMAL_MIN_COVER, conf_max_cover=CONF_MAX_COVER,
               method='T+5 周频；IS(2019~2022)挑参数→OOS(2023起)验收；零后视',
               selected=res)
    if save:
        with open(OUT_JSON, 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        with open(OUT_MD, 'w', encoding='utf-8') as f:
            f.write(to_md(out))
        print('[save] %s\n[save] %s' % (OUT_JSON, OUT_MD))
    return out


def _row(name, cell, unmet=None):
    if not cell:
        if unmet:
            return ('| %s | ❌ 未达标（最好: %s th%.2f） | %.0f%% | %.1f%% (n=%d) | %.1f%% | %.4f | %+.2f%% |'
                    % (name, unmet['cand'], unmet['thr'], unmet['cover'] * 100,
                       unmet['oos_hit'] * 100, unmet['oos_n'], unmet['lb'] * 100,
                       unmet['oos_p'], unmet['oos_raw']))
        return '| %s | ❌ 无可用配置 | — | — | — | — | — |' % name
    return ('| %s | %s | th%.2f | %.0f%% | %.1f%% (n=%d) | %.1f%% | %.4f | %+.2f%% |'
            % (name, cell['cand'], cell['thr'], cell['cover'] * 100,
               cell['oos_hit'] * 100, cell['oos_n'], cell['lb'] * 100,
               cell['oos_p'], cell['oos_raw']))


def to_md(out):
    L = []
    L.append('# T+5 周频 · 双模式胜率表（5 个宽基）\n')
    L.append('> 生成：%s\n> 口径：IS(2019~2022) 挑参数 → OOS(2023 起) 验收，零后视\n'
             % out['updated'])
    L.append('\n## 一、普通模式（平时都能预测）\n')
    L.append('要求：覆盖率 ≥%.0f%%、OOS 胜率 ≥%.0f%%、p≤0.10、方向收益>0\n'
             % (out['normal_min_cover'] * 100, out['normal_bar'] * 100))
    L.append('| 指数 | 模型 | 门槛 | 覆盖率 | OOS胜率 | 置信下限 | p | 单次收益 |')
    L.append('|---|---|---|---|---|---|---|---|')
    for code, r in out['selected'].items():
        L.append(_row(NAMES[code], r.get('normal'), r.get('normal_unmet')))
    L.append('\n## 二、高确定模式（出手少但准）\n')
    L.append('要求：覆盖率 ≤%.0f%%、置信下限 ≥%.0f%%、p≤0.10、方向收益>0\n'
             % (out['conf_max_cover'] * 100, out['conf_bar'] * 100))
    L.append('| 指数 | 模型 | 门槛 | 覆盖率 | OOS胜率 | 置信下限 | p | 单次收益 |')
    L.append('|---|---|---|---|---|---|---|---|')
    for code, r in out['selected'].items():
        L.append(_row(NAMES[code], r.get('conf'), r.get('conf_unmet')))
    L.append('\n## 三、当前信号\n')
    L.append('| 指数 | 普通模式 | 高确定模式 |')
    L.append('|---|---|---|')
    for code, r in out['selected'].items():
        a = r.get('normal_now') or {}
        b = r.get('conf_now') or {}
        L.append('| %s | %s %s | %s %s |' % (
            NAMES[code],
            a.get('date', '-'), a.get('state', '-'),
            b.get('date', '-'), b.get('state', '-')))
    return '\n'.join(L)


def fmt(out):
    L = ['=' * 126]
    L.append('T+5 周频 · 双模式胜率表（IS 挑参数 → OOS 验收，零后视）')
    L.append('=' * 126)
    L.append('')
    L.append('【普通模式】覆盖率 ≥%.0f%%｜要求 OOS ≥%.0f%%'
             % (out['normal_min_cover'] * 100, out['normal_bar'] * 100))
    L.append('%-9s %-27s %6s %8s %14s %9s %10s'
             % ('指数', '模型', '门槛', '覆盖率', 'OOS胜率', '置信下限', '单次收益'))
    L.append('-' * 126)
    for code, r in out['selected'].items():
        c = r.get('normal')
        nm = NAMES[code]
        if not c:
            u = r.get('normal_unmet') or {}
            if u:
                L.append('%-9s ❌未达标 最好:%s th%.2f 覆盖%.0f%% 胜率%.1f%%(n=%d) 下限%.1f%%'
                         % (nm, u['cand'][:20], u['thr'], u['cover'] * 100,
                            u['oos_hit'] * 100, u['oos_n'], u['lb'] * 100))
            else:
                L.append('%-9s ❌无可用配置' % nm)
            continue
        L.append('%-9s %-27s %6.2f %7.0f%% %8.1f%% (n=%-3d) %8.1f%% %+9.2f%%'
                 % (nm, c['cand'][:27], c['thr'], c['cover'] * 100,
                    c['oos_hit'] * 100, c['oos_n'], c['lb'] * 100, c['oos_raw']))
    L.append('')
    L.append('【高确定模式】覆盖率 ≤%.0f%%｜要求置信下限 ≥%.0f%%'
             % (out['conf_max_cover'] * 100, out['conf_bar'] * 100))
    L.append('%-9s %-27s %6s %8s %14s %9s %10s'
             % ('指数', '模型', '门槛', '覆盖率', 'OOS胜率', '置信下限', '单次收益'))
    L.append('-' * 126)
    for code, r in out['selected'].items():
        c = r.get('conf')
        nm = NAMES[code]
        if not c:
            u = r.get('conf_unmet') or {}
            if u:
                L.append('%-9s ❌未达标 最好:%s th%.2f 覆盖%.0f%% 胜率%.1f%%(n=%d) 下限%.1f%%'
                         % (nm, u['cand'][:20], u['thr'], u['cover'] * 100,
                            u['oos_hit'] * 100, u['oos_n'], u['lb'] * 100))
            else:
                L.append('%-9s ❌无可用配置' % nm)
            continue
        L.append('%-9s %-27s %6.2f %7.0f%% %8.1f%% (n=%-3d) %8.1f%% %+9.2f%%'
                 % (nm, c['cand'][:27], c['thr'], c['cover'] * 100,
                    c['oos_hit'] * 100, c['oos_n'], c['lb'] * 100, c['oos_raw']))
    L.append('')
    L.append('【当前信号】')
    for code, r in out['selected'].items():
        a = r.get('normal_now') or {}
        b = r.get('conf_now') or {}
        L.append('%-9s 普通: %s %s ｜ 高确定: %s %s'
                 % (NAMES[code], a.get('date', '-'), a.get('state', '-'),
                    b.get('date', '-'), b.get('state', '-')))
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
