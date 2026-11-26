# -*- coding: utf-8 -*-
"""nowcast_daily 的框架适配：fetch 跑 run.py，emit 把落盘的 points 交给指标仓。

run.py 一次取完 EDB 并算完变动、写 ``out/points.json`` 与 ``out/report.md``；
emit 阶段只做「取回 + 落库」，不重复联网，避免同一天两次打四个官方端点。
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.normpath(os.path.join(HERE, "..", "_lib"))
for _p in (HERE, LIB):
    if _p not in sys.path:
        sys.path.insert(0, _p)

CMD_FETCH = "python3 -u run.py"
TIMEOUT = 900


def fetch(ctx):
    """取数 + 判大幅波动 + 发邮件；check 阶段随后核对 report.md 与 points.json。"""
    ctx.sh(CMD_FETCH, timeout=TIMEOUT, check=True)


def emit(ctx):
    """把 out/points.json 里的标准指标交回框架（框架负责写 latest.json 并落库）。"""
    path = os.path.join(ctx.out_dir, "points.json")
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        ctx.warn("points.json 缺失",
                 ["run.py 没有正常产出", "检查 out/report.md 是否存在"], "error")
        return []
    return data.get("points") or []
