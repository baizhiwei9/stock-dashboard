# -*- coding: utf-8 -*-
"""factor_rotation 的框架适配（自动生成，可直接改）

由 case/_tools/make_pipeline.py 按入口参数生成：
  · 原入口一体式（没有发送开关）：取数阶段一次跑完取数+出报告+自带发送，emit 之后只做产物复盘
  · 指标用 case/_lib/extract.py 扫 out/*.json 自动抽取，需要更细就改下面的 emit()

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

CMD_FETCH = 'PYTHONPATH="${PYLIBS_DIR:?请先设置 PYLIBS_DIR 指向 pylibs 目录}:." python3 -u raw/fetch_all.py'
CMD_SEND = ''
SEC = 'CN.STOCK'
TIMEOUT = 1800


def fetch(ctx):
    ctx.sh(CMD_FETCH, timeout=TIMEOUT, check=False)


def emit(ctx):
    from extract import auto_extract
    points = auto_extract(ctx, sec=SEC)
    if not points:
        ctx.warn("没有抽到可展示的指标", [
            "out/ 下没有可解析的 json 产物",
            "任务还在跑的话，面板这一格会是空的"], "warn")
    return points


def dispatch(ctx):
    ctx.log("这个任务的发送内嵌在取数阶段（原入口无 --no-push 开关），dispatch 不重复发送")
