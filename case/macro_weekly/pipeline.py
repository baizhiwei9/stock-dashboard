# -*- coding: utf-8 -*-
"""macro_weekly 的框架适配（周频跟踪）

取数与推送分开两步：
  · fetch   —— 只取数出报（--no-feishu），保证产物落在 out/ 供管理端展示；
  · dispatch —— 只推送（--push-only），读 fetch 阶段留下的 data/key_numbers.json，
                不重复取数（EDB 是 force_refresh 全量拉，重跑没必要）。

阶段：fetch → check → emit → dispatch（顺序见 task.json 的 pipeline）
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.normpath(os.path.join(HERE, "..", "_lib"))
for _p in (HERE, LIB):
    if _p not in sys.path:
        sys.path.insert(0, _p)

PY = 'PYTHONPATH="${PYLIBS_DIR:+$PYLIBS_DIR:}${PYTHONPATH:+$PYTHONPATH:}." python3 -u '
CMD_FETCH = PY + "weekly_track.py --no-feishu --no-email"
CMD_SEND = PY + "weekly_track.py --push-only"
SEC = "CN.MACRO"
TIMEOUT = 1800


def fetch(ctx):
    ctx.sh(CMD_FETCH, timeout=TIMEOUT, check=True)


def emit(ctx):
    from extract import auto_extract

    points = auto_extract(ctx, sec=SEC)
    if not points:
        ctx.warn("没有抽到可展示的指标", [
            "out/ 下没有可解析的 json 产物",
            "任务还在跑的话，面板这一格会是空的"], "warn")
    return points


def dispatch(ctx):
    ctx.sh(CMD_SEND, timeout=600, check=False)