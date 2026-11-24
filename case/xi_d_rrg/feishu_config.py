#!/usr/bin/env python3
"""飞书机器人配置 - 用于图片上传和消息发送"""

# 飞书应用凭据
APP_ID = "cli_a97b60337b7bdbd5"
APP_SECRET = "CwiWINsJk6Zzu0uO5pdmCd00y3HEQwPB"

# 自定义机器人webhook(用于发消息)
WEBHOOK_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/6d34534a-af18-48ba-be77-a30c524734a8"

# 飞书群chat_id (AI股票消息群, 用于应用API发文件消息)
CHAT_ID = "oc_5971f823bf987081f722f2942bbc8d71"

# API端点
TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
UPLOAD_URL = "https://open.feishu.cn/open-apis/im/v1/images"
FILE_UPLOAD_URL = "https://open.feishu.cn/open-apis/im/v1/files"
MSG_SEND_URL = "https://open.feishu.cn/open-apis/im/v1/messages"
