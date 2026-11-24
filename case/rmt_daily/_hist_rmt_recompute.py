#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读历史回溯：按 rmt_daily_monitor.compute_rmt 同一方法，逐交易日重算 RMT 指标。
不修改主脚本、不推送、不落盘报告。用于反推机会/风险预警阈值。"""
import os, sys, json, time, warnings
import numpy as np, pandas as pd
from datetime import datetime, timedelta

warnings.filterwarnings('ignore')
WORK = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WORK)
import rmt_daily_monitor as m

# 拉取与主脚本一致的18只ETF日线
all_bars = {}
for code, prefix in m.ETF_LIST.items():
    bars = m.fetch_kline(f'{prefix}{code}', days=800)
    for d, c in bars.items():
        all_bars.setdefault(d, {})[code] = c
    time.sleep(0.12)

dates_sorted = sorted(all_bars.keys())
etf_codes = list(m.ETF_LIST.keys())
df = pd.DataFrame({'date': pd.to_datetime(dates_sorted)})
for code in etf_codes:
    df[code] = [all_bars[d].get(code, np.nan) for d in dates_sorted]
etf_codes = [c for c in etf_codes if df[c].notna().sum() > 10]
df[etf_codes] = df[etf_codes].ffill().bfill()

closes = df[etf_codes].values.astype(float)
ret_arr = np.zeros_like(closes)
ret_arr[1:] = closes[1:] / closes[:-1] - 1
ret_arr = np.nan_to_num(ret_arr, nan=0.0)
ret_df = pd.DataFrame(ret_arr, columns=etf_codes, index=df['date'])

WINDOW = 60
def rmt_at(i):
    """i 为 ret_df 的整数位置（当日索引）"""
    if i < WINDOW: return None
    w = ret_df.iloc[i-WINDOW+1:i+1]
    C = np.nan_to_num(w.corr().values, nan=0.0)
    np.fill_diagonal(C, 1.0); C = (C + C.T)/2
    eigs = np.maximum(np.sort(np.linalg.eigvalsh(C))[::-1], 1e-12)
    gap_1 = float((eigs[0]-eigs[1])/eigs[0])
    p = eigs/eigs.sum(); mask = p > 1e-12
    entropy = float(-np.sum(p[mask]*np.log(p[mask])))
    snr = float(eigs[0]/(np.mean(eigs[1:])+1e-12))
    delta_s = 0.0
    if i-WINDOW+1-5 >= 0:
        pw = ret_df.iloc[i-WINDOW+1-5:i+1-5]
        pC = np.nan_to_num(pw.corr().values, nan=0.0)
        np.fill_diagonal(pC, 1.0); pC=(pC+pC.T)/2
        pe = np.maximum(np.sort(np.linalg.eigvalsh(pC))[::-1], 1e-12)
        pp = pe/pe.sum(); pm = pp > 1e-12
        pent = float(-np.sum(pp[pm]*np.log(pp[pm])))
        delta_s = entropy - pent
    return dict(gap_1=gap_1, entropy=entropy, delta_s=delta_s, snr=snr)

out = []
for i in range(WINDOW, len(ret_df)):
    r = rmt_at(i)
    if r is None: continue
    d = ret_df.index[i].strftime('%Y-%m-%d')
    out.append((d, r['gap_1'], r['entropy'], r['delta_s'], r['snr']))

# 只输出 2026-06-01 以后
print('date        gap1    entropy    dS       snr')
for d, g, e, ds, s in out:
    if d >= '2026-06-01':
        print(f'{d}  {g:7.4f}  {e:8.4f}  {ds:+8.4f}  {s:8.3f}')

json.dump([{'date':d,'gap_1':g,'entropy':e,'delta_s':ds,'snr':s} for d,g,e,ds,s in out],
          open(os.path.join(WORK,'_hist_rmt.json'),'w'), ensure_ascii=False, indent=1)
print('\nsaved _hist_rmt.json, n=', len(out), 'range', out[0][0], '~', out[-1][0])