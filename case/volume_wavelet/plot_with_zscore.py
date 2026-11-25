import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime
import pandas as pd

# Load z-score data
data = np.load('/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0/zscores.npz', allow_pickle=True)
dates = pd.to_datetime(data['dates'])
z_long = data['z_long']
z_med = data['z_med']
cA = data['cA']
cD32 = data['cD32']
close = data['close']

# Convert to Billions
v_long = cA / 1e9
v_med = cD32 / 1e9

fig, axes = plt.subplots(3, 1, figsize=(16, 12), sharex=True,
                         gridspec_kw={'height_ratios': [1, 1.5, 1]})

# Panel 1: Price
ax1 = axes[0]
ax1.plot(dates, close, color='black', linewidth=1.2)
ax1.set_ylabel('Close Price (CNY)', fontsize=11)
ax1.grid(True, alpha=0.3)
ax1.set_title('CSI 300 ETF (510300) - Volume Wavelet + Z-Score Signal (Last ~1 Year)', 
              fontsize=14, fontweight='bold')

# Panel 2: Volume with z-score colors
ax2 = axes[1]

# Plot long-term with intensity based on z-score
for i in range(len(dates)-1):
    if z_long[i] > 2:
        color = '#0000CC'  # deep blue for high confidence up
    elif z_long[i] < -2:
        color = '#6666FF'  # light blue for high confidence down
    else:
        color = 'blue'
    ax2.plot([dates[i], dates[i+1]], [v_long[i], v_long[i+1]], color=color, linewidth=2.0)

ax2.fill_between(dates, v_long, alpha=0.2, color='blue')
ax2.plot(dates, v_long, color='blue', linewidth=0.5, alpha=0.3)  # thin baseline

# Medium-term
for i in range(len(dates)-1):
    if z_med[i] > 2:
        color = '#006600'  # deep green
    elif z_med[i] < -2:
        color = '#66CC66'  # light green
    else:
        color = 'green'
    ax2.plot([dates[i], dates[i+1]], [v_med[i], v_med[i+1]], color=color, linewidth=1.5)

# Signal markers
for i in range(len(dates)):
    if z_long[i] > 2 and z_med[i] > 2:
        ax2.scatter(dates[i], v_long[i], color='red', s=100, marker='^', zorder=5, edgecolors='black', linewidth=0.5)
    elif z_long[i] < -2 and z_med[i] < -2:
        ax2.scatter(dates[i], v_long[i], color='red', s=100, marker='v', zorder=5, edgecolors='black', linewidth=0.5)

ax2.axhline(y=0, color='gray', linestyle='--', linewidth=0.7, alpha=0.5)
ax2.set_ylabel('Volume (Billion CNY)', fontsize=11)
ax2.grid(True, alpha=0.3)

# Custom legend
from matplotlib.lines import Line2D
legend_elements = [
    Line2D([0], [0], color='blue', lw=2, label='Long-term (cA3)'),
    Line2D([0], [0], color='green', lw=1.5, label='Medium-term (cD3+cD2)'),
    Line2D([0], [0], marker='^', color='red', markersize=8, linestyle='None', label='Both UP (z>2)'),
    Line2D([0], [0], marker='v', color='red', markersize=8, linestyle='None', label='Both DOWN (z<-2)'),
]
ax2.legend(handles=legend_elements, loc='upper left', fontsize=9)

# Panel 3: Z-score
ax3 = axes[2]
ax3.plot(dates, z_long, color='blue', linewidth=1.5, label='Long-term z-score')
ax3.plot(dates, z_med, color='green', linewidth=1.5, label='Medium-term z-score')
ax3.axhline(y=0, color='black', linestyle='-', linewidth=0.5)
ax3.axhline(y=2, color='red', linestyle='--', linewidth=1, alpha=0.7, label='z=+2')
ax3.axhline(y=-2, color='red', linestyle='--', linewidth=1, alpha=0.7, label='z=-2')
ax3.axhline(y=1.5, color='orange', linestyle=':', linewidth=0.8, alpha=0.5)
ax3.axhline(y=-1.5, color='orange', linestyle=':', linewidth=0.8, alpha=0.5)
ax3.fill_between(dates, z_long, where=(z_long > 2) | (z_long < -2), alpha=0.2, color='blue')
ax3.fill_between(dates, z_med, where=(z_med > 2) | (z_med < -2), alpha=0.2, color='green')
ax3.set_ylabel('Z-Score', fontsize=11)
ax3.set_xlabel('Date', fontsize=11)
ax3.legend(loc='upper left', fontsize=9)
ax3.grid(True, alpha=0.3)

# X-axis formatting
for ax in axes:
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    ax.tick_params(axis='x', rotation=45)

plt.tight_layout()
out_path = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0/v5_zscore_signals.png'
plt.savefig(out_path, dpi=180, bbox_inches='tight')
plt.close()
print(f"Saved: {out_path}")
