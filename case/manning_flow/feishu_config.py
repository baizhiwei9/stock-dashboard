"""飞书推送凭据（统一从 config/secrets.json 读取，不写明文）

只用群机器人推送时仅需 feishu.webhook；需要应用消息/上传图片时再取
feishu.app_id / feishu.app_secret。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
_LIB = os.path.join(_ROOT, "case", "_lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

import app_secrets  # noqa: E402

WEBHOOK_URL = app_secrets.get("feishu.webhook")


def app_credentials():
    """返回 (app_id, app_secret)；缺配置时抛 SecretMissing。"""
    return (app_secrets.get("feishu.app_id"),
            app_secrets.get("feishu.app_secret"))
