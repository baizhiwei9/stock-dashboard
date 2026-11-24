"""
Send full wavelet report to Feishu in 2 messages:
1. Image card (510300 chart)
2. Text card (all 4 ETFs analysis)
"""
import sys
sys.path.insert(0, '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0')
from tushare_config import pro, amount_to_yuan

import pandas as pd
import numpy as np
import pywt
import requests
from datetime import datetime
import json

APP_ID = "cli_a97b60337b7bdbd5"
APP_SECRET = "CwiWINsJk6Zzu0uO5pdmCd00y3HEQwPB"
WEBHOOK_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/6d34534a-af18-48ba-be77-a30c524734a8"
Z_THRESHOLD = 1.5
ROLLING_WINDOW = 20

def get_tenant_token():
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    resp = requests.post(url, headers={"Content-Type": "application/json"}, 
                         json={"app_id": APP_ID, "app_secret": APP_SECRET}, timeout=30)
    data = resp.json()
    if data.get("code") == 0:
        return data["tenant_access_token"]
    print(f"Token error: {data}")
    return None

def upload_image(token, img_path):
    url = "https://open.feishu.cn/open-apis/im/v1/images"
    headers = {"Authorization": f"Bearer {token}"}
    with open(img_path, "rb") as f:
        files = {"image": f}
        data = {"image_type": "message"}
        resp = requests.post(url, headers=headers, files=files, data=data, timeout=30)
    data = resp.json()
    if data.get("code") == 0:
        return data["data"]["image_key"]
    print(f"Upload error: {data}")
    return None

def send_webhook_card(payload):
    resp = requests.post(WEBHOOK_URL, json=payload, headers={"Content-Type": "application/json"}, timeout=30)
    data = resp.json()
    return data.get("code") == 0, data

# ========== DATA ANALYSIS ==========
ETFS = {
    '510300.SH': '沪深300ETF',
    '588000.SH': '科创板50ETF',
    '159915.SZ': '创业板ETF',
    '513130.SH': '恒生科技ETF',
}

def wavelet_decompose(volume):
    pad_len = (2**3) - (len(volume) % (2**3))
    if pad_len == 8: pad_len = 0
    if pad_len > 0:
        volume_padded = np.pad(volume, (0, pad_len), mode='symmetric')
    else:
        volume_padded = volume
    coeffs = pywt.wavedec(volume_padded, 'sym4', level=3)
    c_long = pywt.waverec([coeffs[0]] + [np.zeros_like(c) for c in coeffs[1:]], 'sym4')[:len(volume)]
    c_medium = pywt.waverec([np.zeros_like(coeffs[0]), coeffs[1], coeffs[2], np.zeros_like(coeffs[3])], 'sym4')[:len(volume)]
    return c_long, c_medium

def analyze_etf(code, name):
    df = pro.fund_daily(ts_code=code, start_date='20250917', end_date=datetime.now().strftime('%Y%m%d'))
    df = df.sort_values('trade_date').reset_index(drop=True)
    df['amount_yuan'] = amount_to_yuan(df['amount'])
    df['close'] = df['close'].astype(float)
    
    volume = df['amount_yuan'].values
    close = df['close'].values
    
    c_long, c_medium = wavelet_decompose(volume)
    
    roll_mean_long = pd.Series(c_long).rolling(window=20, min_periods=5).mean().values
    roll_std_long = pd.Series(c_long).rolling(window=20, min_periods=5).std().values
    z_long = (c_long[-1] - roll_mean_long[-1]) / roll_std_long[-1] if roll_std_long[-1] > 0 else 0
    
    roll_mean_med = pd.Series(c_medium).rolling(window=20, min_periods=5).mean().values
    roll_std_med = pd.Series(c_medium).rolling(window=20, min_periods=5).std().values
    z_med = (c_medium[-1] - roll_mean_med[-1]) / roll_std_med[-1] if roll_std_med[-1] > 0 else 0
    
    long_mom = ((np.mean(c_long[-5:]) - np.mean(c_long[-10:-5])) / abs(np.mean(c_long[-10:-5])) * 100) if len(c_long) >= 10 else None
    med_mom = ((np.mean(c_medium[-5:]) - np.mean(c_medium[-10:-5])) / abs(np.mean(c_medium[-10:-5])) * 100) if len(c_medium) >= 10 else None
    
    long_day = ((c_long[-1] - c_long[-2]) / abs(c_long[-2]) * 100) if len(c_long) > 1 and c_long[-2] != 0 else 0
    med_day = ((c_medium[-1] - c_medium[-2]) / abs(c_medium[-2]) * 100) if len(c_medium) > 1 and c_medium[-2] != 0 else 0
    close_chg = ((close[-1] - close[-2]) / close[-2] * 100) if len(close) > 1 else 0
    
    long_pct = np.sum(c_long <= c_long[-1]) / len(c_long) * 100
    med_pct = np.sum(c_medium <= c_medium[-1]) / len(c_medium) * 100
    
    return {
        'code': code.split('.')[0],
        'name': name,
        'close': close[-1],
        'close_chg': close_chg,
        'amount': volume[-1] / 1e8,
        'long_value': c_long[-1] / 1e8,
        'long_mom': long_mom,
        'long_day': long_day,
        'long_z': z_long,
        'long_pct': long_pct,
        'med_value': c_medium[-1] / 1e8,
        'med_mom': med_mom,
        'med_day': med_day,
        'med_z': z_med,
        'med_pct': med_pct,
    }

