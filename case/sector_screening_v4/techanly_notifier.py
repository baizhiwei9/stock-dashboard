#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""techAnly-summary 的本机通知通道（**本机私有，不进公开库**）
=====================================================================
公开库 `techAnly-summary` 只负责触发预警，不内置任何账号/路径；
本文件是它在本机的实现：库 emit 时调用这里注册的通道。

用法（跨进程，库会自动 import 本文件并执行 register）：

    export TECHANLY_NOTIFIERS=/Coze/Drive/蓁宝/所有对话/主对话/techanly_notifier.py
    python3 charts/chan_fractal.py --stack ... --alert-email
    python3 charts/rmt_rc.py --alert --email

当前通道（环境变量 TECHANLY_NOTIFY_CHANNELS 选，逗号分隔，默认 email）：
  email   163 → QQ 统一出口 `email_sender.send_email`（环流日报同款）
  feishu  工作区 `notifier.py`（飞书群机器人，带图则走卡片）

`email_sender` 会把完整收件人打进 stdout，这里**整体吞掉**，只回显脱敏行。
"""
from __future__ import annotations

import contextlib
import io
import os
import re
import sys

_EMAIL_DIR = "/Coze/Drive/蓁宝/所有对话/主对话"
_REPO = os.environ.get("TECHANLY_HOME") or (
    "/root/.coze/agents/7649998182050545974/workspace/techAnly_合并多尺度交易/techAnly-summary")
_WORKSPACE = "/root/.coze/agents/7649998182050545974/workspace"

# 库在 sys.path 上时直接 import；单独跑本文件时兜底加库路径
try:
    import notify                                            # noqa: F401
except Exception:                                            # noqa: BLE001
    _core = os.path.join(_REPO, "misc", "core")
    if os.path.isdir(_core) and _core not in sys.path:
        sys.path.insert(0, _core)
    import notify

_EMAIL_RX = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _mask(addr: str) -> str:
    s = str(addr or "").strip()
    if "@" not in s:
        return s or "—"
    local, _, domain = s.partition("@")
    if len(local) <= 2:
        return local[:1] + "***@" + domain
    if len(local) <= 4:
        return local[:1] + "***" + local[-1:] + "@" + domain
    return local[:3] + "***" + local[-3:] + "@" + domain


def _scrub(text) -> str:
    return _EMAIL_RX.sub(lambda m: _mask(m.group(0)), str(text))


def _flush(buf: io.StringIO, tag: str, tail: int = 3) -> None:
    lines = [x for x in buf.getvalue().splitlines() if x.strip()]
    for line in lines[-tail:]:
        print("[techanly-notifier] %s %s" % (tag, _scrub(line).strip()))


# ═══════════════════════════ 1) 邮件（163 → QQ）═══════════════════════════
def _email(event) -> bool:
    if _EMAIL_DIR not in sys.path:
        sys.path.insert(0, _EMAIL_DIR)
    try:
        from email_sender import send_email
    except Exception as e:                                   # noqa: BLE001
        print("[techanly-notifier] email_sender 不可用：%s" % _scrub(e)[:160])
        return False

    to_addr = (event.meta or {}).get("to") or None
    heads = [str(f) for f in (event.attachments or ()) if f and os.path.exists(f)]
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            ok = send_email(
                subject=event.subject,
                text=event.body,
                source=event.source or "techAnly",
                to_addr=to_addr,
                attachments=heads or None,
                dry_run=event.dry_run,
            )
    except Exception as e:                                   # noqa: BLE001
        _flush(buf, "email:")
        print("[techanly-notifier] ❌ email 异常：%s" % _scrub(e)[:160])
        return False
    _flush(buf, "email:")
    return bool(ok)


# ═══════════════════════════ 2) 飞书 ═══════════════════════════
_IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")


def _feishu(event) -> bool:
    path = os.path.join(_WORKSPACE, "notifier.py")
    if not os.path.isfile(path):
        print("[techanly-notifier] 找不到 %s" % path)
        return False
    if _WORKSPACE not in sys.path:
        sys.path.insert(0, _WORKSPACE)
    try:
        import importlib.util
        if "notifier" in sys.modules:
            fm = sys.modules["notifier"]
        else:
            spec = importlib.util.spec_from_file_location("notifier", path)
            fm = importlib.util.module_from_spec(spec)
            sys.modules["notifier"] = fm
            spec.loader.exec_module(fm)
    except Exception as e:                                   # noqa: BLE001
        print("[techanly-notifier] notifier 不可用：%s" % _scrub(e)[:160])
        return False

    imgs = [str(f) for f in (event.attachments or ())
            if str(f).lower().endswith(_IMG_EXT) and os.path.exists(str(f))]
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            if imgs:
                fm.send_feishu_message_with_image(
                    event.subject, event.body, imgs[0])
            else:
                fm.send_feishu_message(
                    event.subject, event.body, header_prefix="【技术分析汇总】")
    except Exception as e:                                   # noqa: BLE001
        _flush(buf, "feishu:")
        print("[techanly-notifier] ❌ feishu 异常：%s" % _scrub(e)[:160])
        return False
    _flush(buf, "feishu:")
    return True


# ═══════════════════════════ 注册 ═══════════════════════════
_CHANNELS = {
    "email": _email,
    "feishu": _feishu,
}


def _wanted() -> list:
    raw = os.environ.get("TECHANLY_NOTIFY_CHANNELS", "email")
    out = [x.strip().lower() for x in str(raw).split(",") if x.strip()]
    return [x for x in out if x in _CHANNELS] or ["email"]


for _name in _wanted():
    notify.register(_name, _CHANNELS[_name])

print("[techanly-notifier] 已注册通道：%s" % ", ".join(_wanted()))
