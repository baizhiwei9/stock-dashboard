import pandas as pd
import numpy as np
import pywt
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.font_manager import FontProperties
import warnings
warnings.filterwarnings('ignore')

# 加载中文字体
font_path = '/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf'
cn_font = FontProperties(fname=font_path)
cn_font_large = FontProperties(fname=font_path, size=16)
cn_font_medium = FontProperties(fname=font_path, size=12)
cn_font_small = FontProperties(fname=font_path, size=10)

print("Loading data...")
df = pd.read_csv("/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0/510300_data.csv", encoding='utf-8-sig')
df['date'] = pd.to_datetime(df['日期'])
df = df.sort_values('date').reset_index(drop=True)

volume = df['成交额'].values
close = df['收盘'].values
dates = df['date'].values

print("Wavelet decomposition...")
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

print("Generating plots with Chinese fonts...")

# Plot 1: Decomposition
fig, axes = plt.subplots(4, 1, figsize=(16, 14), sharex=True)
fig.suptitle('沪深300ETF (510300) 成交量小波分解 - sym4 三层多尺度分析 (2019-2026)', 
             fontproperties=cn_font_large, fontweight='bold', y=0.98)

# Price
ax1 = axes[0]
ax1.plot(dates, close, color='black', linewidth=1.2)
ax1.set_ylabel('价格 (元)', fontproperties=cn_font_medium)
ax1.grid(True, alpha=0.3)
title1 = ax1.set_title('沪深300ETF收盘价走势', fontproperties=cn_font_medium, fontweight='bold')

# Long-term
ax2 = axes[1]
ax2.fill_between(dates, volume_long, alpha=0.4, color='blue')
ax2.plot(dates, volume_long, color='blue', linewidth=1.5)
ax2.set_ylabel('成交额 (元)', fontproperties=cn_font_medium)
ax2.grid(True, alpha=0.3)
ax2.set_title('长周期成交量趋势 (低频基底)', fontproperties=cn_font_medium, fontweight='bold')

# Medium-term
ax3 = axes[2]
ax3.fill_between(dates, volume_medium, alpha=0.4, color='green')
ax3.plot(dates, volume_medium, color='green', linewidth=1.2)
ax3.axhline(y=0, color='black', linestyle='-', linewidth=0.5)
ax3.set_ylabel('成交额 (元)', fontproperties=cn_font_medium)
ax3.grid(True, alpha=0.3)
ax3.set_title('中周期成交量波段 (中频趋势资金)', fontproperties=cn_font_medium, fontweight='bold')

# Short-term
ax4 = axes[3]
ax4.fill_between(dates, volume_short, alpha=0.4, color='red')
ax4.plot(dates, volume_short, color='red', linewidth=0.8)
ax4.axhline(y=0, color='black', linestyle='-', linewidth=0.5)
ax4.set_ylabel('成交额 (元)', fontproperties=cn_font_medium)
ax4.grid(True, alpha=0.3)
ax4.set_title('短周期成交量脉冲 (高频噪声/日内博弈)', fontproperties=cn_font_medium, fontweight='bold')
ax4.set_xlabel('日期', fontproperties=cn_font_medium)

for ax in axes:
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
    ax.xaxis.set_minor_locator(mdates.MonthLocator())
    # 设置刻度标签字体
    for label in ax.get_xticklabels() + ax.get_yticklabels():
        label.set_fontproperties(cn_font_small)

plt.tight_layout(rect=[0, 0.02, 1, 0.96])
plt.savefig('/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0/wavelet_decomposition.png', dpi=150, bbox_inches='tight')
print("Plot 1 saved: wavelet_decomposition.png")
plt.close()

# Plot 2: Comparison
fig, axes = plt.subplots(2, 1, figsize=(16, 10), sharex=True)
fig.suptitle('沪深300ETF 成交量频段拆解与趋势真实性研判 (2019-2026)', 
             fontproperties=cn_font_large, fontweight='bold', y=0.98)

ax1 = axes[0]
ax1.plot(dates, close, color='black', linewidth=1.2)
ax1.set_ylabel('价格 (元)', fontproperties=cn_font_medium)
ax1.grid(True, alpha=0.3)
ax1.set_title('沪深300ETF收盘价', fontproperties=cn_font_medium, fontweight='bold')

ax2 = axes[1]
ax2.fill_between(dates, 0, volume, alpha=0.15, color='gray')
ax2.plot(dates, volume_long, color='blue', linewidth=2, label='long-term')
ax2.plot(dates, volume_medium, color='green', linewidth=1.5, label='medium-term')
ax2.plot(dates, volume_short, color='red', linewidth=0.8, alpha=0.6, label='short-term')
ax2.set_ylabel('成交额 (元)', fontproperties=cn_font_medium)
ax2.grid(True, alpha=0.3)
ax2.set_title('成交量多频段拆解', fontproperties=cn_font_medium, fontweight='bold')
ax2.set_xlabel('日期', fontproperties=cn_font_medium)

for ax in axes:
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
    ax.xaxis.set_minor_locator(mdates.MonthLocator())
    for label in ax.get_xticklabels() + ax.get_yticklabels():
        label.set_fontproperties(cn_font_small)

plt.tight_layout(rect=[0, 0.02, 1, 0.96])
plt.savefig('/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0/wavelet_comparison.png', dpi=150, bbox_inches='tight')
print("Plot 2 saved: wavelet_comparison.png")
plt.close()

print("All plots generated successfully!")
