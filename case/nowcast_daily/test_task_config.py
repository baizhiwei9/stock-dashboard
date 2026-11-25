#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""nowcast_daily 调度契约：23:10 且不许提前拉。

`pull_forward` 默认从 16:00 起、最多提前 240 分钟（scheduler.py），任务如果
没有 `not_before`，会在 19:10–23:10 之间被提前拉起来，把「计划点 23:10」
记成已跑，当天不再重跑——23:10 之后（美东 10:10，官方更新后）那一班就永远等不到。这里锁住
两件事：task.json 声明了 `not_before`，且 case_registry 真的把它传给调度器。
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "_lib"))

import case_registry  # noqa: E402


def _task():
    with open(os.path.join(HERE, "task.json"), encoding="utf-8") as handle:
        return json.load(handle)


def test_task_json_declares_not_before_2310():
    task = _task()
    assert task["sched"]["at"] == "23:10"
    assert task["not_before"] == "23:10"


def test_registry_passes_not_before_to_scheduler():
    mods = {m["key"]: m for m in case_registry.as_modules()}
    assert mods["nowcast_daily"]["not_before"] == "23:10"


def test_scheduler_reads_not_before():
    sys.path.insert(0, os.path.join(HERE, "..", ".."))
    import scheduler

    assert scheduler._not_before_time({"not_before": "23:10"}).strftime("%H:%M") == "23:10"
    assert scheduler._not_before_time({}) is None
