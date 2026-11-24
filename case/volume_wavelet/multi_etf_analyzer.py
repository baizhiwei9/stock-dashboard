"""
Multi-ETF Volume Wavelet Analysis Script
Analyzes 3 ETFs: 科创板(588000), 创业板(159915), 恒生科技(513130)
Outputs: Weekly MoM change table for long-term and medium-term volume components
"""
import pandas as pd
import numpy as np
import pywt
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

ETFS = {
    '588000': '科创板50ETF',
    '159915': '创业板ETF',
    '513130': '恒生科技ETF'
}

def wavelet_decompose(volume, wavelet='sym4', level=3):
    """Decompose volume into long/medium/short components"""
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
    c_short = pywt.waverec([np.zeros_like(coeffs[0]), np.zeros_like(coeffs[1]), np.zeros_like(coeffs[2]), coeffs[3]], wavelet)[:len(volume)]
    
    return c_long, c_medium, c_short

def weekly_mom_change(series, dates):
    """Calculate week-over-week change (comparing last 5 days avg vs previous 5 days avg)"""
    if len(series) < 10:
        return None, None
    
    last_week = series[-5:]
    prev_week = series[-10:-5]
    
    last_avg = np.mean(last_week)
    prev_avg = np.mean(prev_week)
    
    change_pct = ((last_avg - prev_avg) / abs(prev_avg)) * 100 if prev_avg != 0 else 0
    
    return change_pct, last_avg

def analyze_etf(csv_path, symbol, name):
    """Analyze single ETF and return metrics"""
    df = pd.read_csv(csv_path, encoding='utf-8-sig')
    df['date'] = pd.to_datetime(df['日期'])
    df = df.sort_values('date').reset_index(drop=True)
    
    # Filter last ~60 days for weekly analysis
    cutoff = df['date'].max() - timedelta(days=60)
    df = df[df['date'] >= cutoff].reset_index(drop=True)
    
    if len(df) < 20:
        return None
    
    volume = df['成交额'].values
    close = df['收盘'].values
    dates = df['date'].values
    
    # Wavelet decomposition
    c_long, c_medium, c_short = wavelet_decompose(volume)
    
    # Calculate z-scores (20-day rolling)
    roll_mean_long = pd.Series(c_long).rolling(window=20, min_periods=5).mean().values
    roll_std_long = pd.Series(c_long).rolling(window=20, min_periods=5).std().values
    z_long = np.array([(c_long[i] - roll_mean_long[i]) / roll_std_long[i] if roll_std_long[i] > 0 else 0 
                       for i in range(len(c_long))])
    
    roll_mean_med = pd.Series(c_medium).rolling(window=20, min_periods=5).mean().values
    roll_std_med = pd.Series(c_medium).rolling(window=20, min_periods=5).std().values
    z_medium = np.array([(c_medium[i] - roll_mean_med[i]) / roll_std_med[i] if roll_std_med[i] > 0 else 0 
                         for i in range(len(c_medium))])
    
    # Weekly MoM changes
    long_mom, long_last = weekly_mom_change(c_long, dates)
    med_mom, med_last = weekly_mom_change(c_medium, dates)
    
    # Current z-scores
    z_long_latest = z_long[-1]
    z_med_latest = z_medium[-1]
    
    # Historical percentiles
    long_pct = np.sum(c_long <= c_long[-1]) / len(c_long) * 100
    med_pct = np.sum(c_medium <= c_medium[-1]) / len(c_medium) * 100
    
    # Signal detection at z=1.5
    threshold = 1.5
    has_signal_up = z_long_latest > threshold and z_med_latest > threshold
    has_signal_down = z_long_latest < -threshold and z_med_latest < -threshold
    
    signal = "UP" if has_signal_up else "DOWN" if has_signal_down else "None"
    
    return {
        'symbol': symbol,
        'name': name,
        'dates': f"{pd.Timestamp(dates[0]).strftime('%Y-%m-%d')} to {pd.Timestamp(dates[-1]).strftime('%Y-%m-%d')}",
        'close': close[-1],
        'long_value': c_long[-1] / 1e9,
        'long_mom': long_mom,
        'long_z': z_long_latest,
        'long_pct': long_pct,
        'med_value': c_medium[-1] / 1e9,
        'med_mom': med_mom,
        'med_z': z_med_latest,
        'med_pct': med_pct,
        'signal': signal
    }

