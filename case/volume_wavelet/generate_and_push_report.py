"""
Generate 510300 Wavelet Analysis Report and Push to Feishu
"""
import pandas as pd
import numpy as np
import pywt
import requests
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

# ========== CONFIG ==========
FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/6d34534a-af18-48ba-be77-a30c524734a8"
Z_THRESHOLD = 1.5
ROLLING_WINDOW = 20

# ========== DATA ==========
print("Loading 510300 data...")
df = pd.read_csv('/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0/510300_data.csv', encoding='utf-8-sig')
df['date'] = pd.to_datetime(df['日期'])
df = df.sort_values('date').reset_index(drop=True)

# Filter last ~60 days
cutoff = df['date'].max() - timedelta(days=90)
df = df[df['date'] >= cutoff].reset_index(drop=True)

volume = df['成交额'].values
close = df['收盘'].values
dates = df['date'].values

print(f"Data: {len(df)} rows, {dates[0]} to {dates[-1]}")

# ========== WAVELET DECOMPOSITION ==========
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
    c_short = pywt.waverec([np.zeros_like(coeffs[0]), np.zeros_like(coeffs[1]), np.zeros_like(coeffs[2]), coeffs[3]], wavelet)[:len(volume)]
    return c_long, c_medium, c_short

print("Decomposing...")
c_long, c_medium, c_short = wavelet_decompose(volume)

# ========== Z-SCORE ==========
roll_mean_long = pd.Series(c_long).rolling(window=ROLLING_WINDOW, min_periods=5).mean().values
roll_std_long = pd.Series(c_long).rolling(window=ROLLING_WINDOW, min_periods=5).std().values
z_long = np.array([(c_long[i]-roll_mean_long[i])/roll_std_long[i] if roll_std_long[i]>0 else 0 for i in range(len(c_long))])

roll_mean_med = pd.Series(c_medium).rolling(window=ROLLING_WINDOW, min_periods=5).mean().values
roll_std_med = pd.Series(c_medium).rolling(window=ROLLING_WINDOW, min_periods=5).std().values
z_medium = np.array([(c_medium[i]-roll_mean_med[i])/roll_std_med[i] if roll_std_med[i]>0 else 0 for i in range(len(c_medium))])

# ========== WEEKLY MOM ==========
def weekly_mom(series):
    if len(series) < 10: return None
    last = np.mean(series[-5:])
    prev = np.mean(series[-10:-5])
    return ((last-prev)/abs(prev))*100 if prev != 0 else 0

long_mom = weekly_mom(c_long)
med_mom = weekly_mom(c_medium)

# ========== PERCENTILES ==========
long_pct = np.sum(c_long <= c_long[-1]) / len(c_long) * 100
med_pct = np.sum(c_medium <= c_medium[-1]) / len(c_medium) * 100
vol_pct = np.sum(volume <= volume[-1]) / len(volume) * 100

# ========== SIGNAL DETECTION ==========
latest_z_long = z_long[-1]
latest_z_med = z_medium[-1]
signal_up = latest_z_long > Z_THRESHOLD and latest_z_med > Z_THRESHOLD
signal_down = latest_z_long < -Z_THRESHOLD and latest_z_med < -Z_THRESHOLD

# ========== GENERATE REPORT ==========
date_str = pd.Timestamp(dates[-1]).strftime('%Y-%m-%d')

report_lines = []
report_lines.append("=" * 60)
report_lines.append("📊 沪深300ETF (510300) 成交量小波分解日报")
report_lines.append(f"📅 报告日期: {date_str}")
report_lines.append("=" * 60)
report_lines.append("")

# Price info
prev_close = close[-2] if len(close) > 1 else close[-1]
close_change = ((close[-1] - prev_close) / prev_close * 100) if prev_close != 0 else 0
report_lines.append(f"💰 收盘价: {close[-1]:.3f} CNY ({close_change:+.2f}%)")
report_lines.append("")

# Volume summary
prev_vol = volume[-2] if len(volume) > 1 else volume[-1]
vol_change = ((volume[-1] - prev_vol) / prev_vol * 100) if prev_vol != 0 else 0
report_lines.append(f"📈 原始成交额: {volume[-1]/1e9:.2f} 亿元 ({vol_change:+.2f}%)")
report_lines.append(f"📊 历史分位数: {vol_pct:.1f}%")
report_lines.append("")

# Long-term
prev_long = c_long[-2] if len(c_long) > 1 else c_long[-1]
long_day_change = ((c_long[-1] - prev_long) / abs(prev_long) * 100) if prev_long != 0 else 0
report_lines.append("🔵 长周期趋势 (cA3 - 低频基底):")
report_lines.append(f"   当前值: {c_long[-1]/1e9:.2f} 亿元")
report_lines.append(f"   z-score: {latest_z_long:+.2f} (20日滚动)")
report_lines.append(f"   较昨日: {long_day_change:+.2f}%")
report_lines.append(f"   周环比: {long_mom:+.1f}%" if long_mom is not None else "   周环比: N/A")
report_lines.append(f"   历史分位: {long_pct:.1f}%")
report_lines.append("")