print("Analyzing 4 ETFs...")
results = []
for code, name in ETFS.items():
    r = analyze_etf(code, name)
    results.append(r)
    print(f"✓ {name}")

# ========== GET TOKEN & UPLOAD IMAGE ==========
print("\nGetting token...")
token = get_tenant_token()
if not token:
    print("Failed to get token")
    exit(1)

print("Uploading image...")
img_path = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0/v6_zscore_1.5_signals.png"
image_key = upload_image(token, img_path)
if not image_key:
    print("Failed to upload image")
    exit(1)

print(f"Image key: {image_key}")

# ========== MESSAGE 1: Image Card ==========
date_str = datetime.now().strftime('%Y-%m-%d')
msg1_payload = {
    "msg_type": "interactive",
    "card": {
        "header": {
            "title": {"tag": "plain_text", "content": f"成交量小波分析 | {date_str} | 510300图表"},
            "template": "blue"
        },
        "elements": [
            {
                "tag": "img",
                "img_key": image_key,
                "alt": {"tag": "plain_text", "content": "510300 Wavelet Chart"}
            },
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": "**沪深300ETF (510300) 成交量小波分解图**\nsym4小波3层分解 | z=±1.5阈值 | 数据区间: 2025-09-17 至 2026-09-16"
                }
            }
        ]
    }
}

print("\nSending message 1 (image)...")
success1, resp1 = send_webhook_card(msg1_payload)
print(f"Message 1: {'✅ Success' if success1 else '❌ Failed'} - {resp1}")

# ========== MESSAGE 2: Text Report ==========
text_content = ""
for r in results:
    long_mom_str = f"{r['long_mom']:+.1f}%" if r['long_mom'] is not None else "N/A"
    med_mom_str = f"{r['med_mom']:+.1f}%" if r['med_mom'] is not None else "N/A"
    
    signal_long = " 🚨" if r['long_z'] > Z_THRESHOLD else " ⚠️" if r['long_z'] < -Z_THRESHOLD else ""
    signal_med = " 🚨" if r['med_z'] > Z_THRESHOLD else " ⚠️" if r['med_z'] < -Z_THRESHOLD else ""
    
    text_content += f"**【{r['name']} ({r['code']})】**\n"
    text_content += f"收盘价: {r['close']:.3f} ({r['close_chg']:+.2f}%) | 成交额: {r['amount']:.2f}亿\n"
    text_content += f"🔵 长周期{signal_long}: {r['long_value']:.2f}亿 | z={r['long_z']:+.2f} | 日环比{r['long_day']:+.2f}% | 周环比{long_mom_str} | 分位{r['long_pct']:.1f}%\n"
    text_content += f"🟢 中周期{signal_med}: {r['med_value']:.2f}亿 | z={r['med_z']:+.2f} | 日环比{r['med_day']:+.2f}% | 周环比{med_mom_str} | 分位{r['med_pct']:.1f}%\n"
    
    if r['long_z'] > Z_THRESHOLD and r['med_z'] > Z_THRESHOLD:
        text_content += "⚡ **双频段共振上涨** — 资金大幅涌入，趋势确立\n"
    elif r['long_z'] < -Z_THRESHOLD and r['med_z'] < -Z_THRESHOLD:
        text_content += "⚡ **双频段共振下跌** — 资金撤离，警惕逆转\n"
    elif r['long_z'] > Z_THRESHOLD:
        text_content += "⚡ **长周期单频段突破** — 长期资金改善，关注持续性\n"
    elif r['long_z'] < -Z_THRESHOLD:
        text_content += "⚡ **长周期单频段跌破** — 长期资金走弱，谨慎观望\n"
    else:
        text_content += f"⚡ 暂无共振 (长z={r['long_z']:+.2f}, 中z={r['med_z']:+.2f})\n"
    
    text_content += "\n"

msg2_payload = {
    "msg_type": "interactive",
    "card": {
        "header": {
            "title": {"tag": "plain_text", "content": f"成交量小波分析 | {date_str} | 四标文字报告"},
            "template": "blue"
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": text_content
                }
            },
            {"tag": "hr"},
            {
                "tag": "note",
                "elements": [{"tag": "plain_text", "content": "⚠️ 仅供研究参考，不构成投资建议"}]
            }
        ]
    }
}

print("Sending message 2 (text)...")
success2, resp2 = send_webhook_card(msg2_payload)
print(f"Message 2: {'✅ Success' if success2 else '❌ Failed'} - {resp2}")

print("\nDone!")
