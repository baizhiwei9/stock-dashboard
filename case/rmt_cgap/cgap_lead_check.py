#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""核对 2026-07 / 2026-10 两波下跌与 RMT 特征值、Gap 等指标的关系（只读分析，不改动日报口径）。

数据：rmt_cgap_history.json（逐日 λ1/λ2/λ3/C(t)/Gap/IPR/q250/d_lambda1/index_close=全A中证全指）
输出：两波下跌区间的数据事实、波动幅度、与全A 同期/次日的相关，供判定指标是否具备预警意义。
"""
import json
import numpy as np
import pandas as pd

H = 'rmt_cgap_history.json'
df = pd.DataFrame(json.load(open(H, encoding='utf-8')))
df['date'] = pd.to_datetime(df['date'])
df = df.sort_values('date').reset_index(drop=True)
df['ret'] = df['index_close'].pct_change()
df['ret_next'] = df['ret'].shift(-1)


def window(a, b):
    m = (df['date'] >= a) & (df['date'] <= b)
    return df[m].reset_index(drop=True)


def drawdown(sub):
    """区间内全A 从峰到谷的最大回撤段。"""
    c = sub['index_close'].values
    peak = int(np.argmax(np.maximum.accumulate(c)))
    peak = int(np.argmax(c[:np.argmin(c) + 1])) if np.argmin(c) > 0 else 0
    tr = int(np.argmin(c[peak:]) + peak)
    return peak, tr


def stats(sub, peak, tr):
    seg = sub.iloc[peak:tr + 1]
    dd = sub['index_close'].iloc[tr] / sub['index_close'].iloc[peak] - 1
    out = {'区间': f"{sub['date'].iloc[0].date()} ~ {sub['date'].iloc[-1].date()}",
           '峰→谷': f"{sub['date'].iloc[peak].date()} → {sub['date'].iloc[tr].date()}",
           '全A回撤': f'{dd * 100:+.2f}%'}
    for k, lab in [('lambda1', 'λ1'), ('lambda2', 'λ2'), ('lambda3', 'λ3'),
                   ('Gap', 'Gap'), ('C', 'C(t)%'), ('IPR', 'IPR'), ('q250', 'q250%'), ('d_lambda1', 'Δλ1')]:
        v = seg[k].values.astype(float)
        sd = seg[k].diff().dropna()
        rel_range = (v.max() - v.min()) / abs(v.mean()) * 100 if v.mean() else float('nan')
        out[lab] = (f"min={v.min():.4g} max={v.max():.4g} 区间幅度={v.max() - v.min():.4g}"
                    f"（占均值 {rel_range:.2f}%） 日均|Δ|={np.abs(sd).mean():.4g}")
    return out, seg


print('=' * 100)
print('样本:', df.shape[0], '交易日', df['date'].iloc[0].date(), '~', df['date'].iloc[-1].date(),
      '| 全A(中证全指) 非空', int(df['index_close'].notna().sum()))
print('全A 全样本回撤最大的 10 个交易日:')
big = df.nlargest(10, 'ret')  # placeholder replaced below
worst = df.nsmallest(10, 'ret')[['date', 'index_close', 'ret', 'lambda1', 'lambda2', 'Gap', 'C', 'd_lambda1', 'q250']]
print(worst.to_string(index=False))

for tag, (a, b) in {'2026-07': ('2026-06-20', '2026-08-10'), '2026-10': ('2026-09-20', '2026-11-03')}.items():
    sub = window(a, b)
    if sub.empty:
        print(f'\n[{tag}] 无数据'); continue
    peak, tr = drawdown(sub)
    out, seg = stats(sub, peak, tr)
    print('\n' + '=' * 100)
    print(f'[{tag}] 下跌区间核对')
    for k, v in out.items():
        print(f'  {k:8s} {v}')
    # 相关：同日 / 领先一日
    def corr(x, y):
        d = pd.concat([x, y], axis=1).dropna()
        return np.corrcoef(d.iloc[:, 0], d.iloc[:, 1])[0, 1] if len(d) > 3 else float('nan')
    print('  同期相关（全样本 / 该区间）：')
    for k in ['d_lambda1', 'Gap', 'C', 'IPR', 'q250', 'lambda1', 'lambda2', 'lambda3']:
        c_all = corr(df[k].diff().fillna(0), df['ret'])
        c_win = corr(seg[k].diff().fillna(0), seg['ret'])
        print(f'    Δ{k:9s} vs 全A同日收益: {c_all:+.3f} / {c_win:+.3f}')
    print('  领先一日（t 日指标 vs t+1 日全A收益）：')
    for k in ['d_lambda1', 'Gap', 'C', 'IPR', 'q250', 'lambda1', 'lambda2']:
        print(f'    Δ{k:9s} vs 次日收益: {corr(df[k].diff().fillna(0), df["ret_next"]):+.3f}')