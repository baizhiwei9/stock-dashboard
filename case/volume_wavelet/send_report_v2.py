import json
import requests
from datetime import datetime

APP_ID = "cli_a97b60337b7bdbd5"
APP_SECRET = "CwiWINsJk6Zzu0uO5pdmCd00y3HEQwPB"
WEBHOOK_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/6d34534a-af18-48ba-be77-a30c524734a8"
Z_THRESHOLD = 1.5

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

# Load analysis results
with open('/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0/wavelet_mom_results.json', 'r', encoding='utf-8') as f:
    results = json.load(f)

# Get token and upload image
print("Getting token...")
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

# Message 1: Image
date_str = datetime.now().strftime('%Y-%m-%d')
msg1 = {
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
                    "content": "**沪深300ETF (510300) 成交量小波分解图**\nsym4小波3层分解 | z=±1.5阈值 | 2025-09-17 至 2026-09-16"
                }
            }
        ]
    }
}

print("\nSending message 1 (image)...")
success1, resp1 = send_webhook_card(msg1)
print(f"Message 1: {'✅' if success1 else '❌'} {resp1}")

# Message 2: Text report
text_content = ""
for r in results:
    long_mom_str = f"{r['long_mom']:+.1f}%" if r['long_mom'] is not None else "N/A"
    med_mom_str = f"{r['med_mom']:+.1f}%" if r['med_mom'] is not None else "N/A"
    signal_long = " 🚨" if r['long_z'] > Z_THRESHOLD else " ⚠️" if r['long_z'] < -Z_THRESHOLD else ""
    signal_med = " 🚨" if r['med_z'] > Z_THRESHOLD else " ⚠️" if r['med_z'] < -Z_THRESHOLD else ""
    
    text_content += f"**【{r['name']} ({r['code']})】**\n"
    text_content += f"收盘价: {r['close']:.3f} ({r['close_chg']:+.2f}%) | 成交额: {r['amount']:.2f}亿\n"
    text_content += f"🔵 长周期{signal_long}: {r['long_value']:.2f}亿 | z={r['long_z']:+.2f} | 日环比{r['long_day_chg']:+.2f}% | 周环比{long_mom_str} | 分位{r['long_pct']:.1f}%\n"
    text_content += f"🟢 中周期{signal_med}: {r['med_value']:.2f}亿 | z={r['med_z']:+.2f} | 日环比{r['med_day_chg']:+.2f}% | 周环比{med_mom_str} | 分位{r['med_pct']:.1f}%\n"
    
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

msg2 = {
    "msg_type": "interactive",
    "card": {
        "header": {
            "title": {"tag": "plain_text", "content": f"成交量小波分析 | {date_str} | 四标文字报告"},
            "template": "blue"
        },
        "elements": [
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": text_content}
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
success2, resp2 = send_webhook_card(msg2)
print(f"Message 2: {'✅' if success2 else '❌'} {resp2}")

if success1 and success2:
    print("\n✅ Both messages sent successfully!")
else:
    print("\n⚠️ Some messages failed")
