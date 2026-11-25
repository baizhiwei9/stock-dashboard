import requests
import json

APP_ID = "cli_a97b60337b7bdbd5"
APP_SECRET = "CwiWINsJk6Zzu0uO5pdmCd00y3HEQwPB"
WEBHOOK_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/6d34534a-af18-48ba-be77-a30c524734a8"

# 1. Get tenant_access_token
url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
headers = {"Content-Type": "application/json"}
data = {"app_id": APP_ID, "app_secret": APP_SECRET}

resp = requests.post(url, headers=headers, json=data, timeout=30)
print(f"Token API status: {resp.status_code}")
token_data = resp.json()
print(f"Token response: {json.dumps(token_data, indent=2)[:500]}")

if token_data.get("code") == 0:
    tenant_token = token_data["tenant_access_token"]
    print(f"✅ Got tenant_access_token")
    
    # 2. Upload image
    img_path = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0/v6_zscore_1.5_signals.png"
    upload_url = "https://open.feishu.cn/open-apis/im/v1/images"
    upload_headers = {"Authorization": f"Bearer {tenant_token}"}
    
    with open(img_path, "rb") as f:
        files = {"image": f}
        data = {"image_type": "message"}
        upload_resp = requests.post(upload_url, headers=upload_headers, files=files, data=data, timeout=30)
    
    print(f"Upload status: {upload_resp.status_code}")
    upload_data = upload_resp.json()
    print(f"Upload response: {json.dumps(upload_data, indent=2)[:500]}")
    
    if upload_data.get("code") == 0:
        image_key = upload_data["data"]["image_key"]
        print(f"✅ Got image_key: {image_key}")
        
        # 3. Send message via webhook with image_key
        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": "成交量小波分析 | 2026-09-17"},
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
                            "content": "**沪深300ETF (510300) 成交量小波分解图**\nsym4小波3层分解 | z=±1.5阈值"
                        }
                    }
                ]
            }
        }
        
        msg_resp = requests.post(WEBHOOK_URL, json=payload, headers={"Content-Type": "application/json"}, timeout=30)
        print(f"Webhook status: {msg_resp.status_code}")
        msg_data = msg_resp.json()
        print(f"Webhook response: {json.dumps(msg_data, indent=2)[:500]}")
        
        if msg_data.get("code") == 0:
            print("✅ Message with image sent successfully!")
        else:
            print(f"❌ Message failed: {msg_data}")
    else:
        print(f"❌ Upload failed: {upload_data}")
else:
    print(f"❌ Token failed: {token_data}")
