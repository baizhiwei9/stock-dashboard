# -*- coding: utf-8 -*-
"""rate_board 的框架适配：fetch 跑 run.py，emit 把 run.py 落盘的 points 交给指标仓。

run.py 一次取完 EDB / 土狗并写 out/points.json，emit 阶段只做"取回 + 落库"，
不再重复联网，避免同一天两次打 FedWatch 与 EDB。
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
    """取数 + 渲染摘要；check 阶段随后核对 out/report.md 与 out/fedwatch.json。"""
    ctx.sh(CMD_FETCH, timeout=TIMEOUT, check=True)


def emit(ctx):
    """把 out/points.json 里的标准指标交回框架（框架负责写 latest.json 并落库）。"""
    p = os.path.join(ctx.out_dir, "points.json")
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        ctx.warn("points.json 缺失", ["run.py 没有正常产出", "检查 out/ 下的 report.md 是否存在"], "error")
        return []
    return data.get("points") or []
