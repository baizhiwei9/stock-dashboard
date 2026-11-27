#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QQ 邮箱通用收发工具
用法:
  python qq_mail_tool.py send --to "xxx@qq.com" --subject "主题" --content "正文" [--attachment "文件路径"]
  python qq_mail_tool.py list [--count 10] [--unread]
  python qq_mail_tool.py read --uid 123 [--save]
  python qq_mail_tool.py mark_read --uid 123
"""

import sys

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
import json
import smtplib
import imaplib
import email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import decode_header, Header
from email.utils import formataddr
from pathlib import Path

# ========== 配置区 ==========
EMAIL = _dh_secret('QQMAIL_USER')
AUTH_CODE = _dh_secret('QQMAIL_AUTH_CODE')
SENDER = "默尔根"
SMTP_SERVER = "smtp.qq.com"
SMTP_PORT = 465
IMAP_SERVER = "imap.qq.com"
IMAP_PORT = 993
# ============================


def decode_str(s):
    """解码邮件标题/发件人"""
    if not s:
        return ""
    parts = decode_header(s)
    result = []
    for text, charset in parts:
        if isinstance(text, bytes):
            result.append(text.decode(charset or "utf-8", errors="replace"))
        else:
            result.append(text)
    return "".join(result)


def send_mail(to, subject, content, html=False, attachment=None):
    """发送邮件"""
    msg = MIMEMultipart()
    msg["From"] = formataddr((str(Header(SENDER, 'utf-8')), EMAIL))
    msg["To"] = to
    msg["Subject"] = Header(subject, 'utf-8')

    content_type = "html" if html else "plain"
    msg.attach(MIMEText(content, content_type, "utf-8"))

    if attachment:
        from email.mime.base import MIMEBase
        from email import encoders
        p = Path(attachment)
        with open(p, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f"attachment; filename={p.name}")
        msg.attach(part)

    with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as server:
        server.login(EMAIL, AUTH_CODE)
        server.sendmail(EMAIL, [to], msg.as_string())
    print("✅ 邮件发送成功")


def list_mails(count=10, unread_only=False):
    """获取邮件列表"""
    with imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT) as imap:
        imap.login(EMAIL, AUTH_CODE)
        imap.select("INBOX")

        search_criteria = "UNSEEN" if unread_only else "ALL"
        status, data = imap.search(None, search_criteria)
        ids = data[0].split()[-count:] if data[0] else []

        print(f"📬 共找到 {len(ids)} 封邮件\n")
        for i, eid in enumerate(reversed(ids), 1):
            status, msg_data = imap.fetch(eid, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])
            subject = decode_str(msg["Subject"])
            sender = decode_str(msg.get("From", ""))
            date = msg.get("Date", "")
            print(f"[{i}] UID: {eid.decode()}")
            print(f"    主题: {subject}")
            print(f"    发件人: {sender}")
            print(f"    日期: {date}\n")


def read_mail(uid, save_attachment=False):
    """读取邮件正文"""
    save_dir = Path("./mail_attachments")
    if save_attachment:
        save_dir.mkdir(exist_ok=True)

    with imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT) as imap:
        imap.login(EMAIL, AUTH_CODE)
        imap.select("INBOX")

        status, msg_data = imap.fetch(uid, "(RFC822)")
        msg = email.message_from_bytes(msg_data[0][1])

        print(f"📧 主题: {decode_str(msg['Subject'])}")
        print(f"   发件人: {decode_str(msg.get('From', ''))}")
        print(f"   日期: {msg.get('Date', '')}\n")

        body = ""
        attachments = []
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain" and not part.get_filename():
                    body += part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="replace"
                    )
                elif part.get_filename():
                    filename = decode_str(part.get_filename())
                    attachments.append(filename)
                    if save_attachment:
                        filepath = save_dir / filename
                        with open(filepath, "wb") as f:
                            f.write(part.get_payload(decode=True))
                        print(f"💾 附件已保存: {filepath}")
        else:
            body = msg.get_payload(decode=True).decode(
                msg.get_content_charset() or "utf-8", errors="replace"
            )

        print("--- 正文 ---")
        print(body[:2000] if len(body) > 2000 else body)
        if attachments:
            print(f"\n📎 附件: {', '.join(attachments)}")


def mark_read(uid):
    """标记邮件为已读"""
    with imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT) as imap:
        imap.login(EMAIL, AUTH_CODE)
        imap.select("INBOX")
        imap.store(uid, "+FLAGS", "\\Seen")
    print(f"✅ UID {uid} 已标记为已读")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]
    args = sys.argv[2:]

    if cmd == "send":
        to = subject = content = None
        html = False
        attachment = None
        i = 0
        while i < len(args):
            if args[i] == "--to":
                to = args[i + 1]; i += 2
            elif args[i] == "--subject":
                subject = args[i + 1]; i += 2
            elif args[i] == "--content":
                content = args[i + 1]; i += 2
            elif args[i] == "--html":
                html = True; i += 1
            elif args[i] == "--attachment":
                attachment = args[i + 1]; i += 2
            else:
                i += 1
        if not all([to, subject, content]):
            print("❌ 缺少参数: --to, --subject, --content 必填")
            return
        send_mail(to, subject, content, html, attachment)

    elif cmd == "list":
        count = 10
        unread = False
        i = 0
        while i < len(args):
            if args[i] == "--count":
                count = int(args[i + 1]); i += 2
            elif args[i] == "--unread":
                unread = True; i += 1
            else:
                i += 1
        list_mails(count, unread)

    elif cmd == "read":
        uid = None
        save = False
        i = 0
        while i < len(args):
            if args[i] == "--uid":
                uid = args[i + 1]; i += 2
            elif args[i] == "--save":
                save = True; i += 1
            else:
                i += 1
        if not uid:
            print("❌ 缺少 --uid 参数")
            return
        read_mail(uid, save)

    elif cmd == "mark_read":
        uid = None
        i = 0
        while i < len(args):
            if args[i] == "--uid":
                uid = args[i + 1]; i += 2
            else:
                i += 1
        if not uid:
            print("❌ 缺少 --uid 参数")
            return
        mark_read(uid)

    else:
        print(f"❌ 未知命令: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
