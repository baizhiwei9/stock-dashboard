#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读：把候选阈值信号与实际事件窗口对齐，输出命中与前瞻收益。
复用 _hist_sig.pkl（已含 RMT 指标 + 市场代理 + 前瞻收益）。"""
import os, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
WORK = os.path.dirname(os.path.abspath(__file__))
H = pd.read_pickle(os.path.join(WORK, '_hist_sig.pkl'))
H.index = pd.to_datetime(H.index)

# 事件窗口（基于本年度真实行情）
WINS = {
 '7月风险窗(暴跌前+暴跌)': ('2026-06-20','2026-07-17'),
 '10月末风险窗':          ('2026-10-15','2026-10-22'),
 '9月机会窗(反弹前+反弹)': ('2026-08-15','2026-09-15'),
}
def inwin(mask, a, b):
    d = H[mask & (H.index>=pd.Timestamp(a)) & (H.index<=pd.Timestamp(b))]
    return d

def show(name, mask):
    d_all = H[mask]
    d5=d_all['fwd5'].dropna(); d10=d_all['fwd10'].dropna()
    m5 = d5.mean()*100 if len(d5) else float('nan')
    w5 = (d5>0).mean()*100 if len(d5) else float('nan')
    m10= d10.mean()*100 if len(d10) else float('nan')
    w10=(d10>0).mean()*100 if len(d10) else float('nan')
    print(f"{name:22s} 全样本n={len(d_all):4d} fwd5={m5:+6.2f}%(胜{w5:4.1f}%) fwd10={m10:+6.2f}%(胜{w10:4.1f}%)")
    for wn,(a,b) in WINS.items():
        dd=inwin(mask,a,b)
        if len(dd):
            print(f"      └ {wn}: 命中{len(dd)}天  {', '.join(x.strftime('%m-%d') for x in dd.index)}")
        else:
            print(f"      └ {wn}: 未命中")

print("================ 风险侧候选 ================")
show('ΔS>=0.03(现有)', H['delta_s']>=0.03)
show('ΔS>=0.05', H['delta_s']>=0.05)
show('ΔS>=0.06', H['delta_s']>=0.06)
show('ΔS>=0.08', H['delta_s']>=0.08)
show('熵>=1.55', H['entropy']>=1.55)
show('熵>=1.60', H['entropy']>=1.60)
print("\n================ 机会侧候选 ================")
show('ΔS<=-0.05', H['delta_s']<=-0.05)
show('ΔS<=-0.06', H['delta_s']<=-0.06)
show('ΔS<=-0.08', H['delta_s']<=-0.08)
show('ΔS<=-0.10(现有)', H['delta_s']<=-0.10)
show('熵<=1.45', H['entropy']<=1.45)
show('熵<=1.44', H['entropy']<=1.44)
show('熵<=1.43', H['entropy']<=1.43)
show('SNR>=21.0', H['snr']>=21.0)
show('SNR>=21.3', H['snr']>=21.3)

print("\n== 2026年 熵 的月度最低 / SNR 月度最高 (定位机会) ==")
y=H.loc['2026-01-01':]
for mth, g in y.groupby(y.index.to_period('M')):
    lo = g['entropy'].idxmin(); hi=g['snr'].idxmax()
    print(f"{mth}  熵min={g['entropy'].min():.4f}({lo.date()})  SNRmax={g['snr'].max():.3f}({hi.date()})  ΔSmin={g['delta_s'].min():+.4f} ΔSmax={g['delta_s'].max():+.4f}")