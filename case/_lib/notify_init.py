# -*- coding: utf-8 -*-
"""Stock Dashboard 的通知初始化模块 —— 绑定本应用自己的飞书群与邮件标题。

框架的告警出口 ``case/_lib/alert.py`` 与各 case 的发信入口在导入时调一次：

    import notify_init
    notify_init.init()

    from data_hub import notify
    notify.send("标题", "正文")          # → 本应用的群 + 本应用的邮件标题

凭据来源（不进仓库）
-------------------
· 飞书 webhook：``CASE_ALERT_WEBHOOK``（框架既有口径，群机器人 webhook）
· 飞书群目标（chat_id）：``chat_id(key)`` —— 统一走 DataHub 通知模块的应用级口径，
  再退本应用 secrets（``feishu.chats.<key>`` / ``feishu.chat_id``）；仓库里不写死
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
_CHAT_ENV = "NOTIFY_APP_STOCK_DASHBOARD_FEISHU_CHAT_ID"
_CHANNELS = ("feishu", "email")

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

__all__ = ["APP", "init", "subject", "webhook", "chat_id", "chat_hint", "active"]


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


def _secret(dotted: str) -> str:
    """本应用 secrets.json 的点号取值（读不到返回空串，不抛）。"""
    try:
        import app_secrets
    except Exception:
        return ""
    try:
        return (app_secrets.get(dotted, "") or "").strip()
    except Exception:
        return ""


def webhook(default: str = "") -> str:
    """本应用的群机器人 webhook（部署配置优先，其次调用方默认值）。"""
    return _feishu_field("webhook_url", default)


def chat_id(key: str = "", default: str = "") -> str:
    """飞书目标群 chat_id —— 唯一取值口径，仓库里不写死。

    取值顺序（``key`` 为空＝本应用默认群）：

    1. 环境变量 ``NOTIFY_APP_STOCK_DASHBOARD_FEISHU_CHAT_ID``（具名群加后缀
       ``_<KEY>``，如 ``..._CHAT_ID_TUGOU``）—— DataHub 通知模块的应用级口径；
    2. 具名群（``key`` 非空）：本应用 secrets 的 ``feishu.chats.<key>``；
    3. 默认群：``data_hub.notify`` app profile 的 ``feishu.chat_id``
       （部署 ``notify.json`` 的 ``apps.stock-dashboard`` 段）；
    4. 默认群：本应用 secrets 的 ``feishu.chat_id``；
    5. ``default``。

    ``key`` 非空时**不回落到默认群** —— 把土狗群 / 标签群误发到主群，比
    如实报缺更难发现。
    """
    key = (key or "").strip().lower()
    names = ["%s_%s" % (_CHAT_ENV, key.upper().replace("-", "_"))] if key else [_CHAT_ENV]
    for name in names:
        val = (os.environ.get(name) or "").strip()
        if val:
            return val
    if key:
        return _secret("feishu.chats.%s" % key) or default
    return _feishu_field("chat_id") or _secret("feishu.chat_id") or default


def chat_hint(key: str = "") -> str:
    """缺群目标时给部署侧的中文指引（只写字段名，不含任何真实 id）。"""
    env = _CHAT_ENV + (("_" + key.upper().replace("-", "_")) if key else "")
    where = ("feishu.chats.%s" % key) if key else "feishu.chat_id"
    return ("未配置飞书群目标 %s：可设环境变量 %s，或在 "
            "~/.config/stock-dashboard/secrets.json 写 %s，或在 data_hub.notify "
            "部署配置的 apps.%s.feishu.chat_id 里指定" % (key or "default", env, where, APP))


def subject(title: str) -> str:
    """按本应用口径生成邮件标题（没配前缀时原样返回）。"""
    try:
        from data_hub.notify import apps
    except Exception:
        return title
    return apps.subject(title, APP)


def _cli(argv) -> int:
    """命令行入口 —— 给 shell 与 modules.json 取群目标用。

        python3 case/_lib/notify_init.py chat [key]   # 打印 chat_id；缺配时退出码 1
        python3 case/_lib/notify_init.py status       # 只看通道是否有值
    """
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "chat":
        key = argv[2] if len(argv) > 2 else ""
        val = chat_id(key)
        if val:
            print(val)
            return 0
        sys.stderr.write(chat_hint(key) + "\n")
        return 1
    if cmd == "status":
        print("app=%s active=%s" % (APP, active()))
        print("webhook=%s chat_id=%s" % ("有值" if webhook() else "缺失",
                                         "有值" if chat_id() else "缺失"))
        return 0
    sys.stderr.write("用法：notify_init.py [chat [key] | status]\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv))