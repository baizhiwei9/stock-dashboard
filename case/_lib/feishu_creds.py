#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""飞书凭据统一入口 —— 凭据不入库，源码里不再出现任何明文。

实际存储由 ``case/_lib/app_secrets.py`` 统一管理（所有敏感配置一个文件），
本模块只是飞书专用的门面，保持 ``APP_ID`` / ``APP_SECRET`` / ``WEBHOOK_URL``
这三个变量名不变，供各 case 的 ``feishu_config.py`` 与 ``feishu_push.py`` 引用。

读取优先级：
  1. 环境变量 FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_WEBHOOK_URL
  2. 凭据文件 ``~/.config/stock-dashboard/secrets.json`` 的 ``feishu`` 段
     （兼容旧的扁平 ``feishu_secret.json``）
  3. 都没有 → 抛 FeishuCredentialMissing，报错里写清该配哪几处

安装时先跑 ``python3 setup.py`` 录入；详见仓库根目录 DEPLOY.md「飞书凭据」。

历史背景：2026-11 之前凭据以明文硬编码散落在 6 个 feishu_config.py 和 10 个脚本里，
已随公开仓库外泄。脱敏后统一收口，仓库只留占位符与示例文件。
"""
import app_secrets as _secrets

__all__ = [
    "APP_ID", "APP_SECRET", "WEBHOOK_URL", "TOKEN_URL", "UPLOAD_URL",
    "FeishuCredentialMissing", "load_credentials", "require",
]

# API 端点不是密钥，可以留在代码里
TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
UPLOAD_URL = "https://open.feishu.cn/open-apis/im/v1/images"

_FIELDS = ("app_id", "app_secret", "webhook_url")


class FeishuCredentialMissing(_secrets.SecretMissing):
    """飞书凭据缺失。"""


def load_credentials():
    """返回 (dict, 来源说明)。缺项用空串，不在这里抛错。"""
    sec = _secrets.section("feishu")
    return ({k: (sec.get(k) or "").strip() for k in _FIELDS}, _secrets.source())


_CREDS, _SOURCE = load_credentials()
APP_ID = _CREDS["app_id"]
APP_SECRET = _CREDS["app_secret"]
WEBHOOK_URL = _CREDS["webhook_url"]


def require(*fields):
    """按需校验。fields 取 "app_id" / "app_secret" / "webhook_url"。

    只发文本（webhook 模式）不需要 APP_ID/APP_SECRET；上传图片或走应用身份
    发消息才需要。调用方按自己真实需要校验，不要一律全查。
    """
    try:
        return _secrets.require("feishu", *(fields or _FIELDS))
    except _secrets.SecretMissing as e:
        raise FeishuCredentialMissing(str(e)) from e
