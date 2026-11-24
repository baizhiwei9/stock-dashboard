#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读分析：基于重算的 RMT 序列，检验候选预警信号的前瞻收益区分度。
不修改主脚本、不推送。"""
import os, sys, time, warnings, json
import numpy as np, pandas as pd
from datetime import datetime

warnings.filterwarnings('ignore')
WORK = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WORK)
import rmt_daily_monitor as m

# ---- 拉数据 ----
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
mkt = (1 + ret_df.mean(axis=1)).cumprod()   # 等权市场代理

WINDOW = 60
def rmt_at(i):
    if i < WINDOW: return None
    w = ret_df.iloc[i-WINDOW+1:i+1]
    C = np.nan_to_num(w.corr().values, nan=0.0)
    np.fill_diagonal(C, 1.0); C=(C+C.T)/2
    eigs = np.maximum(np.sort(np.linalg.eigvalsh(C))[::-1], 1e-12)
    gap_1 = float((eigs[0]-eigs[1])/eigs[0])
    p=eigs/eigs.sum(); mask=p>1e-12
    entropy=float(-np.sum(p[mask]*np.log(p[mask])))
    snr=float(eigs[0]/(np.mean(eigs[1:])+1e-12))
    delta_s=0.0
    if i-WINDOW+1-5>=0:
        pw=ret_df.iloc[i-WINDOW+1-5:i+1-5]
        pC=np.nan_to_num(pw.corr().values,nan=0.0); np.fill_diagonal(pC,1.0); pC=(pC+pC.T)/2
        pe=np.maximum(np.sort(np.linalg.eigvalsh(pC))[::-1],1e-12)
        pp=pe/pe.sum(); pm=pp>1e-12
        delta_s=entropy-float(-np.sum(pp[pm]*np.log(pp[pm])))
    return dict(gap_1=gap_1,entropy=entropy,delta_s=delta_s,snr=snr)

rows=[]
for i in range(WINDOW,len(ret_df)):
    r=rmt_at(i)
    if r: rows.append((ret_df.index[i], r['gap_1'], r['entropy'], r['delta_s'], r['snr']))
H=pd.DataFrame(rows,columns=['date','gap_1','entropy','delta_s','snr']).set_index('date')
H['mkt']=mkt.reindex(H.index)
for k in (5,10):
    H[f'fwd{k}']=H['mkt'].shift(-k)/H['mkt']-1
H.to_pickle(os.path.join(WORK,'_hist_sig.pkl'))
print('saved _hist_sig.pkl n=',len(H),H.index[0].date(),'~',H.index[-1].date())

print("\n=== 2026-only 分位数 ===")
y=H.loc['2026-01-01':]
for c in ['gap_1','entropy','delta_s','snr']:
    print(f"{c:8s} min={y[c].min():.4f} p05={y[c].quantile(.05):.4f} p10={y[c].quantile(.10):.4f} p25={y[c].quantile(.25):.4f} p50={y[c].median():.4f} p75={y[c].quantile(.75):.4f} p90={y[c].quantile(.90):.4f} max={y[c].max():.4f}")

def ev(name, mask, note=''):
    d=H[mask]
    d5=d['fwd5'].dropna(); d10=d['fwd10'].dropna()
    print(f"[{name:28s}] n={len(d):4d} fwd5均值={d5.mean()*100:+6.2f}% 胜率={ (d5>0).mean()*100:5.1f}% | fwd10均值={d10.mean()*100:+6.2f}% 胜率={(d10>0).mean()*100:5.1f}%  {note}")

print("\n=== 候选信号 前瞻收益 (全样本) ===")
print("基准: 全样本 fwd5均值={:.2f}% fwd10均值={:.2f}%".format(H['fwd5'].mean()*100,H['fwd10'].mean()*100))
print("--- 风险侧 (希望前瞻为负) ---")
ev('ΔS>=0.03 (现有危机预警)', H['delta_s']>=0.03)
ev('ΔS>=0.05', H['delta_s']>=0.05)
ev('ΔS>=0.06', H['delta_s']>=0.06)
ev('ΔS>=0.08', H['delta_s']>=0.08)
ev('熵>=1.60', H['entropy']>=1.60)
ev('熵>=1.58', H['entropy']>=1.58)
ev('SNR>=21.5', H['snr']>=21.5)
ev('SNR>=25', H['snr']>=25)
ev('gap1<=0.50', H['gap_1']<=0.50)
print("--- 机会侧 (希望前瞻为正) ---")
ev('ΔS<=-0.05', H['delta_s']<=-0.05)
ev('ΔS<=-0.06', H['delta_s']<=-0.06)
ev('ΔS<=-0.08', H['delta_s']<=-0.08)
ev('熵<=1.45', H['entropy']<=1.45)
ev('熵<=1.43', H['entropy']<=1.43)
ev('熵<=1.40', H['entropy']<=1.40)
ev('SNR<=15', H['snr']<=15)

print("\n=== 2026-only 各信号命中日期 ===")
yidx=H.loc['2026-01-01':].index
def hitdays(name, mask):
    d=H[mask & (H.index>=pd.Timestamp('2026-01-01'))]
    if len(d)==0: print(f"[{name}] 无"); return
    print(f"[{name:26s}] " + ', '.join(x.strftime('%m-%d') for x in d.index))
hitdays('ΔS>=0.05', H['delta_s']>=0.05)
hitdays('ΔS>=0.06', H['delta_s']>=0.06)
hitdays('ΔS<=-0.05', H['delta_s']<=-0.05)
hitdays('ΔS<=-0.06', H['delta_s']<=-0.06)
hitdays('熵<=1.45', H['entropy']<=1.45)
hitdays('熵<=1.43', H['entropy']<=1.43)
hitdays('SNR>=21.0', H['snr']>=21.0)
hitdays('熵>=1.55', H['entropy']>=1.55)