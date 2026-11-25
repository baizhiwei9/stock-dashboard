#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""兼容入口（只做转发）：国债收益率四维预测 + 曲线监控日报。

实现已于 2026-11-17 迁入商品期货策略库独立模块
``commodities_strategies.bond_yield``。

保留本文件是为了让既有日更定时任务的单条命令原样可跑：

    python bond_yield_daily/run_daily.py

参数与输出与迁移前一致；取数口径改为全部经 DataHub（EDB RATE.CN.* +
bond 子包中债全期限曲线），不可达时回退 data/bond_yield/snapshot 并在卡片标注。

依赖说明
--------
``commodities_strategies`` 现在只在 **precious-task 仓库**里维护一份（
``precious-task/case/bond_three_factor/commodities_strategies/``），
bond_three_factor（周更三因子）和本任务（日更收益率曲线）共用它。
这里按「云盘同级目录 → 本仓库副本 → 旧位置」的顺序探测，避免再写死绝对路径。
"""
import os
import sys

# ---- 定位 commodities_strategies 包（父目录须含 commodities_strategies/）--------
# __file__ = <ws>/stock-dashboard/case/bond_yield_daily/bond_yield_daily/run_daily.py
_HERE = os.path.dirname(os.path.abspath(__file__))       # …/bond_yield_daily/bond_yield_daily
_CASE = os.path.dirname(_HERE)                           # …/case/bond_yield_daily
_PROJECT = os.path.dirname(os.path.dirname(_CASE))       # <ws>/stock-dashboard
_WS = os.path.dirname(_PROJECT)                          # <ws>

_REPO_CANDS = [
    os.environ.get("COMMODITIES_STRATEGIES_ROOT", ""),
    # 唯一真源：precious-task 的 bond_three_factor（用户已定该 case 归 precious-task）
    os.path.join(_WS, "precious-task", "case", "bond_three_factor"),
    # 本仓库同 case 副本（若将来同步过来）
    os.path.join(_PROJECT, "case", "bond_three_factor"),
    # 迁移前的旧位置
    os.path.join(_PROJECT, "projects"),
]
_REPO = next((p for p in _REPO_CANDS
              if p and os.path.isdir(os.path.join(p, "commodities_strategies"))), None)
if _REPO is None:
    raise SystemExit(
        "找不到 commodities_strategies 包，bond_yield_daily 无法运行。\n"
        "  候选父目录：\n    %s\n"
        "  请确认 precious-task 仓库与本仓库同级，"
        "或用环境变量 COMMODITIES_STRATEGIES_ROOT 直接指向含该包的父目录。\n"
        "  注：本任务当前 task.json enabled=false，未被调度；启用前必须先补齐该依赖。"
        % "\n    ".join(x for x in _REPO_CANDS if x))

# ---- 依赖路径：DataHub 根 + 仓库根 + workspace 级 py_deps/pylibs ---------------
# bond_yield.data 要 import data_hub.collectors.bond.market，所以 DataHub 必须在
# sys.path 上。路径口径统一走 case/_lib/paths.py，不在这里另写一套。
try:
    from paths import datahub_root, py_deps
except ImportError:                       # 手动单跑：向上找 case/_lib 再导
    _d = os.path.dirname(os.path.abspath(__file__))
    while not os.path.isfile(os.path.join(_d, "_lib", "paths.py")):
        _n = os.path.dirname(_d)
        if _n == _d:
            break
        _d = _n
    sys.path.insert(0, os.path.join(_d, "_lib"))
    from paths import datahub_root, py_deps

for _p in (datahub_root(), py_deps(), _REPO,
           os.path.join(_WS, "pylibs"),
           os.path.join(_WS, "py_deps"),
           os.path.join(_WS, ".venv_pkgs")):
    if _p and os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from commodities_strategies.bond_yield.run import main  # noqa: E402

if __name__ == "__main__":
    main()
