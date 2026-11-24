#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""市场临界监测 —— 预警有效性检验：每个指标/阈值线在历史序列上到底能不能预警。

判据（本文件唯一维护，报告与卡片都引用同一结论，避免口径漂移）
--------------------------------------------------------------
在全部可得交易日上，对每个候选信号计算：
  · 触发占比 = 满足该条件的交易日比例；
  · 触发组 / 未触发组的「前瞻 20 个交易日全A（中证全指 000985）平均收益」及差值；
  · 触发组 / 未触发组的「前瞻 20 个交易日全A 最大回撤」及差值。
判定「能预警」需同时满足：① 触发占比落在 5%~95%（既会触发也会不触发，否则是条直线）；
② 触发组 20 日前瞻收益差 ≤ −2 个百分点，或 20 日前瞻回撤差 ≤ −2 个百分点。
不满足者视为「无区分度」→ 从图上移除；**阈值规则本身不改**，仍逐条列在报告与卡片的核对区。

信号口径与正式阈值规则一致（黄警 = C>25% 或 Gap<5 或 Δλ₁<−5 连续 2 天；
橙警 = C>30% 且 λ₁ 250 日分位>90%；红警 = C>35% 或 (Gap<3 且 Δλ₁<−10) 或 (C>30% 且 IPR>0.15)），
另加同一批指标的分位版本（高位/低位）作为「可预警形态」的候选。

