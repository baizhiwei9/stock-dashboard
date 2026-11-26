#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""飞书机器人配置 —— 凭据统一从 case/_lib/feishu_creds.py 读取。

sim_trade/feishu_pusher.py 与 sim_trade/feishu_push.py 都会先把本目录
（sim_trade/）插进 sys.path，再 ``from feishu_config import WEBHOOK_URL``，
所以本文件必须放在 sim_trade/ 下。

历史：原先这两个推送模块从 TDA 股债策略搬来，硬依赖一个当时同目录的
feishu_config.py，迁移时该文件没有跟过来 → 直接 ModuleNotFoundError。
现在只保留变量名兼容层，实际取值由 case/_lib/feishu_creds 按
「环境变量 → 本地凭据文件」解析，不在仓库里存明文。
"""
import os
import sys

# sim_trade/ -> case/futures_xsec_hedge -> case -> _lib
_LIB = os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "_lib"))
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

from feishu_creds import (  # noqa: E402,F401
    APP_ID,
    APP_SECRET,
    WEBHOOK_URL,
    TOKEN_URL,
    UPLOAD_URL,
)
