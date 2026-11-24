#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ot_render.py — 渲染与飞书推送工具（可移植版）
==============================================
从原 ot_daily_report.py 中抽出的通用部分：PIL 中英文混排绘制 + 飞书卡片推送。
**不包含任何凭据**：Webhook / AppID / AppSecret 全部从环境变量读取，便于安全公开仓库。

环境变量：
    FEISHU_WEBHOOK       飞书自定义机器人 webhook（必填，否则不推送）
    FEISHU_APP_ID        飞书自建应用 App ID（上传图片用）
    FEISHU_APP_SECRET    飞书自建应用 App Secret（上传图片用）
"""

from __future__ import annotations

import os

from PIL import ImageFont

import requests

WEBHOOK_URL = os.environ.get("FEISHU_WEBHOOK", "")
APP_ID = os.environ.get("FEISHU_APP_ID", "")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
UPLOAD_URL = "https://open.feishu.cn/open-apis/im/v1/images"

_HERE = os.path.dirname(os.path.abspath(__file__))
_ZH_CANDIDATES = [
    os.path.join(_HERE, "SimHei.ttf"),
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
]
_EN_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def _first(paths):
    for p in paths:
        if os.path.exists(p):
            return p
    return paths[-1]


_ZH = _first(_ZH_CANDIDATES)
_EN = _first(_EN_CANDIDATES)


def F(sz, lang="zh"):
    return ImageFont.truetype(_ZH if lang == "zh" else _EN, sz)


def TS(draw, text, font):
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def DM(draw, x, y, text, zh_f, en_f, fill="black", anchor="la"):
    """中英文混排：按字符语言切 run，逐段绘制，返回 (宽, 高)。"""
    runs = []
    cl, cr = None, ""
    for ch in text:
        is_zh = ("\u4e00" <= ch <= "\u9fff") or ("\u3000" <= ch <= "\u303f") or ("\uff00" <= ch <= "\uffef")
        lang = "zh" if is_zh else "en"
        if lang == cl:
            cr += ch
        else:
            if cr:
                runs.append((cl, cr))
            cl, cr = lang, ch
    if cr:
        runs.append((cl, cr))
    tw, th = 0, 0
    for lang, run in runs:
        w, h = TS(draw, run, zh_f if lang == "zh" else en_f)
        tw += w
        th = max(th, h)
    if anchor == "ma":
        x -= tw // 2
    elif anchor == "ra":
        x -= tw
    cx = x
    for lang, run in runs:
        font = zh_f if lang == "zh" else en_f
        w, h = TS(draw, run, font)
        dy = (th - h) // 2
        draw.text((cx, y + dy), run, fill=fill, font=font)
        cx += w
    return tw, th


def get_token():
    if not (APP_ID and APP_SECRET):
        return ""
    try:
        r = requests.post(TOKEN_URL, json={"app_id": APP_ID, "app_secret": APP_SECRET}, timeout=15)
        return r.json().get("tenant_access_token", "")
    except Exception:
        return ""


def upload(path, token):
    if not token:
        return ""
    with open(path, "rb") as f:
        r = requests.post(UPLOAD_URL, headers={"Authorization": f"Bearer {token}"},
                          data={"image_type": "message"}, files={"image": f}, timeout=30)
    return r.json().get("data", {}).get("image_key", "")


def push_card(title, body, figs=(), template="blue"):
    """发飞书交互卡片：正文 + 若干本地图片。返回接口 JSON；未配置 webhook 时返回 {'skipped': ...}。"""
    if not WEBHOOK_URL:
        return {"skipped": "FEISHU_WEBHOOK 未配置"}
    keys = []
    tok = get_token()
    if tok:
        for p in figs:
            k = upload(p, tok)
            if k:
                keys.append(k)
    elements = [{"tag": "div", "text": {"tag": "lark_md", "content": body}}]
    for k in keys:
        elements.append({"tag": "img", "img_key": k, "alt": {"tag": "plain_text", "content": "图"}})
    card = {"config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": title}, "template": template},
            "elements": elements}
    r = requests.post(WEBHOOK_URL, json={"msg_type": "interactive", "card": card}, timeout=25)
    try:
        return r.json()
    except Exception:
        return {"status_code": r.status_code, "text": r.text[:200]}