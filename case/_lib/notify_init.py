# -*- coding: utf-8 -*-
"""Stock Dashboard 的通知初始化模块 —— 绑定本应用自己的飞书群与邮件标题。

框架的告警出口 ``case/_lib/alert.py`` 与各 case 的发信入口在导入时调一次：

    import notify_init
    notify_init.init()

    from data_hub import notify
    notify.send("标题", "正文")          # → 本应用的群 + 本应用的邮件标题

凭据来源（不进仓库）
-------------------
· 飞书：``CASE_ALERT_WEBHOOK``（框架既有口径，群机器人 webhook）
· 其余：部署配置 ``apps.stock-dashboard``，或环境变量
  ``NOTIFY_APP_STOCK_DASHBOARD_FEISHU_CHAT_ID`` / ``..._EMAIL_TO`` / ``..._EMAIL_FROM``

**未配齐不激活**：本应用自己的飞书与邮件没配全时返回 ``activated=False``，
``notify.send`` 继续走旧版全局口径；``alert.py`` 也仍优先用 ``CASE_ALERT_WEBHOOK``。
邮件标题默认不加前缀；要区分时设 ``DASHBOARD_NOTIFY_SUBJECT_PREFIX``。
"""
from __future__ import annotations

import os
import sys

APP = "stock-dashboard"
_PREFIX_ENV = "DASHBOARD_NOTIFY_SUBJECT_PREFIX"
_CHANNELS = ("feishu", "email")

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

__all__ = ["APP", "init", "subject", "webhook", "chat_id", "active"]


def _profile_inputs():
    feishu = {"webhook_url": (os.environ.get("CASE_ALERT_WEBHOOK") or "").strip()}
    email = {
        "to": [x.strip() for x in str(os.environ.get("DASHBOARD_EMAIL_TO") or "").split(",")
               if x.strip()],
        "from": (os.environ.get("DASHBOARD_EMAIL_FROM") or "").strip(),
    }
    return feishu, email


def init(**overrides):
    """注册本应用的绑定（未配齐则不激活，返回里带 missing 说明缺什么）。"""
    try:
        from data_hub.notify import apps
    except Exception as exc:
        return {"app": APP, "activated": False,
                "reason": "data_hub.notify 不可用：%s" % exc}
    feishu, email = _profile_inputs()
    kw = dict(overrides)
    kw.setdefault("subject_prefix", (os.environ.get(_PREFIX_ENV) or "").strip() or None)
    return apps.init_if_ready(APP, feishu=feishu, email=email,
                              channels=_CHANNELS, **kw)


def active() -> bool:
    try:
        from data_hub.notify import apps
    except Exception:
        return False
    return apps.current() == APP


def _feishu_field(name: str, default: str = "") -> str:
    try:
        from data_hub.notify import apps
    except Exception:
        return default
    return ((apps.profile(APP).get("feishu") or {}).get(name) or "") or default


def webhook(default: str = "") -> str:
    """本应用的群机器人 webhook（部署配置优先，其次调用方默认值）。"""
    return _feishu_field("webhook_url", default)


def chat_id(default: str = "") -> str:
    return _feishu_field("chat_id", default)


def subject(title: str) -> str:
    """按本应用口径生成邮件标题（没配前缀时原样返回）。"""
    try:
        from data_hub.notify import apps
    except Exception:
        return title
    return apps.subject(title, APP)