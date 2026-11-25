import pandas as pd
import numpy as np
import pywt
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as ticker
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

# Use only ASCII/English labels — avoid all CJK
plt.rcParams['font.family'] = 'DejaVu Sans'
plt.rcParams['axes.unicode_minus'] = True

print("Step 1: Load and filter recent 1-year data...")
df = pd.read_csv("/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0/510300_data.csv", encoding='utf-8-sig')
df['date'] = pd.to_datetime(df['日期'])
df = df.sort_values('date').reset_index(drop=True)

# Filter last ~250 trading days (approx 1 year)
cutoff = pd.to_datetime('2025-09-17')
mask = df['date'] >= cutoff
df = df[mask].reset_index(drop=True)

volume = df['成交额'].values
close = df['收盘'].values
dates = df['date'].values

print(f"Filtered to {len(df)} rows: {dates[0]} to {dates[-1]}")

# Wavelet decomposition
wavelet = 'sym4'
level = 3

pad_len = (2**level) - (len(volume) % (2**level))
if pad_len == 2**level:
    pad_len = 0

if pad_len > 0:
    volume_padded = np.pad(volume, (0, pad_len), mode='symmetric')
else:
    volume_padded = volume

coeffs = pywt.wavedec(volume_padded, wavelet, level=level)

coeffs_long = [coeffs[0]] + [np.zeros_like(c) for c in coeffs[1:]]
coeffs_medium = [np.zeros_like(coeffs[0])] + [np.zeros_like(c) for c in coeffs[1:]]
coeffs_short = [np.zeros_like(coeffs[0])] + [np.zeros_like(c) for c in coeffs[1:]]

coeffs_medium[1] = coeffs[1]
coeffs_medium[2] = coeffs[2]
coeffs_short[3] = coeffs[3]

volume_long = pywt.waverec(coeffs_long, wavelet)[:len(volume)]
volume_medium = pywt.waverec(coeffs_medium, wavelet)[:len(volume)]
volume_short = pywt.waverec(coeffs_short, wavelet)[:len(volume)]

print(f"Decomposition done: long range [{volume_long.min():.0f}, {volume_long.max():.0f}]")

# Create single combined chart
fig, axes = plt.subplots(2, 1, figsize=(16, 11), sharex=True,
                         gridspec_kw={'height_ratios': [1, 2]})

# --- Top panel: Price ---
ax1 = axes[0]
ax1.plot(dates, close, color='black', linewidth=1.2)
ax1.set_ylabel('Close Price (CNY)', fontsize=12)
ax1.grid(True, alpha=0.3)
ax1.set_title('CSI 300 ETF (510300) — Volume Wavelet Decomposition (Last ~1 Year)', 
              fontsize=14, fontweight='bold', pad=10)

# --- Bottom panel: Combined volume components ---
ax2 = axes[1]

# Fill between for long-term (blue)
ax2.fill_between(dates, volume_long, alpha=0.25, color='blue')
ax2.plot(dates, volume_long, color='blue', linewidth=2.0, label='Long-term (cA3)')

# Fill between for medium-term (green)
ax2.fill_between(dates, volume_medium, alpha=0.3, color='green')
ax2.plot(dates, volume_medium, color='green', linewidth=1.5, label='Medium-term (cD3+cD2)')

# Short-term (red) — offset to positive for visibility
short_offset = volume_short + np.mean(volume_short) * 0.3
ax2.fill_between(dates, short_offset, alpha=0.15, color='red')
ax2.plot(dates, short_offset, color='red', linewidth=0.8, label='Short-term (cD1)')

# Zero line for medium reference
ax2.axhline(y=0, color='gray', linestyle='--', linewidth=0.7, alpha=0.5)

# Y-axis formatter (billions)
ax2.yaxis.set_major_formatter(ticker.FuncFormatter(lambda x, p: f'{x/1e9:.1f}B'))
ax2.set_ylabel('Volume (Billion CNY)', fontsize=12)
ax2.set_xlabel('Date', fontsize=12)
ax2.grid(True, alpha=0.3)

# Legend
ax2.legend(loc='upper left', fontsize=11, framealpha=0.9)

# X-axis: month ticks
for ax in axes:
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    ax.tick_params(axis='x', rotation=45)

# Color-coded annotations
ax2.annotate('Long-term trend', xy=(0.02, 0.95), xycoords='axes fraction',
             fontsize=11, color='blue', fontweight='bold',
             verticalalignment='top')
ax2.annotate('Medium-term wave', xy=(0.02, 0.88), xycoords='axes fraction',
             fontsize=11, color='green', fontweight='bold',
             verticalalignment='top')
ax2.annotate('Short-term pulse', xy=(0.02, 0.81), xycoords='axes fraction',
             fontsize=11, color='red', fontweight='bold',
             verticalalignment='top')

plt.tight_layout()
out_path = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0/v2_combined_wavelet.png'
plt.savefig(out_path, dpi=180, bbox_inches='tight')
plt.close()

print(f"Saved combined chart: {out_path}")

# Also save a zoomed version with just the volume components (no price)
fig, ax = plt.subplots(1, 1, figsize=(16, 8))
ax.fill_between(dates, volume_long, alpha=0.25, color='blue')
ax.plot(dates, volume_long, color='blue', linewidth=2.0, label='Long-term (cA3)')
ax.fill_between(dates, volume_medium, alpha=0.3, color='green')
ax.plot(dates, volume_medium, color='green', linewidth=1.5, label='Medium-term (cD3+cD2)')
ax.fill_between(dates, volume_short + np.mean(volume_short) * 0.3, alpha=0.15, color='red')
ax.plot(dates, volume_short + np.mean(volume_short) * 0.3, color='red', linewidth=0.8, label='Short-term (cD1)')
ax.axhline(y=0, color='gray', linestyle='--', linewidth=0.7, alpha=0.5)
ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda x, p: f'{x/1e9:.1f}B'))
ax.set_ylabel('Volume (Billion CNY)', fontsize=12)
ax.set_xlabel('Date', fontsize=12)
ax.set_title('Volume Wavelet Decomposition — Long/Medium/Short Frequency Bands', 
             fontsize=14, fontweight='bold', pad=10)
ax.legend(loc='upper left', fontsize=11, framealpha=0.9)
ax.grid(True, alpha=0.3)
ax.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
ax.tick_params(axis='x', rotation=45)

plt.tight_layout()
out_path2 = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0/v2_volume_only.png'
plt.savefig(out_path2, dpi=180, bbox_inches='tight')
plt.close()

print(f"Saved volume-only chart: {out_path2}")
print("Done!")
