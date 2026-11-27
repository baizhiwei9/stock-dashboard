# -*- coding: utf-8 -*-
"""options_oi_daily 的 emit 钩子：把 options_utility 的产出搬成指标点（不做二次计算）。"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SHARED = os.path.normpath(os.path.join(_HERE, "..", "_shared"))
if _SHARED not in sys.path:
    sys.path.insert(0, _SHARED)


def emit(ctx):
    import options_metrics
    pts = options_metrics.points_for("options_oi_daily")
    ctx.log("搬运 %d 条指标点" % len(pts))
    return pts