用法：python3 cgap_warning_power.py [history.json]
"""
import os
import sys
import json

import numpy as np

MIN_ON, MIN_OFF = 5, 5        # 触发/未触发两组各自的最少样本天数
FWD = 20                      # 前瞻交易日数
ABS_D = 0.02                  # 2 个百分点
TRIG_LO, TRIG_HI = 0.05, 0.95
ROLL_WIN = 250


def _roll_pct(s, win=ROLL_WIN):
    out = []
    for i in range(len(s)):
        w = np.asarray(s[max(0, i - win + 1):i + 1], dtype=float)
        out.append(float((w <= s[i]).mean() * 100))
    return np.asarray(out, dtype=float)


def _signals(p):
    """返回 [(key, 图上/说明用标签, 掩码, 类型)]，口径与正式阈值规则一致。"""
    C, G, D, IPR, Q = p['C'], p['Gap'], p['d_lambda1'], p['IPR'], p['q250']
    Cq, Gq, IPRq = p['Cq'], p['Gq'], p['IPRq']
    return [
        ('C>35%', 'C(t) > 35%（红警绝对线）', C > 35, '绝对阈值线'),
        ('C>30%', 'C(t) > 30%（橙警绝对线）', C > 30, '绝对阈值线'),
        ('C>25%', 'C(t) > 25%（黄警绝对线）', C > 25, '绝对阈值线'),
        ('Gap<5', 'Gap < 5（切换预警线）', G < 5, '绝对阈值线'),
        ('Gap<3', 'Gap < 3（混战线）', G < 3, '绝对阈值线'),
        ('dL1<-5', 'Δλ₁ < −5（一致性骤降线）', D < -5, '绝对阈值线'),
        ('IPR>0.15', 'IPR > 0.15（龙头拥挤线）', IPR > 0.15, '绝对阈值线'),
        ('q250>90', 'λ₁ 250 日滚动分位 > 90%', Q > 90, '分位信号'),
        ('q250<10', 'λ₁ 250 日滚动分位 < 10%', Q < 10, '分位信号'),
        ('Cq>90', 'C(t) 250 日滚动分位 > 90%', Cq > 90, '分位信号'),
        ('Gq<10', 'Gap 250 日滚动分位 < 10%', Gq < 10, '分位信号'),
        ('dL1<0', 'Δλ₁ < 0（一致性转负）', D < 0, '方向信号'),
        ('IPRq>90', 'IPR 250 日滚动分位 > 90%', IPRq > 90, '分位信号'),
    ]


def warning_power(hist):
    """对历史序列（按日期升序的 dict 列表）做区分度检验，返回结论字典。"""
    n = len(hist)
    if n < FWD + 40:
        return {'ok': False, 'note': f'历史样本不足（{n} 个交易日），无法做区分度检验'}

    p = {}
    for k in ('C', 'Gap', 'd_lambda1', 'IPR', 'q250'):
        p[k] = np.asarray([np.nan if r.get(k) is None else float(r[k]) for r in hist], dtype=float)
    p['d_lambda1'] = np.nan_to_num(p['d_lambda1'], nan=0.0)
    p['Cq'], p['Gq'], p['IPRq'] = _roll_pct(p['C']), _roll_pct(p['Gap']), _roll_pct(p['IPR'])
    idx = np.asarray([np.nan if r.get('index_close') is None else float(r['index_close'])
                      for r in hist], dtype=float)

    fwd = np.full(n, np.nan)
    dd = np.full(n, np.nan)
    for i in range(n - FWD):
        w = idx[i:i + FWD + 1]
        if np.isnan(w).any():
            continue
        fwd[i] = w[-1] / w[0] - 1
        dd[i] = w.min() / w[0] - 1
    valid = ~np.isnan(fwd)

    rows = []
    for key, label, mask, kind in _signals(p):
        on, off = mask & valid, (~mask) & valid
        row = {'key': key, 'label': label, 'kind': kind,
               'trigger_pct': round(float(mask.mean() * 100), 1),
               'n_on': int(on.sum()), 'n_off': int(off.sum()),
               'fwd20_on': None, 'fwd20_off': None, 'd_fwd20': None,
               'dd20_on': None, 'dd20_off': None, 'd_dd20': None}
        if row['n_on'] >= MIN_ON and row['n_off'] >= MIN_OFF:
            row['fwd20_on'] = round(float(np.nanmean(fwd[on])), 4)
            row['fwd20_off'] = round(float(np.nanmean(fwd[off])), 4)
            row['d_fwd20'] = round(row['fwd20_on'] - row['fwd20_off'], 4)
            row['dd20_on'] = round(float(np.nanmean(dd[on])), 4)
            row['dd20_off'] = round(float(np.nanmean(dd[off])), 4)
            row['d_dd20'] = round(row['dd20_on'] - row['dd20_off'], 4)

        ratio_ok = TRIG_LO <= row['trigger_pct'] / 100.0 <= TRIG_HI
        disc_ok = (row['d_fwd20'] is not None
                   and (row['d_fwd20'] <= -ABS_D or row['d_dd20'] <= -ABS_D))
        row['keep'] = bool(ratio_ok and disc_ok)
        if row['keep']:
            row['reason'] = (f"触发占比 {row['trigger_pct']:.1f}%，20 日前瞻收益差 "
                             f"{row['d_fwd20'] * 100:+.2f}pp / 回撤差 {row['d_dd20'] * 100:+.2f}pp → 有区分度")
        elif not ratio_ok:
            row['reason'] = (f"触发占比 {row['trigger_pct']:.1f}% 超出 5%~95%"
                             f"（{row['n_on']}/{n} 天）→ 近乎直线/恒定触发，无区分度")
        elif row['d_fwd20'] is None:
            row['reason'] = f"样本不足（触发 {row['n_on']} 天 / 未触发 {row['n_off']} 天）"
        else:
            row['reason'] = (f"区分度不足：20 日前瞻收益差 {row['d_fwd20'] * 100:+.2f}pp / "
                             f"回撤差 {row['d_dd20'] * 100:+.2f}pp（未达 −2pp 门槛）")
        rows.append(row)

    return {
        'ok': True,
        'criterion': ('触发占比需落在 5%~95%，且触发组 20 日前瞻全A 收益差 ≤ −2pp '
                      '或回撤差 ≤ −2pp，方视为「能预警」；不达标者从图上移除（阈值规则不改）'),
        'sample': {'days': n, 'start': hist[0]['date'], 'end': hist[-1]['date'],
                   'fwd': FWD, 'valid_fwd': int(valid.sum())},
        'rows': rows,
        'kept': [{'key': r['key'], 'label': r['label'], 'trigger_pct': r['trigger_pct'],
                  'd_fwd20': r['d_fwd20'], 'd_dd20': r['d_dd20'], 'reason': r['reason']}
                 for r in rows if r['keep']],
        'removed': [{'key': r['key'], 'label': r['label'], 'trigger_pct': r['trigger_pct'],
                     'n_on': r['n_on'], 'reason': r['reason']} for r in rows if not r['keep']],
    }


if __name__ == '__main__':
    default = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'rmt_cgap_history.json')
    path = sys.argv[1] if len(sys.argv) > 1 else default
    hist = json.load(open(path, encoding='utf-8'))
    res = warning_power(hist)
    if not res.get('ok'):
        print('[不可判]', res.get('note')); sys.exit(0)
    s = res['sample']
    print(f"样本 {s['days']} 个交易日（{s['start']} → {s['end']}），"
          f"可用于前瞻统计 {s['valid_fwd']} 天（前瞻 {s['fwd']} 日）")
    print('判据：', res['criterion'])
    print(f"\n{'信号':<34}{'触发占比':>9}{'触发/未触发':>12}{'20日前瞻收益差':>14}{'20日回撤差':>11}  结论")
    for r in res['rows']:
        d1 = 'n/a' if r['d_fwd20'] is None else f"{r['d_fwd20'] * 100:+.2f}pp"
        d2 = 'n/a' if r['d_dd20'] is None else f"{r['d_dd20'] * 100:+.2f}pp"
        print(f"{r['label']:<34}{r['trigger_pct']:>8.1f}%{r['n_on']:>6}/{r['n_off']:<5}{d1:>14}{d2:>11}"
              f"  {'保留' if r['keep'] else '移除'}")
    print('\n保留：', [x['key'] for x in res['kept']] or '无')
    print('移除：', [x['key'] for x in res['removed']] or '无')