"""飞书卡片构建与推送（债券日报）。

安全约定：仓库内不保存任何 webhook / app secret。
- 优先读环境变量 ``FEISHU_WEBHOOK``（自建机器人 webhook，直接 POST）；
- 未配置时回退到既有 ``feishu_pusher`` 模块（目录由 ``BOND_FEISHU_PUSHER_DIR``
  指定，默认沿用历史位置的既有凭据文件），保持与迁移前完全一致的推送行为；
- 两者都不可用时返回 False，由调用方打印失败，不伪造「已推送」。
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, Optional

DEFAULT_PUSHER_DIR = ("/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/"
                      "ses_gffe5febb5eaeeffe0mTIpBeu5w0Nq0")


# ========== 卡片构件（与原日报实现完全一致的结构） ==========
def make_header(title: str, template: str = "blue") -> dict:
    return {"title": {"tag": "plain_text", "content": title}, "template": template}


def make_text_element(content: str, tag: str = "lark_md") -> dict:
    return {"tag": "div", "text": {"tag": tag, "content": content}}


def make_divider() -> dict:
    return {"tag": "hr"}


def make_note(content: str) -> dict:
    return {"tag": "note", "elements": [{"tag": "plain_text", "content": content}]}


def _pusher():
    """加载既有 feishu_pusher（凭据只在运行环境/既有目录，不入库）。"""
    path = os.environ.get("BOND_FEISHU_PUSHER_DIR") or DEFAULT_PUSHER_DIR
    if path and os.path.isdir(path) and path not in sys.path:
        sys.path.insert(0, path)
    try:
        import feishu_pusher  # noqa: WPS433
        return feishu_pusher
    except Exception:
        return None


def send_feishu_card(card: Dict[str, Any]) -> bool:
    """推送交互卡片。返回是否真的推送成功。"""
    payload = {"msg_type": "interactive", "card": card}
    webhook = os.environ.get("FEISHU_WEBHOOK")
    if webhook:
        try:
            import requests  # noqa: WPS433
            resp = requests.post(webhook, json=payload, timeout=15)
            return bool(resp.ok)
        except Exception as err:
            print(f"[push] webhook 推送失败: {err}", flush=True)
            return False
    mod = _pusher()
    if mod is not None and hasattr(mod, "send_feishu_card"):
        try:
            return bool(mod.send_feishu_card(card))
        except Exception as err:
            print(f"[push] feishu_pusher 推送失败: {err}", flush=True)
            return False
    print("[push] 未配置 FEISHU_WEBHOOK，且既有 feishu_pusher 不可用，未推送", flush=True)
    return False


def pusher_available() -> Optional[str]:
    """返回当前推送通道名，便于日志标注。"""
    if os.environ.get("FEISHU_WEBHOOK"):
        return "webhook"
    return "feishu_pusher" if _pusher() is not None else None