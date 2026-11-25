#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检验各类指标对 2026-07 / 2026-10 两波下跌的提前预警能力（只读分析）。

信号池：RMT（gap_1/熵/ΔS）、CGAP（λ1/λ2/λ3、C(t)、Gap、IPR、q250、Δλ1）、全A自身趋势与波动。
判定：①全样本触发次数与次日/未来5日收益；②两波下跌峰前 15 个交易日内是否至少提前 1 日触发。
"""
import json, glob
import numpy as np
import pandas as pd

cg = pd.DataFrame(json.load(open('rmt_cgap_history.json', encoding='utf-8')))
cg['date'] = pd.to_datetime(cg['date'])
cg = cg.sort_values('date').reset_index(drop=True)

rm = pd.DataFrame(json.load(open('_hist_rmt.json', encoding='utf-8')))
rm['date'] = pd.to_datetime(rm['date'])
extra = []
for p in sorted(glob.glob('rmt_report_*.json')):
    try:
        d = json.load(open(p, encoding='utf-8'))
    except Exception:
        continue
    dt = p.replace('rmt_report_', '').replace('.json', '')
    extra.append({'date': pd.to_datetime(dt), 'gap_1': d.get('gap_1'), 'entropy': d.get('entropy'),
                  'delta_s': d.get('delta_s'), 'snr': d.get('snr')})
rm = pd.concat([rm, pd.DataFrame(extra)], ignore_index=True).drop_duplicates('date').sort_values('date')

m = cg.merge(rm, on='date', how='left')
m['ret'] = m['index_close'].pct_change()
m['ret5'] = m['index_close'].shift(-5) / m['index_close'] - 1
m['ret_next'] = m['ret'].shift(-1)
m['ma20'] = m['index_close'].rolling(20).mean()
m['ma20_slope'] = m['ma20'].diff(5)
m['mom5'] = m['index_close'] / m['index_close'].shift(5) - 1
m['mom20'] = m['index_close'] / m['index_close'].shift(20) - 1
m['vol20'] = m['ret'].rolling(20).std()
N = len(m)

SIG = {
    'RMT 红灯 ΔS≥+0.08': m['delta_s'] >= 0.08,
    'RMT 黄灯 ΔS≥+0.05': m['delta_s'] >= 0.05,
    'RMT 熵≥1.55(维度扩张)': m['entropy'] >= 1.55,
    'RMT 熵≤1.45(低维共振)': m['entropy'] <= 1.45,
    'RMT Gap1<0.62': m['gap_1'] < 0.62,
    'CGAP C(t)>30%': m['C'] > 30,
    'CGAP C(t)>35%': m['C'] > 35,
    'CGAP Gap<3': m['Gap'] < 3,
    'CGAP λ1 分位>90%': m['q250'] > 90,
    'CGAP Δλ1<-0.15': m['d_lambda1'] < -0.15,
    'CGAP λ3 上行(近5日>+3%)': m['lambda3'] / m['lambda3'].shift(5) - 1 > 0.03,
    '全A 跌破MA20': m['index_close'] < m['ma20'],
    '全A 跌破MA20且MA20走低': (m['index_close'] < m['ma20']) & (m['ma20_slope'] < 0),
    '全A 5日动量<-2%': m['mom5'] < -0.02,
    '全A 20日动量<-5%': m['mom20'] < -0.05,
    '全A 20日波动率>年化20%': m['vol20'] * np.sqrt(252) > 0.20,
}
base_neg = (m['ret_next'] < 0).mean()
base_r1 = m['ret_next'].mean()
base_r5 = m['ret5'].mean()

WAVES = {'2026-07波': ('2026-06-22', '2026-07-30'), '2026-10波': ('2026-09-22', '2026-10-30')}

print(f'样本 {N} 交易日 {m["date"].iloc[0].date()} ~ {m["date"].iloc[-1].date()}｜基准：次日跌概率 {base_neg:.1%}，'
      f'次日均值 {base_r1*100:+.3f}%，未来5日均值 {base_r5*100:+.2f}%')
print('=' * 118)
print(f'{"信号":26s} {"触发":>4s} {"次日跌概率":>8s} {"次日均值":>9s} {"未来5日":>8s} | 两波提前量（交易日）')
for name, s in SIG.items():
    s = s.fillna(False).values
    pn = np.nanmean(np.where(s, (m['ret_next'] < 0).values, np.nan))
    r1 = np.nanmean(np.where(s, m['ret_next'].values, np.nan))
    r5 = np.nanmean(np.where(s, m['ret5'].values, np.nan))
    lead = []
    for tag, (pk, tg) in WAVES.items():
        pki = m.index[m['date'] == pd.Timestamp(pk)]
        if len(pki) == 0:
            lead.append(f'{tag}:?'); continue
        pki = pki[0]
        lo = max(0, pki - 15)
        trig = [i for i in range(lo, pki + 1) if s[i]]
        if not trig:
            lead.append(f'{tag}:未提前')
        else:
            first, last = trig[0], trig[-1]
            lead.append(f'{tag}:首触{pki-first}日前/末触{pki-last}日前')
    print(f'{name:26s} {int(np.nansum(s)):4d} {pn*100:7.1f}% {r1*100:+8.3f}% {r5*100:+7.2f}% | ' + ' ；'.join(lead))

print('=' * 118)
print('两波下跌的逐日轨迹（峰前 5 日 → 谷后 3 日）：')
for tag, (pk, tg) in WAVES.items():
    pki = m.index[m['date'] == pd.Timestamp(pk)][0]
    tgi = m.index[m['date'] == pd.Timestamp(tg)][0]
    seg = m.iloc[max(0, pki - 5):tgi + 4]
    print(f'\n[{tag}] 峰 {pk} → 谷 {tg}')
    cols = ['date', 'index_close', 'ret', 'lambda1', 'lambda2', 'Gap', 'C', 'q250', 'delta_s', 'entropy', 'gap_1']
    show = seg[cols].copy()
    show['date'] = show['date'].dt.strftime('%m-%d')
    for c in ['ret']:
        show[c] = (show[c] * 100).round(2)
    for c in ['lambda1', 'lambda2', 'Gap', 'C', 'q250', 'delta_s']:
        show[c] = show[c].round(3)
    for c in ['entropy', 'gap_1']:
        show[c] = show[c].round(4)
    print(show.to_string(index=False))