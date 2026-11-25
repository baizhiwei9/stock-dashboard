"""
Wavelet Volume Signal Monitor for 510300
Detects simultaneous long-term + medium-term volume turning signals
"""
import akshare as ak
import pandas as pd
import numpy as np
import pywt
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from datetime import datetime, timedelta
import os
import sys

# ========== CONFIGURATION ==========
# Threshold for signal detection (z-score)
Z_THRESHOLD = 1.5
ROLLING_WINDOW = 20

# Notification settings
EMAIL_ENABLED = True
EMAIL_HOST = "smtp.163.com"
EMAIL_PORT = 25
EMAIL_USER = "songshang951@163.com"
EMAIL_PASS = os.environ.get("WAVELET_EMAIL_PASS", "")
EMAIL_TO = "410119229@qq.com"

FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/6d34534a-af18-48ba-be77-a30c524734a8"

# ========== DATA FETCH ==========
def fetch_data(symbol="510300", days=300):
    """Fetch last N days of ETF data"""
    try:
        df = ak.fund_etf_hist_em(symbol=symbol, period="daily", 
                                  start_date=(datetime.now() - timedelta(days=days)).strftime("%Y%m%d"),
                                  end_date=datetime.now().strftime("%Y%m%d"))
        df['日期'] = pd.to_datetime(df['日期'])
        df = df.sort_values('日期').reset_index(drop=True)
        return df
    except Exception as e:
        print(f"Error fetching data: {e}")
        return None

# ========== WAVELET DECOMPOSITION ==========
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
    
    # Long-term (cA3)
    c_long = pywt.waverec([coeffs[0]] + [np.zeros_like(c) for c in coeffs[1:]], wavelet)[:len(volume)]
    # Medium-term (cD3+cD2)
    c_medium = pywt.waverec([np.zeros_like(coeffs[0]), coeffs[1], coeffs[2], np.zeros_like(coeffs[3])], wavelet)[:len(volume)]
    # Short-term (cD1)
    c_short = pywt.waverec([np.zeros_like(coeffs[0]), np.zeros_like(coeffs[1]), np.zeros_like(coeffs[2]), coeffs[3]], wavelet)[:len(volume)]
    
    return c_long, c_medium, c_short

# ========== Z-SCORE CALCULATION ==========
def calculate_zscore(series, window=20):
    """Calculate rolling z-score"""
    roll_mean = pd.Series(series).rolling(window=window, min_periods=5).mean().values
    roll_std = pd.Series(series).rolling(window=window, min_periods=5).std().values
    z = np.zeros(len(series))
    for i in range(len(series)):
        if roll_std[i] > 0:
            z[i] = (series[i] - roll_mean[i]) / roll_std[i]
    return z

# ========== SIGNAL DETECTION ==========
def detect_signals(z_long, z_medium, threshold=2.0):
    """Detect simultaneous signals"""
    signals = []
    for i in range(len(z_long)):
        if z_long[i] > threshold and z_medium[i] > threshold:
            signals.append((i, 'UP', z_long[i], z_medium[i]))
        elif z_long[i] < -threshold and z_medium[i] < -threshold:
            signals.append((i, 'DOWN', z_long[i], z_medium[i]))
    return signals

# ========== NOTIFICATION ==========
def send_email(subject, body, image_path=None):
    """Send email notification"""
    if not EMAIL_ENABLED or not EMAIL_USER or not EMAIL_PASS or not EMAIL_TO:
        print("Email not configured, skipping...")
        return False
    
    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_USER
        msg['To'] = EMAIL_TO
        msg['Subject'] = subject
        
        msg.attach(MIMEText(body, 'html', 'utf-8'))
        
        if image_path and os.path.exists(image_path):
            with open(image_path, 'rb') as f:
                img = MIMEImage(f.read())
                img.add_header('Content-ID', '<chart>')
                msg.attach(img)
        
        server = smtplib.SMTP(EMAIL_HOST, EMAIL_PORT)
        server.starttls()
        server.login(EMAIL_USER, EMAIL_PASS)
        server.sendmail(EMAIL_USER, EMAIL_TO, msg.as_string())
        server.quit()
        print(f"Email sent successfully to {EMAIL_TO}")
        return True
    except Exception as e:
        print(f"Email failed: {e}")
        return False

