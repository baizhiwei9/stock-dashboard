#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""市场融资风险扫描日报 · 邮件发送模块

邮件通道与凭据统一走 config/secrets.json（不复制明文进本工程）：
  主通道：dws 钉钉企业邮箱（dws CLI）
  回退通道：163 SMTP
两通道均失败时整体重试一次（默认 retries=1）。

调用方：run_daily.py 按红标项数分级调用（≥1 发提醒，≥2 发强提醒）。

独立入口：
  import email_push
  ok = email_push.send_email(subject, text_body, html_body=None)

CLI:
  python email_push.py --subject "..." --body-file body.txt [--html-file body.html] [--dry-run]
"""
import argparse
import os
import smtplib
import subprocess
import sys
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
_LIB = os.path.join(_ROOT, "case", "_lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

import app_secrets  # noqa: E402

DWS_CLI = os.path.expanduser("~/.local/bin/dws")
EMAIL_FROM = app_secrets.get("email.sender", "")
EMAIL_TO = app_secrets.get("email.recipients", "")
EMAIL_HOST = app_secrets.get("email.smtp_host", "smtp.163.com")
EMAIL_PORT = app_secrets.get_int("email.smtp_port", 25)
EMAIL_USER = app_secrets.get("email.username", "")


def get_auth_code():
    """SMTP 授权码：只从 config/secrets.json / 环境变量取。"""
    return app_secrets.get("email.password", "")

SENDER_NAME = "DuMate市场融资风险扫描日报"


def send_via_dws(subject, text_body):
    """主通道：dws 钉钉企业邮箱。成功判定：stdout 含 "success": "true"。"""
    try:
        cmd = [DWS_CLI, "mail", "message", "send",
               "--from", EMAIL_FROM, "--to", EMAIL_TO,
               "--subject", subject, "--content", text_body,
               "--format", "json"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        out = proc.stdout.strip()
        if '"success": "true"' in out or '"success":"true"' in out:
            print(f"[邮件] dws 主通道发送成功 -> {EMAIL_TO}")
            return True
        print(f"[邮件] dws 返回异常: {(proc.stderr or out)[:200]}")
        return False
    except FileNotFoundError:
        print(f"[邮件] dws CLI 不存在: {DWS_CLI}")
        return False
    except Exception as e:
        print(f"[邮件] dws 调用失败: {str(e)[:200]}")
        return False


def send_via_smtp(subject, html_body):
    """回退通道：163 SMTP。"""
    code = get_auth_code()
    if not code:
        print("[邮件] 未取到 163 授权码，回退通道不可用")
        return False
    try:
        msg = MIMEText(html_body, "html", "utf-8")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = formataddr((str(Header(SENDER_NAME, "utf-8")), EMAIL_USER))
        msg["To"] = EMAIL_TO
        server = smtplib.SMTP(EMAIL_HOST, EMAIL_PORT, timeout=30)
        server.starttls()
        server.login(EMAIL_USER, code)
        server.sendmail(EMAIL_USER, [EMAIL_TO], msg.as_string())
        server.quit()
        print(f"[邮件] 163 SMTP 回退通道发送成功 -> {EMAIL_TO}")
        return True
    except Exception as e:
        print(f"[邮件] 163 SMTP 发送失败: {str(e)[:200]}")
        return False


def send_email(subject, text_body, html_body=None, retries=1):
    """邮件发送：主通道 dws，失败回退 163 SMTP；双通道均失败后整体重试 retries 次。"""
    if not (EMAIL_FROM and EMAIL_TO):
        print("[邮件] 未配置 email.sender/email.recipients（见 config/secrets.json），跳过发送")
        return False
    for attempt in range(retries + 1):
        if send_via_dws(subject, text_body):
            return True
        print("[邮件] 主通道失败，尝试 163 SMTP 回退...")
        if send_via_smtp(subject, html_body or text_body):
            return True
        if attempt < retries:
            print(f"[邮件] 双通道失败，重试第 {attempt + 1} 次...")
    print("[邮件] 发送失败（双通道均不可用）")
    return False


def main():
    ap = argparse.ArgumentParser(description="市场融资风险扫描日报邮件发送入口")
    ap.add_argument("--subject", required=True, help="邮件主题")
    ap.add_argument("--body", default="", help="纯文本正文（与 --body-file 二选一）")
    ap.add_argument("--body-file", default="", help="纯文本正文本文件（dws 主通道用）")
    ap.add_argument("--html-file", default="", help="HTML 正文本文件（SMTP 回退用）")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不实际发送")
    args = ap.parse_args()

    text_body = args.body
    if args.body_file:
        with open(args.body_file, encoding="utf-8") as f:
            text_body = f.read()
    html_body = ""
    if args.html_file and os.path.exists(args.html_file):
        with open(args.html_file, encoding="utf-8") as f:
            html_body = f.read()

    if args.dry_run:
        print(f"[dry-run] 主题: {args.subject}")
        print(f"[dry-run] 正文长度: text={len(text_body)}, html={len(html_body)} -> {EMAIL_TO}")
        sys.exit(0)

    ok = send_email(args.subject, text_body, html_body or None)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()