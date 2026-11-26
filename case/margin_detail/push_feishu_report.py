# -*- coding: utf-8 -*-
"""把「市场融资风险扫描日报」推送到飞书（复用既有 feishu_push 卡片工具，不复制密钥）

报告已拆为两份，本脚本按传入的卡片文件逐份推送：
    python3 push_feishu_report.py --cards-file feishu_cards_margin.json   # 两融专项
    python3 push_feishu_report.py --cards-file feishu_cards_other.json    # 资金流与宽度
未传 --cards-file 时默认 data/feishu_cards.json（兼容旧调用）。

统一「机会 / 风险」重点突出标记（与报告正文保持一致）：
- 卡片头部颜色：读卡片文件每张卡的 color 字段（risk=red / opp=green / 中性=orange，缺省 blue）。
- 含 image 键的卡片：图文卡（先上传「全部子图合并大图」取 image_key，再发彩色 header + img + lark_md 卡片）；
  失败自动回退为「图卡 + 文字卡」。
- 无 image 键的卡片：同样发彩色 header + lark_md 卡片；失败回退 fp.push_text_card。
本脚本不改动共享 feishu_push.py，彩色卡在本地自建后复用 fp._send_card 发送。
"""
import os, sys, json
D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
# feishu_push.py 在 case/tech_consensus（原指向的 ses_.../tech_consensus_daily 已不存在）。
# 用相对本文件的方式定位，换机/换盘都不用改。
FSH_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "tech_consensus")
sys.path.insert(0, FSH_DIR)
import feishu_push as fp

CARDS_NAME = "feishu_cards.json"
for _i, _a in enumerate(sys.argv):
    if _a == "--cards-file" and _i + 1 < len(sys.argv):
        CARDS_NAME = sys.argv[_i + 1]
CARDS_PATH = CARDS_NAME if os.path.isabs(CARDS_NAME) else os.path.join(D, os.path.basename(CARDS_NAME))

app_id, app_secret, webhook = fp._config()
token = fp._tenant_token(app_id, app_secret)
cards = json.load(open(CARDS_PATH, encoding="utf-8"))

img_cache = {}          # 同一路径只上传一次，image_key 可复用


def _header(title, color):
    """彩色卡片头部：风险=red / 机会=green / 中性=orange / 缺省=blue"""
    return {"template": color or "blue",
            "title": {"tag": "plain_text", "content": title}}


ok = 0
for c in cards:
    title = c.get("title")
    content = c.get("content")
    img = c.get("image")
    color = c.get("color")
    if img and os.path.exists(img):
        if img not in img_cache:
            img_cache[img] = fp.upload_image(token, img)
        key = img_cache[img]
        card = {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": _header(title, color),
                "elements": [
                    {"tag": "img", "img_key": key,
                     "alt": {"tag": "plain_text", "content": "ETF存量资金MACD全部子图合并大图"}},
                    {"tag": "div", "text": {"tag": "lark_md", "content": content}},
                ],
            },
        }
        try:
            fp._send_card(webhook, card)
            print("sent(图文, %s):" % (color or "blue"), title)
        except Exception as e:
            print("图文卡失败，回退为图卡+文字卡:", e)
            fp.push_image_card(webhook, token, key, title)
            fp.push_text_card(webhook, token, title, content)
            print("sent(图文回退):", title)
    else:
        card = {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": _header(title, color),
                "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": content}}],
            },
        }
        try:
            fp._send_card(webhook, card)
            print("sent(文字, %s):" % (color or "blue"), title)
        except Exception as e:
            print("彩色文字卡失败，回退普通文字卡:", e)
            fp.push_text_card(webhook, token, title, content)
            print("sent(文字回退):", title)
    ok += 1
print("DONE", ok, "cards from", CARDS_PATH)