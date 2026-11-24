import pandas as pd
import numpy as np
import pywt
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

# Minimal font config — no CJK
plt.rcParams['font.family'] = 'sans-serif'
plt.rcParams['font.sans-serif'] = ['DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

# Load data
df = pd.read_csv("/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0/510300_data.csv", encoding='utf-8-sig')
df['date'] = pd.to_datetime(df['日期'])
df = df.sort_values('date').reset_index(drop=True)

# Last ~1 year
cutoff = pd.to_datetime('2025-09-17')
df = df[df['date'] >= cutoff].reset_index(drop=True)

volume = df['成交额'].values
close = df['收盘'].values
dates = df['date'].values

print(f"Loaded {len(df)} rows: {pd.Timestamp(dates[0]).strftime('%Y-%m-%d')} to {pd.Timestamp(dates[-1]).strftime('%Y-%m-%d')}")

# Wavelet decomposition
coeffs = pywt.wavedec(volume, 'sym4', level=3)
cA = pywt.waverec([coeffs[0]] + [np.zeros_like(c) for c in coeffs[1:]], 'sym4')[:len(volume)]
cD32 = pywt.waverec([np.zeros_like(coeffs[0]), coeffs[1], coeffs[2], np.zeros_like(coeffs[3])], 'sym4')[:len(volume)]
cD1 = pywt.waverec([np.zeros_like(coeffs[0]), np.zeros_like(coeffs[1]), np.zeros_like(coeffs[2]), coeffs[3]], 'sym4')[:len(volume)]

# Convert to Billions directly — no formatter needed
v_long = cA / 1e9
v_med = cD32 / 1e9
v_short = cD1 / 1e9

fig, axes = plt.subplots(2, 1, figsize=(16, 10), sharex=True, gridspec_kw={'height_ratios': [1, 2]})

# Top: Price
ax1 = axes[0]
ax1.plot(dates, close, color='black', linewidth=1.2)
ax1.set_ylabel('Close Price (CNY)', fontsize=11)
ax1.grid(True, alpha=0.3)
ax1.set_title('CSI 300 ETF (510300) - Volume Wavelet Decomposition (Last ~1 Year)', fontsize=14, fontweight='bold')

# Bottom: Volume components (all in one panel)
ax2 = axes[1]
ax2.fill_between(dates, v_long, alpha=0.25, color='blue')
ax2.plot(dates, v_long, color='blue', linewidth=2.0, label='Long-term (cA3)')

ax2.fill_between(dates, v_med, alpha=0.3, color='green')
ax2.plot(dates, v_med, color='green', linewidth=1.5, label='Medium-term (cD3+cD2)')

# Shift short-term upward for visibility
short_shift = v_short + np.mean(v_long) * 0.15
ax2.fill_between(dates, short_shift, alpha=0.2, color='red')
ax2.plot(dates, short_shift, color='red', linewidth=0.8, label='Short-term (cD1)')

ax2.axhline(y=0, color='gray', linestyle='--', linewidth=0.7, alpha=0.5)
ax2.set_ylabel('Volume (Billion CNY)', fontsize=11)
ax2.set_xlabel('Date', fontsize=11)
ax2.grid(True, alpha=0.3)
ax2.legend(loc='upper left', fontsize=10, framealpha=0.9)

# X-axis
for ax in axes:
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    ax.tick_params(axis='x', rotation=45)

plt.tight_layout()
out_path = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0/v4_clean.png'
plt.savefig(out_path, dpi=180, bbox_inches='tight')
plt.close()

print(f"Saved: {out_path}")
