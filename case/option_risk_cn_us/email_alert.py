#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""中美期权风险释放汇总 · 邮件提醒模块（A股红灯 / 偏度分位 / GEX·Flip 越阈触发）

邮件通道与凭据统一走 config/secrets.json（不复制明文进本工程）：
  主通道：dws 钉钉企业邮箱（dws CLI，支持 --inline-attachment 内嵌图片）
  回退通道：163 SMTP（multipart/related，cid 内嵌图片）
两通道均失败时整体重试一次（默认 retries=1）。

成功判定与端口
--------------
· 主通道以 CLI 返回 JSON 的 success 布尔值为准（兼容字符串 "true"）：dws 返回
  {"success": true, "messageId": ...} 时即判成功，避免把真成功误判为失败并重复外发。
· 回退通道按 465 SSL → 994 SSL → 配置端口 STARTTLS 依次尝试（沙箱内 25 端口不可达）。
· 发送失败原因写入 LAST_ERROR，供调用方在飞书消息侧通知。

带图约定
--------
images: 本地图片路径列表（建议顺序 = 正文中希望出现的顺序）。
  · 主通道 dws：正文用占位符 [inline:文件名] 引用内联图；若正文未出现该占位符，
    自动追加到正文末尾，并逐张加 --inline-attachment（CLI 自动生成 cid）。
  · 回退通道 SMTP：HTML 正文用 <img src="cid:文件名"> 引用；若未出现，自动追加到正文末尾，
    并按文件名作为 Content-ID 内联。两通道文件名均取 basename。

独立可调用入口：
  import email_alert
  ok = email_alert.send_email(subject, text_body, html_body=None, images=[...])
  email_alert.notify_feishu("正文", title="标题")   # 失败时在消息侧通知

CLI:
  python email_alert.py --subject "..." --body-file body.txt [--html-file body.html]
                        [--image a.png --image b.png] [--dry-run]