# Medium-term
prev_med = c_medium[-2] if len(c_medium) > 1 else c_medium[-1]
med_day_change = ((c_medium[-1] - prev_med) / abs(prev_med) * 100) if prev_med != 0 else 0
report_lines.append("🟢 中周期波段 (cD3+cD2 - 中频趋势资金):")
report_lines.append(f"   当前值: {c_medium[-1]/1e9:.2f} 亿元")
report_lines.append(f"   z-score: {latest_z_med:+.2f} (20日滚动)")
report_lines.append(f"   较昨日: {med_day_change:+.2f}%")
report_lines.append(f"   周环比: {med_mom:+.1f}%" if med_mom is not None else "   周环比: N/A")
report_lines.append(f"   历史分位: {med_pct:.1f}%")
report_lines.append("")

# Short-term
report_lines.append("🔴 短周期脉冲 (cD1 - 高频噪声):")
report_lines.append(f"   当前值: {c_short[-1]/1e9:.2f} 亿元")
report_lines.append("")

# Signal
report_lines.append("⚡ 信号检测 (z=±1.5):")
if signal_up:
    report_lines.append("   🚨 双频段共振上涨信号！")
    report_lines.append("   长周期+中周期同时突破+1.5σ")
    report_lines.append("   资金大幅涌入，趋势真实坚固")
elif signal_down:
    report_lines.append("   🚨 双频段共振下跌信号！")
    report_lines.append("   长周期+中周期同时突破-1.5σ")
    report_lines.append("   资金大幅撤离，警惕趋势逆转")
else:
    report_lines.append("   ✅ 暂无双频段共振信号")
    report_lines.append(f"   长周期 z={latest_z_long:+.2f}, 中周期 z={latest_z_med:+.2f}")
report_lines.append("")

# Interpretation
report_lines.append("📝 研判结论:")
if long_mom is not None and med_mom is not None:
    if long_mom > 0 and med_mom > 0:
        report_lines.append("   长周期+中周期周环比双升，")
        report_lines.append("   中低频同步放大，资金跨日建仓，趋势确立")
    elif long_mom < 0 and med_mom < 0:
        report_lines.append("   长周期+中周期周环比双降，")
        report_lines.append("   资金出逃，谨慎观望")
    else:
        report_lines.append("   长中周期方向不一致，")
        report_lines.append("   市场分歧，等待信号确认")
else:
    report_lines.append("   数据不足以判断周环比趋势")

report_lines.append("")
report_lines.append("=" * 60)
report_lines.append("⚠️ 免责声明: 本报告仅供研究参考，不构成投资建议")
report_lines.append("=" * 60)

report_text = "\n".join(report_lines)

# Save report
report_path = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0/daily_report.txt'
with open(report_path, 'w', encoding='utf-8') as f:
    f.write(report_text)

print("\n" + "="*60)
print(report_text)
print("\nReport saved to:", report_path)

# ========== PUSH TO FEISHU ==========
print("\nPushing to Feishu...")

# Convert report to Feishu card format
feishu_content = "\n".join([line for line in report_lines if not line.startswith("=")])

payload = {
    "msg_type": "interactive",
    "card": {
        "header": {
            "title": {
                "tag": "plain_text",
                "content": f"📊 510300成交量小波分析日报 | {date_str}"
            },
            "template": "blue"
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**报告时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n**数据区间**: {pd.Timestamp(dates[0]).strftime('%Y-%m-%d')} 至 {date_str}\n**分析标的**: 沪深300ETF (510300.SH)"
                }
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**💰 收盘价**: {close[-1]:.3f} CNY ({close_change:+.2f}%)\n**📈 成交额**: {volume[-1]/1e9:.2f} 亿元 ({vol_change:+.2f}%)\n**📊 历史分位**: {vol_pct:.1f}%"
                }
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**🔵 长周期 (cA3)**\n值: {c_long[-1]/1e9:.2f}亿 | z: {latest_z_long:+.2f}\n较昨日: {long_day_change:+.2f}% | 周环比: {long_mom:+.1f}% | 分位: {long_pct:.1f}%"
                }
            },
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**🟢 中周期 (cD3+cD2)**\n值: {c_medium[-1]/1e9:.2f}亿 | z: {latest_z_med:+.2f}\n较昨日: {med_day_change:+.2f}% | 周环比: {med_mom:+.1f}% | 分位: {med_pct:.1f}%"
                }
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**⚡ 信号检测 (z=±1.5)**\n{'🚨 双频段共振上涨！资金涌入，趋势确立' if signal_up else '🚨 双频段共振下跌！资金撤离，警惕逆转' if signal_down else '✅ 暂无共振信号'}\n\n**📝 研判**: {'中低频同步放大，可坚定顺势' if signal_up else '中低频同步萎缩，谨慎观望' if signal_down else '方向不一致，等待确认'}"
                }
            }
        ]
    }
}

try:
    resp = requests.post(FEISHU_WEBHOOK, json=payload, headers={'Content-Type': 'application/json'}, timeout=30)
    print(f"Feishu response: {resp.status_code}")
    if resp.status_code == 200:
        resp_data = resp.json()
        print(f"Response: {resp_data}")
        if resp_data.get('code') == 0:
            print("✅ Feishu push successful!")
        else:
            print(f"⚠️ Feishu returned error: {resp_data}")
    else:
        print(f"⚠️ HTTP error: {resp.status_code}")
        print(resp.text[:500])
except Exception as e:
    print(f"❌ Feishu push failed: {e}")

print("\nDone!")