def send_feishu(title, content):
    """Send Feishu webhook notification"""
    if not FEISHU_WEBHOOK:
        print("Feishu webhook not configured, skipping...")
        return False
    
    try:
        import requests
        import json
        
        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": title},
                    "template": "red"
                },
                "elements": [
                    {"tag": "div", "text": {"tag": "lark_md", "content": content}}
                ]
            }
        }
        
        resp = requests.post(FEISHU_WEBHOOK, json=payload, headers={'Content-Type': 'application/json'})
        print(f"Feishu response: {resp.status_code}")
        return resp.status_code == 200
    except Exception as e:
        print(f"Feishu failed: {e}")
        return False

# ========== SIGNAL DESCRIPTION ==========
def generate_signal_description(direction, z_long, z_med, c_long, c_medium, c_short, volume, close, dates, idx, threshold):
    """Generate rich text description of the signal"""
    
    # Current values
    curr_long = c_long[idx]
    curr_med = c_medium[idx]
    curr_short = c_short[idx]
    curr_vol = volume[idx]
    curr_close = close[idx]
    curr_date = pd.Timestamp(dates[idx]).strftime('%Y-%m-%d')
    
    # Previous day values
    prev_long = c_long[idx-1] if idx > 0 else curr_long
    prev_med = c_medium[idx-1] if idx > 0 else curr_med
    prev_vol = volume[idx-1] if idx > 0 else curr_vol
    prev_close = close[idx-1] if idx > 0 else curr_close
    
    # Changes
    long_change = ((curr_long - prev_long) / abs(prev_long) * 100) if prev_long != 0 else 0
    med_change = ((curr_med - prev_med) / abs(prev_med) * 100) if prev_med != 0 else 0
    vol_change = ((curr_vol - prev_vol) / prev_vol * 100) if prev_vol != 0 else 0
    close_change = ((curr_close - prev_close) / prev_close * 100) if prev_close != 0 else 0
    
    # Percentiles (historical position)
    long_pct = np.sum(c_long[:idx+1] <= curr_long) / (idx + 1) * 100 if idx > 0 else 50
    med_pct = np.sum(c_medium[:idx+1] <= curr_med) / (idx + 1) * 100 if idx > 0 else 50
    vol_pct = np.sum(volume[:idx+1] <= curr_vol) / (idx + 1) * 100 if idx > 0 else 50
    
    # Historical highs/lows
    long_max, long_min = np.max(c_long[:idx+1]), np.min(c_long[:idx+1])
    med_max, med_min = np.max(c_medium[:idx+1]), np.min(c_medium[:idx+1])
    
    # Distance from historical extremes
    long_dist_max = ((curr_long - long_max) / long_max * 100) if long_max != 0 else 0
    med_dist_max = ((curr_med - med_max) / med_max * 100) if med_max != 0 else 0
    
    # Trend direction words
    if direction == 'UP':
        trend_word = "上涨"
        strength = "强势" if z_long > threshold + 0.5 else ""
    else:
        trend_word = "下跌"
        strength = "恐慌" if z_long < -(threshold + 0.5) else ""
    
    # Build description
    desc = f"""
**信号日期**: {curr_date}
**信号类型**: 长周期+中周期成交量同时{trend_word} (z>{threshold})

**价格信息**:
- 收盘价: {curr_close:.3f} CNY ({close_change:+.2f}% vs 昨日)

**成交量分析**:
- 原始成交额: {curr_vol/1e9:.2f} 亿元 ({vol_change:+.2f}% vs 昨日)
- 历史分位数: {vol_pct:.1f}% (越高表示成交量越活跃)

**长周期趋势 (cA3 - 低频基底)**:
- 当前值: {curr_long/1e9:.2f} 亿元
- z-score: {z_long:+.2f} (20日滚动)
- 较昨日变化: {long_change:+.2f}%
- 历史分位数: {long_pct:.1f}%
- 距历史最高点: {long_dist_max:+.1f}%

**中周期波段 (cD3+cD2 - 中频趋势资金)**:
- 当前值: {curr_med/1e9:.2f} 亿元
- z-score: {z_med:+.2f} (20日滚动)
- 较昨日变化: {med_change:+.2f}%
- 历史分位数: {med_pct:.1f}%
- 距历史最高点: {med_dist_max:+.1f}%

**短周期脉冲 (cD1 - 高频噪声)**:
- 当前值: {curr_short/1e9:.2f} 亿元

**研判结论**:
长周期{'上涨' if curr_long > prev_long else '下跌'} {abs(long_change):.1f}%，中周期{'上涨' if curr_med > prev_med else '下跌'} {abs(med_change):.1f}%。
"""
    
    if direction == 'UP':
        if long_pct > 80 and med_pct > 80:
            desc += "双频段成交量均处于历史高位，资金大幅涌入，趋势真实坚固，可坚定顺势。"
        elif long_pct > 60 or med_pct > 60:
            desc += "中低频同步放大，有大级别资金跨日建仓，趋势确立信号。"
        else:
            desc += "中低频同步上行，成交量改善中，关注持续性。"
    else:
        if long_pct < 20 and med_pct < 20:
            desc += "双频段成交量均处于历史低位，资金大幅撤离，警惕趋势逆转。"
        elif long_pct < 40 or med_pct < 40:
            desc += "中低频同步萎缩，资金出逃迹象，谨慎观望。"
        else:
            desc += "中低频同步下行，成交量走弱，趋势可能承压。"
    return desc

