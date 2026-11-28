# -*- coding: utf-8 -*-
"""精简简报双通道推送：飞书交互式卡片（含三张图）+ 邮件（内联图）。

- 飞书：用 case/_lib/feishu_card 上传图片拿 img_key，再以 webhook 发卡片；
  同一张卡片落 out/card.json + out/card.md，供展示页复用。
- 邮件：钉钉企业邮箱 CLI（dws）以 HTML 正文 + 内联图发送；dws 不可用或失败时
  回退 163 SMTP（MIMEMultipart/related，图片按 cid 内联）。
- 两路都返回真实成功标志才算成功；缺失凭据时打印明确原因而不是静默忽略。
"""
import argparse
import json
import os
import smtplib
import subprocess
import sys
from email.header import Header
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import brief                                            # noqa: E402
import config                                           # noqa: E402


# ---------------- 飞书 ----------------
def send_feishu(payload):
    import feishu_media

    if not config.FEISHU_WEBHOOK:
        print("[飞书] 未配置 webhook（FEISHU_WEBHOOK_URL / secrets.json feishu.webhook），跳过")
        return False
    try:
        tok = feishu_media.token()
        keys = {}
        for k, path in payload["imgs"].items():
            key = feishu_media.upload_image(tok, path)
            if key:
                keys[k] = key
            else:
                print(f"[飞书] 图片 {k} 上传失败，卡片将缺该图")
        card = _card_v1(payload, keys)
        feishu_media.dump_card({"msg_type": "interactive", "card": card}, config.OUT_DIR)
        body = json.dumps({"msg_type": "interactive", "card": card},
                          ensure_ascii=False).encode()
        req = __import__("urllib.request", fromlist=["Request"]).Request(
            config.FEISHU_WEBHOOK, data=body,
            headers={"Content-Type": "application/json"})
        res = json.loads(__import__("urllib.request", fromlist=["urlopen"]).urlopen(
            req, timeout=30).read())
        ok = res.get("code") == 0 or res.get("StatusCode") == 0
        print("[飞书] 卡片" + ("发送成功" if ok else f"返回错误: {str(res)[:200]}"))
        return ok
    except Exception as exc:                              # noqa: BLE001
        print("[飞书] 发送异常:", str(exc)[:200])
        return False


def _card_v1(payload, keys):
    """卡片 1.0 结构（div/lark_md + img + hr）。

    自定义机器人 webhook 只接受 1.0 元素；`{"tag": "markdown"}` 属卡片 2.0，
    会报 11310 unsupported type of block，所以这里不复用 build_card。
    """
    d, j = payload["data"], payload["judgements"]
    els = [{"tag": "div",
            "fields": [{"is_short": True,
                        "text": {"tag": "lark_md", "content": f"**{k}**\n{v}"}}
                       for k, v in payload["kpis"]]},
           {"tag": "hr"}]
    for head, key, text in brief._sections(d):
        els.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**{head}**"}})
        if keys.get(key):
            els.append({"tag": "img", "img_key": keys[key],
                        "alt": {"tag": "plain_text", "content": head},
                        "mode": "fit_horizontal", "preview": True})
        els.append({"tag": "div", "text": {"tag": "lark_md", "content": text}})
    if j.get("combo"):
        els.append({"tag": "hr"})
        els.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**一句话**：{j['combo']}"}})
    els.append({"tag": "note",
                "elements": [{"tag": "plain_text",
                              "content": "DuMate 宏观策略组 · 数据来自 EDB（统计局 / 海关 / ICE）"}]})
    return {"config": {"wide_screen_mode": True},
            "header": {"template": "blue",
                       "title": {"tag": "plain_text", "content": payload["title"]}},
            "elements": els}


# ---------------- 邮件 ----------------
def _dws_ok(proc):
    """dws 返回 {"success": true}（布尔）或旧版 {"success": "true"}（字符串），都要认。"""
    out = (proc.stdout or "").strip()
    try:
        obj = json.loads(out)
        val = obj.get("success")
        return val is True or (isinstance(val, str) and val.lower() == "true")
    except Exception:                                    # noqa: BLE001
        low = out.replace(" ", "").lower()
        return '"success":true' in low


def send_email_dws(subject, html, imgs):
    if not config.DWS_CLI or not os.path.exists(config.DWS_CLI):
        print("[邮件] 未找到 dws CLI（可设 DWS_CLI），改用 SMTP")
        return False
    cmd = [config.DWS_CLI, "mail", "message", "send",
           "--from", config.EMAIL_FROM, "--to", config.EMAIL_TO,
           "--subject", subject, "--content", html, "--format", "json", "-y"]
    for path in imgs:
        cmd += ["--inline-attachment", path]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if _dws_ok(proc):
        print("[邮件] dws 发送成功 ->", config.EMAIL_TO)
        return True
    print("[邮件] dws 发送失败：", (proc.stdout or proc.stderr)[-300:])
    return False


def send_email_smtp(subject, html, imgs):
    code = config.get_auth_code()
    if not (config.EMAIL_USER and code and config.EMAIL_TO):
        print("[邮件] SMTP 凭据不全（EMAIL_USER / EMAIL_AUTH_CODE / EMAIL_TO），回退不可用")
        return False
    msg = MIMEMultipart("related")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr(("DuMate 宏观策略组", config.EMAIL_USER))
    msg["To"] = config.EMAIL_TO
    msg.attach(MIMEText(html, "html", "utf-8"))
    for path in imgs:
        with open(path, "rb") as f:
            part = MIMEImage(f.read())
        part.add_header("Content-ID", "<%s>" % os.path.basename(path))
        part.add_header("Content-Disposition", "inline",
                        filename=os.path.basename(path))
        msg.attach(part)
    try:
        s = smtplib.SMTP(config.EMAIL_HOST, config.EMAIL_PORT, timeout=60)
        s.login(config.EMAIL_USER, code)
        s.sendmail(config.EMAIL_USER, [t.strip() for t in config.EMAIL_TO.split(",") if t.strip()],
                   msg.as_string())
        s.quit()
        print("[邮件] SMTP 发送成功 ->", config.EMAIL_TO)
        return True
    except Exception as exc:                              # noqa: BLE001
        print("[邮件] SMTP 发送失败：", str(exc)[:200])
        return False


def send_email(payload):
    imgs = list(payload["imgs"].values())
    names = {k: os.path.basename(v) for k, v in payload["imgs"].items()}
    html = brief.email_html(payload["data"], names)
    path = os.path.join(config.OUT_DIR, "brief_email.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    subject = f"[宏观三条线] {payload['title']}"
    if send_email_dws(subject, html, imgs):
        return True
    return send_email_smtp(subject, html, imgs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--feishu-only", action="store_true")
    ap.add_argument("--email-only", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="只生成简报与图，不推送")
    args = ap.parse_args()

    payload = brief.build()
    print("[brief] 标题:", payload["title"], "| 图:", ", ".join(payload["imgs"]))
    if args.dry_run:
        print("[brief] dry-run，未推送")
        return 0

    ok = True
    if not args.email_only:
        ok = send_feishu(payload) and ok
    if not args.feishu_only:
        ok = send_email(payload) and ok
    print("[push_brief] 完成" if ok else "[push_brief] 存在失败通道")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())