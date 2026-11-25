#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交易机会邮件通知入口。

用法：
    python scripts/notify_opportunities.py            # dry-run，只预览
    python scripts/notify_opportunities.py --asof 2026-11-12
    python scripts/notify_opportunities.py --send     # 真实发送

通道（与 structural-factor 一致）：
    主通道：钉钉企业邮箱 dws（用户已授权，无需在仓库保存凭据）
    回退：SMTP / 163

环境变量：
    dws：EMAIL_TO / EMAIL_FROM（可选 DWS_CLI）
    SMTP：EMAIL_HOST / EMAIL_PORT / EMAIL_USER / EMAIL_AUTH_CODE
    EMAIL_FROM / EMAIL_TO 两个通道共用
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from commodities_strategies.email_notify import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())