"""
import argparse
import html as _html
import json
import os
import re
import smtplib
import subprocess
import sys
from email.header import Header
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, make_msgid

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

SENDER_NAME = "DuMate中美期权风险释放汇总"
SMTP_TIMEOUT = 60

# 通道状态（供失败时在飞书消息侧通知取用；不涉及任何凭据）
LAST_DWS_ERR = ""
LAST_SMTP_ERR = ""
LAST_ERROR = ""


def _img_name(p):
    return os.path.basename(p)


def _dws_ok(out):
    """dws CLI 成功判定：优先解析 JSON 的 success 字段，兼容历史上字符串写法。

    dws 实际返回 pretty-print JSON，success 为布尔 true：
      {"arguments": [], "messageId": "...", "result": {...}, "success": true}
    早期实现按字符串 '"success": "true"' 匹配，无法识别布尔 true，会把成功误判为失败。
    """
    if not out:
        return False
    try:
        j = json.loads(out)
    except Exception:
        j = None
    if isinstance(j, dict) and "success" in j:
        v = j["success"]
        if v is True or (isinstance(v, str) and v.strip().lower() == "true"):
            return True
        return False
    flat = re.sub(r"\s+", "", out)
    return '"success":true' in flat or "success=true" in flat


def _dws_msgid(out):
    """从 dws 返回中取 messageId（仅用于日志追溯）。"""
    try:
        j = json.loads(out)
        if isinstance(j, dict):
            res = j.get("result") if isinstance(j.get("result"), dict) else {}
            for k in ("messageId", "internetMessageId"):
                v = j.get(k) or res.get(k)
                if v:
                    return str(v)
    except Exception:
        pass
    m = re.search(r'"messageId"\s*:\s*"([^"]+)"', out or "")
    return m.group(1) if m else ""


def _dws_content(text_body, images):
    """主通道正文：确保每张内联图都有 [inline:文件名] 占位符（缺失则追加末尾）。"""
    content = text_body or ""
    for p in images or []:
        tag = "[inline:%s]" % _img_name(p)
        if tag not in content:
            content += "\n\n" + tag
    return content


def _smtp_html(html_body, text_body, images):
    """回退通道 HTML 正文：确保每张图都有 <img src='cid:文件名'>（缺失则追加末尾）。"""
    doc = html_body or ("<pre style='font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif'>"
                        "%s</pre>" % _html.escape(text_body or ""))
    for p in images or []:
        cid = _img_name(p)
        if ("cid:%s" % cid) not in doc:
            doc += "<div><img src='cid:%s' alt='%s' style='max-width:100%%;'></div>" % (cid, cid)
    return doc


def _smtp_candidates():
    """163 发送候选：465/994 SSL 优先，最后保留配置端口 STARTTLS（兼容配置变更）。"""
    raw = [(EMAIL_HOST, 465, "ssl"), (EMAIL_HOST, 994, "ssl"),
           (EMAIL_HOST, EMAIL_PORT, "starttls")]
    out, seen = [], set()
    for host, port, mode in raw:
        key = (host, int(port))
        if key in seen:
            continue
        seen.add(key)
        out.append((host, int(port), mode))
    return out


def send_via_dws(subject, text_body, images=None, dry_run=False):
    """主通道：dws 钉钉企业邮箱（支持内联图）。成功判定见 _dws_ok。"""
    global LAST_DWS_ERR
    images = list(images or [])
    content = _dws_content(text_body, images)
    cmd = [DWS_CLI, "mail", "message", "send",
           "--from", EMAIL_FROM, "--to", EMAIL_TO,
           "--subject", subject, "--content", content,
           "--format", "json"]
    for p in images:
        cmd += ["--inline-attachment", p]
    if dry_run:
        print("[dry-run][dws] 主题=%s | 内联图 %d 张 %s | 正文含占位符=%s"
              % (subject, len(images), [_img_name(p) for p in images],
                 all(("[inline:%s]" % _img_name(p)) in content for p in images)))
        return True
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        out = proc.stdout.strip()
        if _dws_ok(out):
            mid = _dws_msgid(out)
            LAST_DWS_ERR = ""
            print("[邮件] dws 主通道发送成功 -> %s（内联图 %d 张%s）"
                  % (EMAIL_TO, len(images), "，messageId=%s" % mid if mid else ""))
            return True
        LAST_DWS_ERR = "返回未含成功标记｜%s" % ((proc.stderr or out)[:200] or "(空返回)")
        print("[邮件] dws 返回异常: %s" % LAST_DWS_ERR)
        return False
    except FileNotFoundError:
        LAST_DWS_ERR = "dws CLI 不存在: %s" % DWS_CLI
        print("[邮件] %s" % LAST_DWS_ERR)
        return False
    except Exception as e:
        LAST_DWS_ERR = "调用异常 %s: %s" % (type(e).__name__, str(e)[:160])
        print("[邮件] dws 调用失败: %s" % str(e)[:200])
        return False


def send_via_smtp(subject, html_body, text_body="", images=None, dry_run=False):
    """回退通道：163 SMTP（有图时用 multipart/related 内嵌；465/994 SSL 优先）。"""
    global LAST_SMTP_ERR
    images = list(images or [])
    if dry_run:
        print("[dry-run][smtp] 主题=%s | 内联图 %d 张 %s | html含cid=%s"
              % (subject, len(images), [_img_name(p) for p in images],
                 all(("<img src='cid:%s'" % _img_name(p)) in _smtp_html(html_body, text_body, images)
                     for p in images)))
        print("[dry-run][smtp] 端口候选=%s" % [("%s:%d/%s" % c) for c in _smtp_candidates()])
        return True
    code = get_auth_code()
    if not code:
        LAST_SMTP_ERR = "未取到 163 授权码，回退通道不可用"
        print("[邮件] %s" % LAST_SMTP_ERR)
        return False

    html = _smtp_html(html_body, text_body, images)
    if images:
        msg = MIMEMultipart("related")
        msg.attach(MIMEText(html, "html", "utf-8"))
        for p in images:
            with open(p, "rb") as f:
                img = MIMEImage(f.read())
            img.add_header("Content-ID", "<%s>" % _img_name(p))
            img.add_header("Content-Disposition", "inline", filename=_img_name(p))
            msg.attach(img)
        msg["Message-ID"] = make_msgid()
    else:
        msg = MIMEText(html, "html", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr((str(Header(SENDER_NAME, "utf-8")), EMAIL_USER))
    msg["To"] = EMAIL_TO

    last = ""
    for host, port, mode in _smtp_candidates():
        try:
            if mode == "ssl":
                server = smtplib.SMTP_SSL(host, port, timeout=SMTP_TIMEOUT)
            else:
                server = smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT)
                server.starttls()
            server.login(EMAIL_USER, code)
        except Exception as e:
            last = "%s:%d/%s 连接或登录失败 %s: %s" % (host, port, mode.upper(),
                                                  type(e).__name__, str(e)[:120])
            print("[邮件] 163 SMTP 尝试失败: %s" % last)
            continue
        sent, send_err = False, ""
        try:
            server.sendmail(EMAIL_USER, [EMAIL_TO], msg.as_string())
            sent = True
        except Exception as e:
            send_err = "%s: %s" % (type(e).__name__, str(e)[:120])
        finally:
            try:
                server.quit()
            except Exception:
                pass
        if sent:
            LAST_SMTP_ERR = ""
            print("[邮件] 163 SMTP 回退通道发送成功 -> %s（内联图 %d 张，%s:%d/%s）"
                  % (EMAIL_TO, len(images), host, port, mode.upper()))
            return True
        last = "%s:%d/%s 投递失败 %s" % (host, port, mode.upper(), send_err)
        print("[邮件] 163 SMTP 尝试失败: %s" % last)
    LAST_SMTP_ERR = last or "无可用发送端口"
    print("[邮件] 163 SMTP 发送失败: %s" % LAST_SMTP_ERR)
    return False


def _feishu_webhook():
    """取飞书群机器人 webhook：优先与卡片推送同一配置（feishu_config）。"""
    try:
        import feishu_config as fc
        url = getattr(fc, "WEBHOOK_URL", "")
        if url:
            return url
    except Exception:
        pass
    try:
        from config import FEISHU_WEBHOOK
        return FEISHU_WEBHOOK or ""
    except Exception:
        return ""


def notify_feishu(text, title=""):
    """邮件发送失败时在飞书消息侧通知（同一 webhook，纯文本消息）。"""
    url = _feishu_webhook()
    if not url:
        print("[通知] 未取到飞书 webhook，失败通知未发送")
        return False
    try:
        import requests
    except Exception as e:
        print("[通知] requests 不可用，失败通知未发送: %s" % str(e)[:120])
        return False
    body = {"msg_type": "text",
            "content": {"text": (("%s\n" % title) if title else "") + (text or "")}}
    try:
        r = requests.post(url, json=body, headers={"Content-Type": "application/json"},
                          timeout=20)
        d = r.json()
        print("[通知] 飞书失败通知回执:", json.dumps(d, ensure_ascii=False)[:200])
        return d.get("code") == 0 or d.get("StatusCode") == 0
    except Exception as e:
        print("[通知] 飞书失败通知发送异常: %s" % str(e)[:200])
        return False


def send_email(subject, text_body, html_body=None, images=None, retries=1, dry_run=False):
    """邮件发送：主通道 dws（可带内联图），失败回退 163 SMTP；双通道均失败后整体重试 retries 次。

    images: 本地图片路径列表，按顺序内嵌进正文；None/[] 表示纯文本邮件（与原行为一致）。
    失败时把原因写入 LAST_ERROR，供调用方在飞书消息侧通知。
    """
    if not (EMAIL_FROM and EMAIL_TO):
        print("[邮件] 未配置 email.sender/email.recipients（见 config/secrets.json），跳过发送")
        return False
    global LAST_ERROR
    images = list(images or [])
    if dry_run:
        send_via_dws(subject, text_body, images, dry_run=True)
        send_via_smtp(subject, html_body or "", text_body, images, dry_run=True)
        LAST_ERROR = ""
        return True
    for attempt in range(retries + 1):
        if send_via_dws(subject, text_body, images):
            LAST_ERROR = ""
            return True
        print("[邮件] 主通道失败，尝试 163 SMTP 回退...")
        if send_via_smtp(subject, html_body or "", text_body, images):
            LAST_ERROR = ""
            return True
        if attempt < retries:
            print("[邮件] 双通道失败，重试第 %d 次..." % (attempt + 1))
    LAST_ERROR = "主通道 dws: %s ｜ 回退 163 SMTP: %s" % (LAST_DWS_ERR or "失败",
                                                    LAST_SMTP_ERR or "失败")
    print("[邮件] 发送失败（双通道均不可用）｜ %s" % LAST_ERROR)
    return False


def main():
    ap = argparse.ArgumentParser(description="中美期权风险释放汇总 邮件提醒入口")
    ap.add_argument("--subject", required=True, help="邮件主题")
    ap.add_argument("--body", default="", help="纯文本正文（与 --body-file 二选一）")
    ap.add_argument("--body-file", default="", help="纯文本正文文件（dws 主通道用）")
    ap.add_argument("--html-file", default="", help="HTML 正文文件（SMTP 回退用）")
    ap.add_argument("--image", action="append", default=[], help="内联图片路径，可多次指定")
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

    ok = send_email(args.subject, text_body, html_body or None, images=args.image,
                    dry_run=args.dry_run)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()