# ========== DAILY SUMMARY ==========
def generate_daily_summary(c_long, c_medium, c_short, volume, close, dates, idx, threshold, z_long, z_med):
    """Generate daily summary when no signal is present"""
    
    curr_long = c_long[idx]
    curr_med = c_medium[idx]
    curr_vol = volume[idx]
    curr_close = close[idx]
    curr_date = pd.Timestamp(dates[idx]).strftime('%Y-%m-%d')
    
    prev_long = c_long[idx-1] if idx > 0 else curr_long
    prev_med = c_medium[idx-1] if idx > 0 else curr_med
    prev_vol = volume[idx-1] if idx > 0 else curr_vol
    
    long_change = ((curr_long - prev_long) / abs(prev_long) * 100) if prev_long != 0 else 0
    med_change = ((curr_med - prev_med) / abs(prev_med) * 100) if prev_med != 0 else 0
    vol_change = ((curr_vol - prev_vol) / prev_vol * 100) if prev_vol != 0 else 0
    
    long_pct = np.sum(c_long[:idx+1] <= curr_long) / (idx + 1) * 100 if idx > 0 else 50
    med_pct = np.sum(c_medium[:idx+1] <= curr_med) / (idx + 1) * 100 if idx > 0 else 50
    vol_pct = np.sum(volume[:idx+1] <= curr_vol) / (idx + 1) * 100 if idx > 0 else 50
    
    summary = f"""
**日期**: {curr_date} | **收盘价**: {curr_close:.3f} CNY
**原始成交额**: {curr_vol/1e9:.2f} 亿元 ({vol_change:+.2f}%)
**历史分位数**: {vol_pct:.1f}%

**长周期 (cA3)**: {curr_long/1e9:.2f} 亿元 | z={z_long[idx]:+.2f} | {long_change:+.1f}% | 分位 {long_pct:.1f}%
**中周期 (cD3+cD2)**: {curr_med/1e9:.2f} 亿元 | z={z_med[idx]:+.2f} | {med_change:+.1f}% | 分位 {med_pct:.1f}%
**短周期 (cD1)**: {c_short[idx]/1e9:.2f} 亿元

**状态**: 无双频段同时突破 z={threshold}
"""
    
    return summary

