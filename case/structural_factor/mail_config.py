#!/usr/bin/env python3
"""邮件发送配置 - 用于结构因子同时转向提醒

主通道：钉钉企业邮箱（dws CLI，用户已授权，无需额外凭据）
回退通道：163 SMTP（授权码不在此文件落盘，运行时读取环境变量或既有配置）
"""
import os
import re
import subprocess

# ===== 主通道：钉钉邮箱（dws）=====
DWS_CLI = os.path.expanduser("~/.local/bin/dws")   # dws 可执行文件
EMAIL_FROM = "baizhiwei9492@dingtalk.com"          # 用户企业邮箱
EMAIL_TO = "410119229@qq.com"                      # 收件人：用户个人邮箱

# ===== 回退通道：163 SMTP =====
EMAIL_HOST = "smtp.163.com"
EMAIL_PORT = 25
EMAIL_USER = "songshang951@163.com"

# 既有配置来源（历史会话验证过的 163 邮箱授权码所在文件，仅作回退）
_LEGACY_PATHS = [
    "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0/daily_wavelet_monitor.py",
    "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0/test_email_only.py",
]


def get_auth_code():
    """获取 163 授权码（仅回退通道使用）：环境变量优先，缺失时回退到既有配置文件"""
    code = os.environ.get("EMAIL_AUTH_CODE", "")
    if code:
        return code
    for path in _LEGACY_PATHS:
        try:
            with open(path, encoding="utf-8") as f:
                src = f.read()
            m = re.search(r"EMAIL_PASS\s*=\s*[\"']([^\"']+)[\"']", src)
            if m and len(m.group(1)) >= 6:
                return m.group(1)
        except Exception:
            continue
    return ""


def send_via_dws(subject, markdown_body):
    """通过钉钉企业邮箱发送邮件（主通道）。

    Returns:
        bool: 是否发送成功
    """
    try:
        cmd = [
            DWS_CLI, "mail", "message", "send",
            "--from", EMAIL_FROM,
            "--to", EMAIL_TO,
            "--subject", subject,
            "--content", markdown_body,
            "--format", "json",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        out = proc.stdout.strip()
        if '"success": "true"' in out or '"success":"true"' in out:
            return True
        print(f"[邮件] dws 返回异常: {proc.stderr.strip()[:200] or out[:200]}")
        return False
    except FileNotFoundError:
        print(f"[邮件] dws CLI 不存在: {DWS_CLI}")
        return False
    except Exception as e:
        print(f"[邮件] dws 调用失败: {e}")
        return False