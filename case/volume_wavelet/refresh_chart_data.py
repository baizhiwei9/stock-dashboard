"""
Pre-process: fetch fresh 510300 data from tushare, compute wavelet z-scores,
save to zscores.npz for plot_v15_signals.py chart generation.
Uses the same wavelet_decompose logic as daily_wavelet_monitor.py.
"""
import sys
sys.path.insert(0, '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0')
from tushare_config import pro, amount_to_yuan

import pandas as pd
import numpy as np
import pywt
from datetime import datetime, timedelta
import time

Z_THRESHOLD = 1.5
ROLLING_WINDOW = 20

def wavelet_decompose(volume, wavelet='sym4', level=3):
    pad_len = (2**level) - (len(volume) % (2**level))
    if pad_len == 2**level:
        pad_len = 0
    if pad_len > 0:
        volume_padded = np.pad(volume, (0, pad_len), mode='symmetric')
    else:
        volume_padded = volume
    coeffs = pywt.wavedec(volume_padded, wavelet, level=level)
    c_long = pywt.waverec([coeffs[0]] + [np.zeros_like(c) for c in coeffs[1:]], wavelet)[:len(volume)]
    c_medium = pywt.waverec([np.zeros_like(coeffs[0]), coeffs[1], coeffs[2], np.zeros_like(coeffs[3])], wavelet)[:len(volume)]
    return c_long, c_medium

# Fetch 510300 data with retry (same as daily_wavelet_monitor.py)
df = None
for attempt in range(3):
    try:
        df = pro.fund_daily(ts_code='510300.SH',
                            start_date=(datetime.now() - timedelta(days=370)).strftime('%Y%m%d'),
                            end_date=datetime.now().strftime('%Y%m%d'))
        if df is not None and len(df) > 0:
            break
    except Exception as e:
        print(f"  retry 510300.SH ({attempt+1}/3): {str(e)[:40]}")
        time.sleep(5 * (attempt + 1))

if df is None or len(df) == 0:
    raise RuntimeError("fund_daily fetch failed for 510300.SH")

df = df.sort_values('trade_date').reset_index(drop=True)
df['amount_yuan'] = amount_to_yuan(df['amount'])
df['close'] = df['close'].astype(float)

volume = df['amount_yuan'].values
close = df['close'].values
dates = pd.to_datetime(df['trade_date']).values

print(f"Data: {len(df)} rows, {pd.Timestamp(dates[0]).strftime('%Y-%m-%d')} to {pd.Timestamp(dates[-1]).strftime('%Y-%m-%d')}")

# Wavelet decomposition (same as daily_wavelet_monitor.py)
c_long, c_medium = wavelet_decompose(volume)

# Z-scores (same rolling window as compute_zscore.py and daily_wavelet_monitor.py)
roll_mean_long = pd.Series(c_long).rolling(window=ROLLING_WINDOW, min_periods=5).mean().values
roll_std_long = pd.Series(c_long).rolling(window=ROLLING_WINDOW, min_periods=5).std().values
z_long = np.zeros(len(c_long))
for i in range(len(c_long)):
    if roll_std_long[i] > 0:
        z_long[i] = (c_long[i] - roll_mean_long[i]) / roll_std_long[i]

roll_mean_med = pd.Series(c_medium).rolling(window=ROLLING_WINDOW, min_periods=5).mean().values
roll_std_med = pd.Series(c_medium).rolling(window=ROLLING_WINDOW, min_periods=5).std().values
z_med = np.zeros(len(c_medium))
for i in range(len(c_medium)):
    if roll_std_med[i] > 0:
        z_med[i] = (c_medium[i] - roll_mean_med[i]) / roll_std_med[i]

# Save with same keys as compute_zscore.py expects
np.savez('/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0/zscores.npz',
         dates=dates, z_long=z_long, z_med=z_med,
         cA=c_long, cD32=c_medium, close=close)

print(f"Last day: z_long={z_long[-1]:+.2f}, z_med={z_med[-1]:+.2f}")
print(f"Signals at z={Z_THRESHOLD}: UP={sum((z_long > Z_THRESHOLD) & (z_med > Z_THRESHOLD))}, "
      f"DOWN={sum((z_long < -Z_THRESHOLD) & (z_med < -Z_THRESHOLD))}")
print("Z-scores saved to zscores.npz")
