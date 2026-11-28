# -*- coding: utf-8 -*-
"""clearance_cycle · 五阶段钩子

只实现 ``emit``：框架 ``runner.run_stage`` 的 emit 分支只认 ``hook`` / ``builtin``，
**不执行 stages[].cmd**；没有 hook 时 merged 为空，12 条指标一条也落不了库。

指标构造复用 ``emit.build_points()``，保证「框架跑」与「run_daily.sh 直跑」同一份口径。
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def emit(ctx):
    """把 build_points() 的结果交回框架（写 latest.json + 落库都由框架做）。"""
    from emit import build_points

    points = build_points()
    if callable(getattr(ctx, "emit", None)):
        ctx.emit(points)
    return points
