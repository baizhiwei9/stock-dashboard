"""
Multi-ETF Volume Wavelet Decomposition + Weekly MoM Analysis
"""
import sys
sys.path.insert(0, '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0')
from tushare_config import pro, amount_to_yuan

import pandas as pd
import numpy as np
import pywt
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

ETFS = {
    '588000.SH': '科创板50ETF',
    '159915.SZ': '创业板ETF', 
    '513130.SH': '恒生科技ETF'
}

def wavelet_decompose(volume, wavelet='sym4', level=3):
    pad_len = (2**level) - (len(volume) % (2**level))
    if pad_len == 2**level: pad_len = 0
    if pad_len > 0:
        volume_padded = np.pad(volume, (0, pad_len), mode='symmetric')
    else:
        volume_padded = volume
    coeffs = pywt.wavedec(volume_padded, wavelet, level=level)
    c_long = pywt.waverec([coeffs[0]] + [np.zeros_like(c) for c in coeffs[1:]], wavelet)[:len(volume)]
    c_medium = pywt.waverec([np.zeros_like(coeffs[0]), coeffs[1], coeffs[2], np.zeros_like(coeffs[3])], wavelet)[:len(volume)]
    return c_long, c_medium

def weekly_mom(series):
    if len(series) < 10: return None, None
    last_avg = np.mean(series[-5:])
    prev_avg = np.mean(series[-10:-5])
    change_pct = ((last_avg - prev_avg) / abs(prev_avg)) * 100 if prev_avg != 0 else 0
    return change_pct, last_avg

results = []

for code, name in ETFS.items():
    print(f"\nProcessing {name} ({code})...")
    
    # Fetch data from Tushare
    df = pro.fund_daily(ts_code=code, start_date='20250917', end_date='20260916')
    df = df.sort_values('trade_date').reset_index(drop=True)
    
    # Convert amount to yuan via shared entry (Tushare amount is in 千元)
    df['amount_yuan'] = amount_to_yuan(df['amount'])
    df['close'] = df['close'].astype(float)
    
    volume = df['amount_yuan'].values
    close = df['close'].values
    dates = df['trade_date'].values
    
    print(f"  Loaded {len(df)} rows, {dates[0]} to {dates[-1]}")
    print(f"  Close: {close[-1]:.3f}, Amount: {volume[-1]/1e8:.2f} 亿元")
    
    # Wavelet decomposition
    c_long, c_medium = wavelet_decompose(volume)
    
    # Weekly MoM
    long_mom, long_last = weekly_mom(c_long)
    med_mom, med_last = weekly_mom(c_medium)
    
    # Daily change
    long_day_chg = ((c_long[-1] - c_long[-2]) / abs(c_long[-2]) * 100) if len(c_long) > 1 and c_long[-2] != 0 else 0
    med_day_chg = ((c_medium[-1] - c_medium[-2]) / abs(c_medium[-2]) * 100) if len(c_medium) > 1 and c_medium[-2] != 0 else 0
    
    # Historical percentiles
    long_pct = np.sum(c_long <= c_long[-1]) / len(c_long) * 100
    med_pct = np.sum(c_medium <= c_medium[-1]) / len(c_medium) * 100
    
    # Z-scores (20-day)
    roll_mean_long = pd.Series(c_long).rolling(window=20, min_periods=5).mean().values
    roll_std_long = pd.Series(c_long).rolling(window=20, min_periods=5).std().values
    z_long = (c_long[-1] - roll_mean_long[-1]) / roll_std_long[-1] if roll_std_long[-1] > 0 else 0
    
    roll_mean_med = pd.Series(c_medium).rolling(window=20, min_periods=5).mean().values
    roll_std_med = pd.Series(c_medium).rolling(window=20, min_periods=5).std().values
    z_med = (c_medium[-1] - roll_mean_med[-1]) / roll_std_med[-1] if roll_std_med[-1] > 0 else 0
    
    result = {
        'code': code,
        'name': name,
        'close': close[-1],
        'close_chg': ((close[-1] - close[-2]) / close[-2] * 100) if len(close) > 1 else 0,
        'amount': volume[-1] / 1e8,
        'long_value': c_long[-1] / 1e8,
        'long_mom': long_mom,
        'long_day_chg': long_day_chg,
        'long_z': z_long,
        'long_pct': long_pct,
        'med_value': c_medium[-1] / 1e8,
        'med_mom': med_mom,
        'med_day_chg': med_day_chg,
        'med_z': z_med,
        'med_pct': med_pct,
    }
    results.append(result)
    
    print(f"  Long: {result['long_value']:.2f}亿, MoM: {long_mom:+.1f}%, z: {z_long:+.2f}")
    print(f"  Med:  {result['med_value']:.2f}亿, MoM: {med_mom:+.1f}%, z: {z_med:+.2f}")

# Save results
import json
with open('/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0/wavelet_mom_results.json', 'w', encoding='utf-8') as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

print("\n✓ Results saved to wavelet_mom_results.json")
print("\n=== Summary ===")
for r in results:
    print(f"{r['name']}: Long MoM={r['long_mom']:+.1f}%, Med MoM={r['med_mom']:+.1f}%")
