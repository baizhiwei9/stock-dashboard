#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""nowcast_daily 的邮件出口：dws 主通道 + 163 SMTP 回退。

为什么单独一个文件
------------------
任务契约把「发不发」交给任务自己，框架只声明 ``deliver.email``。这里只做
「把标题和正文发出去」，不做任何阈值判断——判断在 ``run.py``。

凭据一律走 ``case/_lib/app_secrets``（``~/.config/stock-dashboard/secrets.json``
或环境变量），仓库里没有明文。缺 ``email.sender`` / ``email.recipients`` 时
返回失败并说明缺哪项，不静默跳过。
"""
from __future__ import annotations

import os
import re
import shutil
import smtplib
import subprocess
import sys
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.normpath(os.path.join(HERE, "..", "_lib"))
if LIB not in sys.path:
    sys.path.insert(0, LIB)

import app_secrets  # noqa: E402

def _dws_cli() -> str:
    """dws CLI 路径：环境变量 > PATH > ~/.local/bin。

    写死 ``~/.local/bin/dws`` 在换机器时会静默退到 SMTP，邮件通道悄悄变了却
    没人知道；这里按优先级找得到哪个用哪个，全找不到返回空串（由 _dws 报错）。
    """
    for candidate in (os.environ.get("DWS_CLI"), shutil.which("dws"),
                      os.path.expanduser("~/.local/bin/dws")):
        if candidate and os.path.exists(candidate):
            return candidate
    return ""


DWS_CLI = _dws_cli()
SENDER_NAME = "DuMate 美国 Nowcast 日更"


def _recipients() -> list:
    """收件人：逗号/分号分隔，去空。"""
    raw = app_secrets.get("email.recipients", "") or ""
    return [item.strip() for item in re.split(r"[,;，；]", raw) if item.strip()]


def _dws(subject: str, body: str, sender: str, recipients: list) -> tuple:
    """dws 钉钉企业邮箱；返回 (ok, info)。"""
    if not os.path.exists(DWS_CLI):
        return False, "dws CLI 不存在"
    cmd = [DWS_CLI, "mail", "message", "send",
           "--from", sender, "--to", ",".join(recipients),
           "--subject", subject, "--content", body, "--format", "json"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except Exception as exc:  # noqa: BLE001
        return False, "dws 调用失败: %s" % str(exc)[:160]
    out = (proc.stdout or "").strip()
    if re.search(r'"success"\s*:\s*(true|"true")', out):
        return True, "dws 发送成功"
    return False, "dws 返回异常: %s" % ((proc.stderr or out).strip()[:160] or "空响应")


def _smtp(subject: str, body: str, sender: str, recipients: list) -> tuple:
    """163 SMTP 回退：465/994 SSL 优先，最后试配置端口的 STARTTLS。"""
    username = app_secrets.get("email.username", "") or sender
    password = app_secrets.get("email.password", "")
    if not password:
        return False, "未配置 email.password，SMTP 不可用"
    host = app_secrets.get("email.smtp_host", "smtp.163.com") or "smtp.163.com"
    configured = app_secrets.get_int("email.smtp_port", 465)

    message = MIMEText(body, "plain", "utf-8")
    message["Subject"] = str(Header(subject, "utf-8"))
    message["From"] = formataddr((str(Header(SENDER_NAME, "utf-8")), sender))
    message["To"] = ", ".join(recipients)

    attempts = [(host, 465, "ssl"), (host, 994, "ssl"), (host, configured, "starttls")]
    last = ""
    seen = set()
    for host_, port, mode in attempts:
        if (host_, port) in seen:
            continue
        seen.add((host_, port))
        try:
            if mode == "ssl":
                server = smtplib.SMTP_SSL(host_, port, timeout=45)
            else:
                server = smtplib.SMTP(host_, port, timeout=45)
                server.starttls()
            server.login(username, password)
            server.sendmail(username, recipients, message.as_string())
            server.quit()
            return True, "SMTP %s(%s) 发送成功" % (host_, port)
        except Exception as exc:  # noqa: BLE001
            last = "%s:%s %s" % (host_, port, str(exc)[:120])
    return False, "SMTP 全部失败：%s" % last


def send(subject: str, body: str, dry_run: bool = False) -> tuple:
    """发送一封纯文本邮件；返回 ``(ok, info)``。

    Args:
        subject: 邮件主题。
        body: 纯文本正文。
        dry_run: True 时只打印，不外发。

    Returns:
        tuple[bool, str]: 是否成功 + 一句可追溯的说明（不回显凭据）。
    """
    sender = app_secrets.get("email.sender", "")
    recipients = _recipients()
    if dry_run:
        print("[邮件][dry] %s -> %s" % (subject, ", ".join(recipients) or "（未配置收件人）"))
        return True, "dry-run"
    if not sender or not recipients:
        return False, "未配置 email.sender / email.recipients，跳过发送"

    ok, info = _dws(subject, body, sender, recipients)
    if ok:
        return True, info
    ok2, info2 = _smtp(subject, body, sender, recipients)
    if ok2:
        return True, info2
    return False, "%s ｜ %s" % (info, info2)
