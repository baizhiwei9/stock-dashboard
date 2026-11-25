import pandas as pd
import numpy as np
import pywt
from datetime import datetime

# Load data
df = pd.read_csv('/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0/510300_data.csv', encoding='utf-8-sig')
df['date'] = pd.to_datetime(df['日期'])
df = df.sort_values('date').reset_index(drop=True)

# Last ~1 year
cutoff = pd.to_datetime('2025-09-17')
df = df[df['date'] >= cutoff].reset_index(drop=True)

volume = df['成交额'].values
close = df['收盘'].values
dates = df['date'].values

print(f"Data: {len(df)} rows, {pd.Timestamp(dates[0]).strftime('%Y-%m-%d')} to {pd.Timestamp(dates[-1]).strftime('%Y-%m-%d')}")

# Wavelet decomposition
coeffs = pywt.wavedec(volume, 'sym4', level=3)
cA = pywt.waverec([coeffs[0]] + [np.zeros_like(c) for c in coeffs[1:]], 'sym4')[:len(volume)]
cD32 = pywt.waverec([np.zeros_like(coeffs[0]), coeffs[1], coeffs[2], np.zeros_like(coeffs[3])], 'sym4')[:len(volume)]

# Calculate z-score with 20-day rolling window
window = 20
roll_mean_long = pd.Series(cA).rolling(window=window, min_periods=5).mean().values
roll_std_long = pd.Series(cA).rolling(window=window, min_periods=5).std().values
z_long = np.zeros(len(cA))
for i in range(len(cA)):
    if roll_std_long[i] > 0:
        z_long[i] = (cA[i] - roll_mean_long[i]) / roll_std_long[i]

roll_mean_med = pd.Series(cD32).rolling(window=window, min_periods=5).mean().values
roll_std_med = pd.Series(cD32).rolling(window=window, min_periods=5).std().values
z_med = np.zeros(len(cD32))
for i in range(len(cD32)):
    if roll_std_med[i] > 0:
        z_med[i] = (cD32[i] - roll_mean_med[i]) / roll_std_med[i]

# Recent 20 days analysis
print("\n=== Recent 20 Days Z-Score Analysis ===")
for i in range(-20, 0):
    sig_long = "UP" if z_long[i] > 2 else "DOWN" if z_long[i] < -2 else "flat"
    sig_med = "UP" if z_med[i] > 2 else "DOWN" if z_med[i] < -2 else "flat"
    both = "**SIGNAL**" if (z_long[i] > 2 and z_med[i] > 2) or (z_long[i] < -2 and z_med[i] < -2) else ""
    print(f"{pd.Timestamp(dates[i]).strftime('%Y-%m-%d')}: Long z={z_long[i]:+.2f} ({sig_long}), Med z={z_med[i]:+.2f} ({sig_med}) {both}")

# Last day signal
last_z_long, last_z_med = z_long[-1], z_med[-1]
print(f"\n=== Last Day (2026-09-16) Signal ===")
print(f"Long-term z-score: {last_z_long:+.2f}")
print(f"Medium-term z-score: {last_z_med:+.2f}")

# Signal detection at different thresholds
for threshold in [1.5, 2.0, 2.5]:
    if last_z_long > threshold and last_z_med > threshold:
        print(f"SIGNAL at z={threshold}: Both RISING")
    elif last_z_long < -threshold and last_z_med < -threshold:
        print(f"SIGNAL at z={threshold}: Both FALLING")
    else:
        print(f"No simultaneous signal at z={threshold}")

# Historical signal count
print("\n=== Historical Signal Count (Last Year) ===")
for threshold in [1.5, 2.0, 2.5]:
    up_signals = sum((z_long[i] > threshold) and (z_med[i] > threshold) for i in range(len(z_long)))
    down_signals = sum((z_long[i] < -threshold) and (z_med[i] < -threshold) for i in range(len(z_long)))
    print(f"z={threshold}: UP signals={up_signals}, DOWN signals={down_signals}, Total={up_signals+down_signals}")

# Save z-scores for plotting
np.savez('/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0/zscores.npz',
         dates=dates, z_long=z_long, z_med=z_med, 
         cA=cA, cD32=cD32, close=close)
print("\nZ-scores saved to zscores.npz")
