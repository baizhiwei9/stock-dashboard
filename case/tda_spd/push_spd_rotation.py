#!/usr/bin/env python3
"""推送上期->本期 SPD 行业旋转报告（桑基图 + 旋转轨迹图 + 整页图）到飞书"""
import os
import sys
import json
import time
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from feishu_config import APP_ID, APP_SECRET, WEBHOOK_URL, TOKEN_URL, UPLOAD_URL

IMAGES = [
    ("桑基图", os.path.join(HERE, "SPD行业桑基图_截至20261009.png")),
    ("旋转轨迹图", os.path.join(HERE, "SPD行业旋转轨迹_截至20261009.png")),
    ("整页报告", os.path.join(HERE, "SPD行业旋转_整页_截至20261009.png")),
]

TEXT_SUMMARY = (
    "**SPD行业旋转报告（桑基图+轨迹图）| 截至2026-10-09 | 来自 DoMate**\n"
    "---\n"
    "**近5个交易日 SPD 漂移 Top5 行业流转**（09-28 → 10-09）\n"
    "· 09-28/29 [震荡偏弱·51%]：金融地产62.2% / 流动性17.9% / 红利高息7.5% / 医药健康6.7% / 新能源5.7%\n"
    "· 09-30 [震荡偏强·57%]：金融地产51.5% / 医药健康17.7% / 红利高息12.2% / 小盘成长10.8% / 流动性7.8%\n"
    "· 10-08 [震荡偏强·57%]：金融地产50.0% / 红利高息17.8% / 小盘成长11.9% / 流动性10.5% / 科技成长9.7%\n"
    "· 10-09 [震荡偏强·56%]：金融地产43.9% / 红利高息18.9% / 流动性14.8% / 小盘成长14.3% / 高端制造8.2%\n"
    "---\n"
    "**关键流转**：09-29→09-30 新能源轮出、小盘成长轮入；09-30→10-08 医药健康轮出、科技成长轮入；10-08→10-09 科技成长轮出、高端制造轮入；金融地产全程在榜但权重 62.2%→43.9% 递减\n"
    "**信号T日收盘生成、T+1生效**：10-09 信号（10-12 生效）持有 金融地产/红利高息/流动性因子/小盘成长/高端制造\n"
    "仅供参考，不构成投资建议"
)


def get_tenant_access_token():
    resp = requests.post(TOKEN_URL, json={
        "app_id": APP_ID,
        "app_secret": APP_SECRET,
    }, timeout=30)
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取 tenant_access_token 失败: {data}")
    return data["tenant_access_token"]


def upload_image(token, path):
    with open(path, "rb") as f:
        resp = requests.post(
            UPLOAD_URL,
            headers={"Authorization": f"Bearer {token}"},
            data={"image_type": "message"},
            files={"image": (os.path.basename(path), f, "image/png")},
            timeout=60,
        )
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"上传图片失败 {os.path.basename(path)}: {data}")
    return data["data"]["image_key"]


def send_webhook(payload, label):
    for attempt in range(3):
        try:
            resp = requests.post(WEBHOOK_URL, json=payload, timeout=30)
            result = resp.json()
            if result.get("code") == 0 or result.get("StatusCode") == 0:
                print(f"[OK] {label} 发送成功")
                return True
            print(f"[WARN] {label} 返回: {result}")
        except Exception as e:
            print(f"[WARN] {label} 异常(第{attempt+1}次): {e}")
        time.sleep(2)
    return False


def main():
    token = get_tenant_access_token()
    print("已获取 tenant_access_token")

    # 1) 文本摘要（含流转结论）
    ok = send_webhook({
        "msg_type": "text",
        "content": {"text": TEXT_SUMMARY},
    }, "文本摘要")
    if not ok:
        print("[FATAL] 文本摘要发送失败，终止")
        sys.exit(1)
    time.sleep(1)

    # 2) 逐张上传并发送图片
    for name, path in IMAGES:
        try:
            image_key = upload_image(token, path)
        except Exception as e:
            print(f"[FATAL] {name} 上传失败: {e}")
            continue
        send_webhook({
            "msg_type": "image",
            "content": {"image_key": image_key},
        }, f"图片-{name}")
        time.sleep(1)


if __name__ == "__main__":
    main()