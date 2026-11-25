# -*- coding: utf-8 -*-
"""月度宏观报告工程 - 可复用配置

飞书 webhook 与邮件凭据统一从 config/secrets.json 读取（见 config/README.md），
本文件不再保存任何明文凭据，也不再从别的工作区脚本里刮授权码。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
_LIB = os.path.join(_ROOT, "case", "_lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

import app_secrets  # noqa: E402

DATA_DIR = os.path.join(_HERE, "data")
OUT_DIR = os.path.join(_HERE, "output")

# ---- 飞书（群机器人发消息不需要 APP_SECRET）----
FEISHU_WEBHOOK = app_secrets.get("feishu.webhook", "")

# ---- 邮件：主通道钉钉企业邮箱（dws CLI），回退 163 SMTP ----
DWS_CLI = os.path.expanduser("~/.local/bin/dws")
EMAIL_FROM = app_secrets.get("email.sender", "")
EMAIL_TO = app_secrets.get("email.recipients", "")

EMAIL_HOST = app_secrets.get("email.smtp_host", "smtp.163.com")
EMAIL_PORT = app_secrets.get_int("email.smtp_port", 465)
EMAIL_USER = app_secrets.get("email.username", "")


def get_auth_code():
    """SMTP 授权码：只从 config/secrets.json / 环境变量取。"""
    return app_secrets.get("email.password", "")


def ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(OUT_DIR, exist_ok=True)
