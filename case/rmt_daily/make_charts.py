#!/usr/bin/env python3
"""RMT vs TDA 风控对比 - 图表生成"""
import os, sys, base64, io
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib import font_manager

WORK = os.path.dirname(os.path.abspath(__file__))
PYLIB = os.path.join(WORK, 'pylib')
if os.path.exists(PYLIB):
    sys.path.insert(0, PYLIB)

# 注册中文字体 (Droid Sans Fallback, 仅含CJK字形)
_CJK_FONT = '/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf'
if os.path.exists(_CJK_FONT):
    font_manager.fontManager.addfont(_CJK_FONT)
# 关键: font.family直接设为双字体列表, DejaVu处理ASCII/数字, Droid回退处理中文
plt.rcParams['font.family'] = ['DejaVu Sans', 'Droid Sans Fallback']
plt.rcParams['font.sans-serif'] = ['DejaVu Sans', 'Droid Sans Fallback']
plt.rcParams['axes.unicode_minus'] = False

df = pd.read_pickle(os.path.join(WORK, 'backtest_rmt_vs_tda.pkl'))

RMT_C, TDA_C, BENCH_C = '#d62728', '#1f77b4', '#7f7f7f'
RMT_L, TDA_L, BENCH_L = 'RMT风控', 'TDA风控', '基准(满仓HS300)'

os.makedirs(os.path.join(WORK, 'charts'), exist_ok=True)

# ===== 图1: 净值曲线 =====
fig, ax = plt.subplots(figsize=(12, 5.5))
ax.plot(df.index, df['rmt_cum'], label=RMT_L, color=RMT_C, lw=1.7)
ax.plot(df.index, df['tda_cum'], label=TDA_L, color=TDA_C, lw=1.7)
ax.plot(df.index, df['bench_cum'], label=BENCH_L, color=BENCH_C, lw=1.3, ls='--')
ax.axhline(1.0, color='black', lw=0.6, alpha=0.4)
ax.set_title('净值曲线对比 (2022-01 ~ 2026-08, 沪深300标的)', fontsize=14)
ax.set_ylabel('净值')
ax.legend(loc='upper left', fontsize=11)
ax.grid(alpha=0.3)
ax.xaxis.set_major_locator(mdates.YearLocator())
ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
fig.tight_layout()
fig.savefig(os.path.join(WORK, 'charts', 'equity_curve.png'), dpi=120)
plt.close(fig)

# ===== 图2: 回撤曲线 =====
fig, ax = plt.subplots(figsize=(12, 4.5))
for col, lab, c in [('rmt_cum_dd', RMT_L, RMT_C), ('tda_cum_dd', TDA_L, TDA_C), ('bench_cum_dd', BENCH_L, BENCH_C)]:
    ax.fill_between(df.index, df[col], 0, alpha=0.25, color=c)
    ax.plot(df.index, df[col], label=lab, color=c, lw=1.3)
ax.set_title('回撤曲线对比', fontsize=14)
ax.set_ylabel('回撤')
ax.legend(loc='lower left', fontsize=11)
ax.grid(alpha=0.3)
ax.xaxis.set_major_locator(mdates.YearLocator())
ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
fig.tight_layout()
fig.savefig(os.path.join(WORK, 'charts', 'drawdown_curve.png'), dpi=120)
plt.close(fig)

# ===== 图3: 仓位时间序列 =====
fig, ax = plt.subplots(figsize=(12, 4.5))
ax.plot(df.index, df['rmt_pos'] * 100, label=RMT_L, color=RMT_C, lw=1.2)
ax.plot(df.index, df['tda_pos'] * 100, label=TDA_L, color=TDA_C, lw=1.2)
ax.set_title('仓位控制对比 (T日信号, T+1生效)', fontsize=14)
ax.set_ylabel('仓位 (%)')
ax.set_ylim(0, 105)
ax.legend(loc='upper right', fontsize=11)
ax.grid(alpha=0.3)
ax.xaxis.set_major_locator(mdates.YearLocator())
ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
fig.tight_layout()
fig.savefig(os.path.join(WORK, 'charts', 'position_curve.png'), dpi=120)
plt.close(fig)

# ===== 图4: 年度收益对比 =====
years = sorted(set(df.index.year))
group = df.groupby(df.index.year)
rmt_yr = group['rmt_return'].apply(lambda x: (1 + x).prod() - 1)
tda_yr = group['tda_return'].apply(lambda x: (1 + x).prod() - 1)
bench_yr = group['bench_return'].apply(lambda x: (1 + x).prod() - 1)

fig, ax = plt.subplots(figsize=(12, 5))
x = np.arange(len(years))
w = 0.27
ax.bar(x - w, rmt_yr * 100, w, label=RMT_L, color=RMT_C)
ax.bar(x, tda_yr * 100, w, label=TDA_L, color=TDA_C)
ax.bar(x + w, bench_yr * 100, w, label=BENCH_L, color=BENCH_C)
ax.axhline(0, color='black', lw=0.7)
ax.set_xticks(x)
ax.set_xticklabels([str(y) for y in years])
ax.set_ylabel('收益 (%)')
ax.set_title('年度收益对比 (%)', fontsize=14)
ax.legend(loc='upper left', fontsize=11)
ax.grid(axis='y', alpha=0.3)
for i in x:
    for j, arr in enumerate([rmt_yr, tda_yr, bench_yr]):
        v = arr.iloc[i] * 100
        ax.text(i + (j - 1) * w, v + (0.6 if v >= 0 else -2.0), f'{v:.1f}',
                ha='center', va='bottom' if v >= 0 else 'top', fontsize=8)
fig.tight_layout()
fig.savefig(os.path.join(WORK, 'charts', 'yearly_returns.png'), dpi=120)
plt.close(fig)

# ===== 图5: 月度胜率/超额 =====
group_m = df.groupby([df.index.year, df.index.month])
rmt_m = group_m['rmt_return'].apply(lambda x: (1 + x).prod() - 1)
tda_m = group_m['tda_return'].apply(lambda x: (1 + x).prod() - 1)
bench_m = group_m['bench_return'].apply(lambda x: (1 + x).prod() - 1)
rmt_excess = rmt_m - bench_m
tda_excess = tda_m - bench_m

fig, ax = plt.subplots(figsize=(12, 5))
idx = np.arange(len(rmt_m))
ax.bar(idx - 0.19, rmt_excess.values * 100, 0.38, label='RMT超额', color=RMT_C, alpha=0.85)
ax.bar(idx + 0.19, tda_excess.values * 100, 0.38, label='TDA超额', color=TDA_C, alpha=0.85)
ax.axhline(0, color='black', lw=0.7)
labels = [f'{y}-{m:02d}' for y, m in rmt_m.index]
ax.set_xticks(idx)
ax.set_xticklabels(labels, rotation=90, fontsize=7)
ax.set_ylabel('月度超额收益 (%)')
ax.set_title('月度超额收益对比 (相对满仓HS300)', fontsize=14)
ax.legend(loc='upper left', fontsize=11)
ax.grid(axis='y', alpha=0.3)
fig.tight_layout()
fig.savefig(os.path.join(WORK, 'charts', 'monthly_excess.png'), dpi=120)
plt.close(fig)

print('图表已生成到 charts/')
print('  equity_curve.png, drawdown_curve.png, position_curve.png, yearly_returns.png, monthly_excess.png')