def main():
    print(f"[{datetime.now()}] Starting Wavelet Signal Monitor...")
    print(f"Z-threshold: {Z_THRESHOLD}, Rolling window: {ROLLING_WINDOW}")
    
    # Fetch data
    df = fetch_data()
    if df is None or len(df) < 60:
        print("Insufficient data, aborting.")
        return
    
    volume = df['成交额'].values
    dates = df['日期'].values
    close = df['收盘'].values
    
    print(f"Loaded {len(df)} rows, {pd.Timestamp(dates[0]).strftime('%Y-%m-%d')} to {pd.Timestamp(dates[-1]).strftime('%Y-%m-%d')}")
    
    # Decompose
    c_long, c_medium, c_short = wavelet_decompose(volume)
    
    # Calculate z-scores
    z_long = calculate_zscore(c_long, ROLLING_WINDOW)
    z_medium = calculate_zscore(c_medium, ROLLING_WINDOW)
    
    # Detect signals
    signals = detect_signals(z_long, z_medium, Z_THRESHOLD)
    
    print(f"\nHistorical signals (z>{Z_THRESHOLD}): {len(signals)}")
    for idx, direction, z_l, z_m in signals[-5:]:
        print(f"  {pd.Timestamp(dates[idx]).strftime('%Y-%m-%d')}: {direction} (Long z={z_l:+.2f}, Med z={z_m:+.2f})")
    
    # Check latest signal
    latest_idx = len(z_long) - 1
    latest_z_long = z_long[latest_idx]
    latest_z_medium = z_medium[latest_idx]
    latest_date = pd.Timestamp(dates[latest_idx]).strftime('%Y-%m-%d')
    
    print(f"\nLatest ({latest_date}): Long z={latest_z_long:+.2f}, Med z={latest_z_medium:+.2f}")
    
    # Generate alert if signal
    if latest_z_long > Z_THRESHOLD and latest_z_medium > Z_THRESHOLD:
        title = f"[ALERT] 510300 Volume Signal: BOTH RISING (z>{Z_THRESHOLD})"
        signal_desc = generate_signal_description('UP', latest_z_long, latest_z_medium, 
                                                   c_long, c_medium, c_short, volume, close, dates, 
                                                   latest_idx, Z_THRESHOLD)
        content = f"""<h3>📈 成交量双频段上涨信号</h3>
<pre>{signal_desc}</pre>
"""
        print(f"\n*** SIGNAL DETECTED: BOTH RISING ***")
        print(signal_desc)
        send_email(title, content)
        send_feishu(title, signal_desc)
        
    elif latest_z_long < -Z_THRESHOLD and latest_z_medium < -Z_THRESHOLD:
        title = f"[ALERT] 510300 Volume Signal: BOTH FALLING (z<-{Z_THRESHOLD})"
        signal_desc = generate_signal_description('DOWN', latest_z_long, latest_z_medium, 
                                                   c_long, c_medium, c_short, volume, close, dates, 
                                                   latest_idx, Z_THRESHOLD)
        content = f"""<h3>📉 成交量双频段下跌信号</h3>
<pre>{signal_desc}</pre>
"""
        print(f"\n*** SIGNAL DETECTED: BOTH FALLING ***")
        print(signal_desc)
        send_email(title, content)
        send_feishu(title, signal_desc)
        
    else:
        # Generate daily summary even without signal
        summary = generate_daily_summary(c_long, c_medium, c_short, volume, close, dates, 
                                         latest_idx, Z_THRESHOLD, z_long, z_medium)
        print(f"\nNo simultaneous signal at z={Z_THRESHOLD} threshold.")
        print(summary)
        # Send daily summary to Feishu (optional, can be disabled)
        # send_feishu(f"510300 Daily Summary - {latest_date}", summary)
    
    print(f"\n[{datetime.now()}] Monitor complete.")

if __name__ == '__main__':
    main()
