# -*- coding: utf-8 -*-
"""邮件推送：当日汇总图 + 文字报告 → 邮箱（钉钉企业邮箱 dws 主通道）

正文为纯文本/Markdown，使用 LF 换行（\n），dws 自动转 HTML 时保持换行结构；
汇总图以内联附件形式插入正文（[inline:文件名] 占位符，图片附在文首）。

用法：
  from mail_push import send_mail
  ok = send_mail(img_path, subject, markdown_body, inline_img=True)
"""
import json
import os
import subprocess

# 钉钉企业邮箱（用户已授权，无额外凭据）
EMAIL_FROM = "baizhiwei9492@dingtalk.com"   # 发件人
EMAIL_TO = "410119229@qq.com"               # 收件人

DWS_CLI = os.path.expanduser("~/.local/bin/dws")


def send_mail(subject, markdown_body, img_path=None, inline_img=True):
    """通过钉钉企业邮箱发送邮件。

    Args:
        subject: 邮件标题
        markdown_body: 正文（LF 换行，可含 [inline:文件名] 占位符）
        img_path: 汇总图路径；inline_img=True 时作为内联图片放文首
    Returns:
        bool
    """
    try:
        cmd = [DWS_CLI, "mail", "message", "send",
               "--from", EMAIL_FROM,
               "--to", EMAIL_TO,
               "--subject", subject]
        body = markdown_body
        if img_path and inline_img:
            fname = os.path.basename(img_path)
            # 先图后文：图片占位符放在正文最前面，紧跟换行
            body = f"图表如下（先图后文）：\n\n[inline:{fname}]\n\n{markdown_body}"
            cmd += ["--inline-attachment", img_path]
        # 注意：正文必须以 LF 换行传递，禁止 \r\n
        cmd += ["--content", body, "--format", "json"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        out = (proc.stdout or "").strip()
        # dws 成功返回 {"success": true, "result": {...messageId...}}；success 为布尔或字符串 "true" 均可
        try:
            resp = json.loads(out)
        except (json.JSONDecodeError, ValueError):
            resp = None
        if isinstance(resp, dict) and str(resp.get("success")).strip().lower() == "true":
            return True
        if '"success": "true"' in out or '"success":"true"' in out:
            return True
        print(f"[邮件] dws 返回异常: {(proc.stderr or '').strip()[:300] or out[:300]}")
        return False
    except FileNotFoundError:
        print(f"[邮件] dws CLI 不存在: {DWS_CLI}")
        return False
    except Exception as e:  # noqa: BLE001
        print(f"[邮件] dws 调用失败: {e}")
        return False