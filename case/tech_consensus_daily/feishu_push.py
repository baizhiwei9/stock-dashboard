# -*- coding: utf-8 -*-
"""飞书图文投递：先发汇总图（image_key 卡片），再发文字说明卡片
凭据引用 momentum-risk-control/feishu_config.py（既有资产，不复制密钥进本工程）
"""
import os
import sys
import requests

CFG_DIR = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/skills/user/momentum-risk-control"
if CFG_DIR not in sys.path:
    sys.path.insert(0, CFG_DIR)

TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
IMAGE_UPLOAD_URL = "https://open.feishu.cn/open-apis/im/v1/images"


def _config():
    import feishu_config as fc
    return fc.APP_ID, fc.APP_SECRET, fc.WEBHOOK_URL


def _tenant_token(app_id, app_secret):
    r = requests.post(TOKEN_URL, json={"app_id": app_id, "app_secret": app_secret}, timeout=30)
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError(f"飞书token失败: {d.get('msg')}")
    return d["tenant_access_token"]


def upload_image(token, img_path):
    with open(img_path, "rb") as f:
        r = requests.post(IMAGE_UPLOAD_URL,
                          headers={"Authorization": f"Bearer {token}"},
                          files={"image": (os.path.basename(img_path), f, "image/png")},
                          data={"image_type": "message"}, timeout=60)
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError(f"图片上传失败: {d.get('msg')}")
    return d["data"]["image_key"]


def _send_card(webhook, card):
    r = requests.post(webhook, json=card, headers={"Content-Type": "application/json"}, timeout=30)
    d = r.json()
    if d.get("code") != 0 and d.get("StatusCode") != 0:
        raise RuntimeError(f"飞书推送失败: {d.get('msg') or d.get('StatusMessage')}")
    return True


def push_image_card(webhook, token, image_key, title):
    """先发图卡片"""
    card = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {"template": "blue",
                       "title": {"tag": "plain_text", "content": title}},
            "elements": [
                {"tag": "img", "img_key": image_key,
                 "alt": {"tag": "plain_text", "content": "多空平汇总图"}},
            ],
        },
    }
    return _send_card(webhook, card)


def push_text_card(webhook, token, title, markdown):
    """再发文字说明卡片"""
    card = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {"template": "turquoise",
                       "title": {"tag": "plain_text", "content": title}},
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": markdown}},
            ],
        },
    }
    return _send_card(webhook, card)


def _chunk_markdown(markdown, limit=3500):
    """长文按段拆分（保留行内完整性），每条消息内容 ≤ limit；
    若 markdown 本身不超过 limit 则整体返回。"""
    paras = [p for p in markdown.split("\n\n") if p.strip()]
    chunks, cur = [], ""
    for p in paras:
        if len(cur) + len(p) + 2 > limit and cur:
            chunks.append(cur)
            cur = p
        else:
            cur = f"{cur}\n\n{p}" if cur else p
    if cur:
        chunks.append(cur)
    return chunks or [""]


def send_report(img_path, header, markdown):
    """完整流程：上传图 -> 先发图卡片 -> 再发文字卡片（长文按段拆多条，每条都带消息头）"""
    app_id, app_secret, webhook = _config()
    tk = _tenant_token(app_id, app_secret)
    key = upload_image(tk, img_path)
    push_image_card(webhook, tk, key, header)
    for seg in _chunk_markdown(markdown):
        push_text_card(webhook, tk, header, seg)
    return True