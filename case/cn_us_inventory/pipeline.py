# -*- coding: utf-8 -*-
"""cn_us_inventory 的框架适配（自动生成，可直接改）

由 case/_tools/make_pipeline.py 按入口参数生成：
  · 取数阶段不带 --no-feishu/--no-email，发送单独放 dispatch
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

CMD_FETCH = 'PYTHONPATH="${PYLIBS_DIR:+$PYLIBS_DIR:}${PYTHONPATH:+$PYTHONPATH:}." python3 -u inventory_monthly.py --no-feishu --no-email'
CMD_SEND = 'PYTHONPATH="${PYLIBS_DIR:+$PYLIBS_DIR:}${PYTHONPATH:+$PYTHONPATH:}." python3 -u inventory_monthly.py'
CMD_ARCHIVE = 'PYTHONPATH="${PYLIBS_DIR:+$PYLIBS_DIR:}${PYTHONPATH:+$PYTHONPATH:}." python3 -u weekly/archive_macro.py'
SEC = 'CN.MACRO'
TIMEOUT = 1800


def fetch(ctx):
    ctx.sh(CMD_FETCH, timeout=TIMEOUT, check=True)
    # 数据刷新后立刻落一年期归档库（独立 SQLite，日频+月频全量 upsert）；
    # 归档失败不阻断主报告，只告警 —— 不要因为归档把月度报告也拖挂。
    try:
        ctx.sh(CMD_ARCHIVE, timeout=900, check=True)
    except Exception as e:  # noqa: BLE001
        ctx.warn("归档失败，月度报告继续", [str(e)[:200]])


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
