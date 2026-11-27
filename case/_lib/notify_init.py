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

# ---- 统一配置（DataHub config / notify；真实值在本机 config.local.json，不入库）----
def _dh_secret(_name, _default=""):
    try:
        from data_hub import get_secret as _gs
        return _gs(_name, _default)
    except Exception:
        return _default


def _dh_cfg(_key, _default=None):
    try:
        from data_hub import cfg as _cfg
        return _cfg(_key, _default)
    except Exception:
        return _default


def _dh_tushare(_default=""):
    try:
        from data_hub import get_tushare_token as _gt
        return _gt() or _default
    except Exception:
        return _default
# ---------------------------------------------------------------------------

import os
import sys

APP = "stock-dashboard"
_PREFIX_ENV = "DASHBOARD_NOTIFY_SUBJECT_PREFIX"
_CHAT_ENV = "NOTIFY_APP_STOCK_DASHBOARD_FEISHU_CHAT_ID"
_CHAT_KEYS = ("stocks", "tugou", "tags")
_CHANNELS = ("feishu", "email")

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

__all__ = ["APP", "init", "subject", "webhook", "chat_id", "resolve_chat",
           "chat_hint", "active"]


def _profile_inputs():
    feishu = {"webhook_url": (os.environ.get("CASE_ALERT_WEBHOOK") or "").strip()}
    email = {
        "to": [x.strip() for x in str(_dh_secret("MAIL_DEFAULT_TO", "") or "").split(",")
               if x.strip()],
        "from": (_dh_secret("MAIL_USER", "") or "").strip(),
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


def resolve_chat(key: str = ""):
    """返回 ``(chat_id, source)``，供排查「这条到底发到哪个群」。

    取值顺序（``key`` 为空＝只要默认群）：

    1. 具名群环境变量 ``NOTIFY_APP_STOCK_DASHBOARD_FEISHU_CHAT_ID_<KEY>``；
    2. 具名群 secrets ``feishu.chats.<key>``；
    3. **回落默认群**（宁可发错群也要发）：默认群环境变量
       ``NOTIFY_APP_STOCK_DASHBOARD_FEISHU_CHAT_ID`` → app profile 的
       ``feishu.chat_id``（部署 ``notify.json``）→ secrets 的 ``feishu.chat_id``；
    4. 都没有 → ``("", "")``，由调用方如实报缺（不会静默换成别的应用）。
    """
    key = (key or "").strip().lower()
    if key:
        name = "%s_%s" % (_CHAT_ENV, key.upper().replace("-", "_"))
        val = (os.environ.get(name) or "").strip()
        if val:
            return val, "env:%s" % name
        val = _secret("feishu.chats.%s" % key)
        if val:
            return val, "secrets:feishu.chats.%s" % key
    val = (os.environ.get(_CHAT_ENV) or "").strip()
    if val:
        return val, "env:%s" % _CHAT_ENV
    val = _feishu_field("chat_id")
    if val:
        return val, "profile:apps.%s.feishu.chat_id" % APP
    val = _secret("feishu.chat_id")
    if val:
        return val, "secrets:feishu.chat_id"
    return "", ""


def chat_id(key: str = "", default: str = "") -> str:
    """飞书目标群 chat_id —— 唯一取值口径，仓库里不写死。

    具名群（``stocks`` / ``tugou`` / ``tags``）没配时**回落到默认群**：宁可发错
    群也必须把报告发出去。「这条到底发到哪个群」用 :func:`resolve_chat` 查。
    连默认群都没有时才返回 ``default``（通常是空串），由调用方如实报缺。
    """
    return resolve_chat(key)[0] or default


def chat_hint(key: str = "") -> str:
    """连默认群都没配时给部署侧的中文指引（只写字段名，不含任何真实 id）。"""
    key = (key or "").strip().lower()
    if key:
        env = "%s_%s" % (_CHAT_ENV, key.upper().replace("-", "_"))
        field = "feishu.chats.%s" % key
        missing = "具名群 %s 与默认群都没有" % key
    else:
        env = _CHAT_ENV
        field = "feishu.chat_id"
        missing = "默认群与具名群（%s）都没有" % " / ".join(_CHAT_KEYS)
    return ("未配置任何飞书群目标（%s）：可设环境变量 %s，或在 "
            "~/.config/stock-dashboard/secrets.json 写 %s / feishu.chat_id，或在 "
            "data_hub.notify 部署配置的 apps.%s.feishu.chat_id 里指定"
            % (missing, env, field, APP))


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
        print("webhook=%s" % ("有值" if webhook() else "缺失"))
        for k in ("",) + _CHAT_KEYS:
            cid, src = resolve_chat(k)
            print("chat[%-7s] %s ← %s" % (k or "default", "有值" if cid else "缺失",
                                         src or "未配置"))
        return 0
    sys.stderr.write("用法：notify_init.py [chat [key] | status]\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv))
