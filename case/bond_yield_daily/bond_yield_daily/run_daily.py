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

_REPO = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/projects/commodities_strategies"
_WS = os.path.dirname(os.path.dirname(_REPO))
for _p in (_REPO, os.path.join(_WS, "pylibs"), os.path.join(_WS, "py_deps"),
           os.path.join(_WS, ".venv_pkgs")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from commodities_strategies.bond_yield.run import main  # noqa: E402

if __name__ == "__main__":
    main()