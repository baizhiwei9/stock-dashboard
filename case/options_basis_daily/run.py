#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""期货基差日报 —— 薄包装，算法在 options_utility 仓。

与老 crontab 行为对齐：先跑 calc/basis_daily.py 落日频基差，再跑 basis_ma20.py 出报告。
（options_utility 仓里的 run/basis_daily.sh 少了第一步，这里按 live 版执行。）
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(_HERE, "..", "_shared")))

from options_daily_runner import run  # noqa: E402

if __name__ == "__main__":
    sys.exit(run(["calc/basis_daily.py", "alert/tasks/basis_ma20.py"],
                 crontab_name="期货基差日报", title="期货基差日报"))
