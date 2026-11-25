#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""兼容入口（只做转发）：国债收益率四维预测 + 曲线监控日报。

实现已于 2026-11-17 迁入商品期货策略库独立模块
``commodities_strategies.bond_yield``（仓库：workspace/projects/commodities_strategies）。

保留本文件是为了让既有日更定时任务的单条命令原样可跑：

    PYTHONPATH=.../workspace/pylibs python bond_yield_daily/run_daily.py

参数与输出与迁移前一致；取数口径改为全部经 DataHub（EDB RATE.CN.* +
bond 子包中债全期限曲线），不可达时回退 data/bond_yield/snapshot 并在卡片标注。
"""
import os
import sys

# 本 case 的计算逻辑在外部仓库 commodities_strategies 里（bond_yield 子包）。
# 原写死的 projects/commodities_strategies 在现环境下不存在，之前是静默 sys.path
# 加空路径 → 到 import 才炸。改成候选探测 + 缺失时给出可操作的报错。
_REPO_CANDS = [
    os.environ.get("COMMODITIES_STRATEGIES_ROOT", ""),
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "projects", "commodities_strategies"),
]
_REPO = next((p for p in _REPO_CANDS
              if p and os.path.isdir(os.path.join(p, "commodities_strategies"))), None)
if _REPO is None:
    raise SystemExit(
        "找不到 commodities_strategies 仓库，bond_yield_daily 无法运行。\n"
        "  候选位置：%s\n"
        "  请把该仓库放到上述任一位置，或用环境变量 COMMODITIES_STRATEGIES_ROOT 指定。\n"
        "  注：本任务当前 task.json enabled=false，未被调度；启用前必须先补齐该依赖。"
        % "、".join(x for x in _REPO_CANDS if x))

_WS = os.path.dirname(os.path.dirname(_REPO))
for _p in (_REPO, os.path.join(_WS, "pylibs"), os.path.join(_WS, "py_deps"),
           os.path.join(_WS, ".venv_pkgs")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from commodities_strategies.bond_yield.run import main  # noqa: E402

if __name__ == "__main__":
    main()