def generate_report(results):
    """Generate text report"""
    report = []
    report.append("=" * 80)
    report.append("Multi-ETF Volume Wavelet Analysis Report")
    report.append("Period: Last ~60 Trading Days")
    report.append("Method: sym4 Wavelet, 3-Level Decomposition, z-score threshold=1.5")
    report.append("=" * 80)
    report.append("")
    
    # Table header
    report.append("| ETF       | Close  | Long-term (cA3)          | Medium-term (cD3+cD2)    | Signal |")
    report.append("|-----------|--------|--------------------------|--------------------------|--------|")
    report.append("|           | (CNY)  | Value(B) | MoM%  | z    | pct | Value(B) | MoM%  | z    | pct |        |")
    report.append("|-----------|--------|----------|-------|------|-----|----------|-------|------|-----|--------|")
    
    for r in results:
        if r is None:
            continue
        long_mom_str = f"{r['long_mom']:+.1f}%" if r['long_mom'] is not None else "N/A"
        med_mom_str = f"{r['med_mom']:+.1f}%" if r['med_mom'] is not None else "N/A"
        
        line = f"| {r['name']:<9} | {r['close']:6.3f} | {r['long_value']:8.2f} | {long_mom_str:>6} | {r['long_z']:+5.2f} | {r['long_pct']:3.0f}% | {r['med_value']:8.2f} | {med_mom_str:>6} | {r['med_z']:+5.2f} | {r['med_pct']:3.0f}% | {r['signal']:<6} |"
        report.append(line)
    
    report.append("")
    report.append("Notes:")
    report.append("- Long-term (cA3): Low-frequency base trend")
    report.append("- Medium-term (cD3+cD2): Mid-frequency wave band (trend capital)")
    report.append("- MoM%: Week-over-week change (last 5 days avg vs previous 5 days avg)")
    report.append("- z: z-score relative to 20-day rolling mean/std")
    report.append("- pct: Historical percentile (0-100%)")
    report.append("- Signal: Both long+medium z>1.5 (UP) or <-1.5 (DOWN)")
    report.append("")
    report.append("Interpretation:")
    report.append("- MoM% > 0: Volume component rising vs last week")
    report.append("- MoM% < 0: Volume component declining vs last week")
    report.append("- z > 1.5: Significantly above recent average (20-day)")
    report.append("- z < -1.5: Significantly below recent average")
    report.append("- pct > 80: Near historical high for the period")
    report.append("- pct < 20: Near historical low for the period")
    
    return "\n".join(report)

def main():
    print("Multi-ETF Volume Wavelet Analyzer")
    print("=" * 50)
    print("")
    print("Required input: CSV files for each ETF")
    print("Expected format: date,open,high,low,close,volume,turnover,amplitude,pct_change,change,turnover_rate")
    print("Files needed:")
    for sym, name in ETFS.items():
        print(f"  - {sym}_data.csv ({name})")
    print("")
    print("To analyze, run: python multi_etf_analyzer.py --run")
    print("")

if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == '--run':
        # Run analysis
        results = []
        for sym, name in ETFS.items():
            csv_path = f'{sym}_data.csv'
            try:
                result = analyze_etf(csv_path, sym, name)
                if result:
                    results.append(result)
                    print(f"✓ {name} analyzed successfully")
                else:
                    print(f"✗ {name}: insufficient data")
            except Exception as e:
                print(f"✗ {name}: {e}")
        
        if results:
            report = generate_report(results)
            print("\n" + report)
            with open('multi_etf_report.txt', 'w', encoding='utf-8') as f:
                f.write(report)
            print("\nReport saved to: multi_etf_report.txt")
    else:
        main()
