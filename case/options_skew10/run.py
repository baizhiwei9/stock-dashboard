#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""偏度预警skew10 —— 薄包装，算法在 options_utility 仓。"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(_HERE, "..", "_shared")))

from options_daily_runner import run  # noqa: E402

if __name__ == "__main__":
    sys.exit(run(["alert/tasks/skew10.py"],
                 crontab_name="偏度预警skew10", title="偏度预警skew10"))
