# -*- coding: utf-8 -*-
"""月度宏观报告工程 - 可复用配置
飞书 webhook 与邮件通道复用既有工作区凭据（未新增密钥落盘）。
"""
import os
import sys

WS = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace"
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")

# ---- 飞书（复用既有 feishu_config.webhook，仅发消息不需要 APP_SECRET）----
_FEISHU_CFG = os.path.join(
    WS, "ses_gffe5febb5eaeeffe0mTIpBeu5w0Nq0", "feishu_config.py")
if os.path.exists(_FEISHU_CFG):
    _d = {}
    with open(_FEISHU_CFG, encoding="utf-8") as _f:
        exec(compile(_f.read(), _FEISHU_CFG, "exec"), _d)
    FEISHU_WEBHOOK = _d.get("WEBHOOK_URL", "")
else:
    FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK_URL", "")

# ---- 邮件：主通道钉钉企业邮箱（dws CLI），回退 163 SMTP ----
DWS_CLI = os.path.expanduser("~/.local/bin/dws")
EMAIL_FROM = "baizhiwei9492@dingtalk.com"
EMAIL_TO = "410119229@qq.com"

EMAIL_HOST = "smtp.163.com"
EMAIL_PORT = 25
EMAIL_USER = "songshang951@163.com"

_LEGACY_PATHS = [
    os.path.join(WS, "ses_gffe5f530aa190ffeRxnRiUPRdvAPh0", "daily_wavelet_monitor.py"),
    os.path.join(WS, "ses_gffe5f530aa190ffeRxnRiUPRdvAPh0", "test_email_only.py"),
]


def get_auth_code():
    """163 授权码：环境变量优先，缺失时读既有 legacy 配置（不新落盘）。"""
    code = os.environ.get("EMAIL_AUTH_CODE", "")
    if code:
        return code
    import re
    for p in _LEGACY_PATHS:
        try:
            with open(p, encoding="utf-8") as f:
                src = f.read()
            m = re.search(r"EMAIL_PASS\s*=\s*[\"']([^\"']+)[\"']", src)
            if m and len(m.group(1)) >= 6:
                return m.group(1)
        except Exception:
            continue
    return ""


def ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(OUT_DIR, exist_ok=True)