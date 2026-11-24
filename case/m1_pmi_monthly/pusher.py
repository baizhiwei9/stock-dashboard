# -*- coding: utf-8 -*-
"""双通道推送脚本：飞书 webhook + 邮件（钉钉企业邮箱主通道，163 SMTP 回退）"""
import argparse
import os
import re
import smtplib
import subprocess
import sys
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import (DWS_CLI, EMAIL_FROM, EMAIL_HOST, EMAIL_PORT, EMAIL_TO,
                    EMAIL_USER, FEISHU_WEBHOOK, get_auth_code)


def build_feishu_card(title, md_content, max_len=3800):
    """构建飞书交互式卡片（interactive card）结构。

    标题置于卡片 header，正文以 lark_md 元素承载；去掉正文首行 H1 避免与标题重复。
    """
    body = re.sub(r"^#\s*.*\n?", "", md_content, count=1).strip()
    if len(body) > max_len:
        body = body[:max_len] + "\n\n…（全文见邮件）"
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": title},
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": body}}
        ],
    }


def send_feishu_card(title, md_content, max_len=3800):
    """飞书交互式卡片消息（webhook，msg_type=interactive）。"""
    if not FEISHU_WEBHOOK:
        print("[飞书] 未配置 webhook，跳过")
        return False
    payload = {"msg_type": "interactive",
               "card": build_feishu_card(title, md_content, max_len=max_len)}
    try:
        r = requests.post(FEISHU_WEBHOOK, json=payload, timeout=30)
        res = r.json()
        if res.get("code") == 0 or res.get("StatusCode") == 0:
            print("[飞书] 交互式卡片发送成功")
            return True
        print("[飞书] 返回错误:", str(res)[:300])
        return False
    except Exception as e:
        print("[飞书] 发送失败:", str(e)[:200])
        return False


def send_via_dws(subject, markdown_body):
    """主通道：钉钉企业邮箱（dws CLI）。"""
    try:
        cmd = [DWS_CLI, "mail", "message", "send",
               "--from", EMAIL_FROM, "--to", EMAIL_TO,
               "--subject", subject, "--content", markdown_body,
               "--format", "json"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        out = proc.stdout.strip()
        if '"success": "true"' in out or '"success":"true"' in out:
            print("[邮件] dws 主通道发送成功 ->", EMAIL_TO)
            return True
        print(f"[邮件] dws 返回异常: {(proc.stderr or out)[:200]}")
        return False
    except FileNotFoundError:
        print(f"[邮件] dws CLI 不存在: {DWS_CLI}")
        return False
    except Exception as e:
        print("[邮件] dws 调用失败:", str(e)[:200])
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
        msg["From"] = formataddr((str(Header("DuMate宏观月度报告", "utf-8")), EMAIL_USER))
        msg["To"] = EMAIL_TO
        server = smtplib.SMTP(EMAIL_HOST, EMAIL_PORT, timeout=30)
        server.starttls()
        server.login(EMAIL_USER, code)
        server.sendmail(EMAIL_USER, [EMAIL_TO], msg.as_string())
        server.quit()
        print("[邮件] 163 SMTP 回退通道发送成功 ->", EMAIL_TO)
        return True
    except Exception as e:
        print("[邮件] 163 SMTP 发送失败:", str(e)[:200])
        return False


def send_email(subject, markdown_body, html_body=None):
    """邮件发送：主通道 dws，失败回退 163 SMTP。"""
    if not (EMAIL_FROM and EMAIL_TO):
        print("[邮件] 未配置 email.sender/email.recipients（见 config/secrets.json），跳过发送")
        return False
    if send_via_dws(subject, markdown_body):
        return True
    print("[邮件] 主通道失败，尝试 163 SMTP 回退...")
    return send_via_smtp(subject, html_body or markdown_body)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", required=True, help="markdown 报告路径")
    ap.add_argument("--html", default="", help="html 报告路径（邮件回退通道用）")
    ap.add_argument("--feishu-md", default="", help="飞书卡片专用精简摘要路径（缺省用 --md）")
    ap.add_argument("--feishu-only", action="store_true")
    ap.add_argument("--email-only", action="store_true")
    args = ap.parse_args()

    with open(args.md, encoding="utf-8") as f:
        md = f.read()
    html_body = ""
    if args.html and os.path.exists(args.html):
        with open(args.html, encoding="utf-8") as f:
            html_body = f.read()

    # 标题（首行）
    first = md.split("\n")[0].strip()
    title = re.sub(r"^#\s*", "", first) or "中国宏观月度展望"
    subject = f"[宏观月度展望] {title}"

    # 飞书消息：交互式卡片，正文取摘要（避免长文被截断丢失关键结论）
    feishu_body = md
    if args.feishu_md and os.path.exists(args.feishu_md):
        with open(args.feishu_md, encoding="utf-8") as f:
            feishu_body = f.read()
    ok = True
    if not args.email_only:
        ok &= send_feishu_card(title, feishu_body, max_len=4200)
    if not args.feishu_only:
        ok &= send_email(subject, md, html_body)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()