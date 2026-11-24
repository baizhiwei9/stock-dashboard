# -*- coding: utf-8 -*-
"""ot_unified 的框架适配（自动生成，可直接改）

由 case/_tools/make_pipeline.py 按入口参数生成：
  · 取数阶段不带 --no-push/--no-share/--no-alert，发送单独放 dispatch
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

CMD_FETCH = 'PYTHONPATH="${PYLIBS_DIR:?请先设置 PYLIBS_DIR 指向 pylibs 目录}:." python3 -u ot_unified_daily.py --no-push --no-share --no-alert'
CMD_SEND = 'PYTHONPATH="${PYLIBS_DIR:?请先设置 PYLIBS_DIR 指向 pylibs 目录}:." python3 -u ot_unified_daily.py'
SEC = 'CN.STOCK'
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
    ctx.sh(CMD_SEND, timeout=TIMEOUT, check=False)
