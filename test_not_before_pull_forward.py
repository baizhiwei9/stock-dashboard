#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""not_before 只在「提前拉取」时生效 —— 回归测试

背景
────
`pull_forward` 默认 16:00 起，会把未来 `pull_ahead_min`（默认 240）分钟内的
任务提前拉起。`not_before` 就是拦这个的口子：

    if nb is not None and now.time() < nb:
        continue                     # 数据还没就绪，不许提前

**但到点正常跑（slot 不为 None）时完全不看 not_before。**

2026-11-26 发现的问题：tech_snapshot 配 `at=23:20, not_before=22:30`，
于是 22:30~23:19 整整 50 个 tick 都可能被提前拉起，而那时 producer 还没跑完，
快照就是半截数据。正确写法是把 not_before 设成与 at 相同，等于「禁止提前跑」。

这些用例锁住三件事：
  1. not_before == at  → 提前拉窗为空
  2. not_before <  at  → 提前拉窗 = [not_before, at)
  3. 到点后（slot 已到）not_before 不参与判断
"""
import json
import os
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "case", "_lib"))

import scheduler as S


def _pull_window(at, not_before, day=datetime(2026, 11, 26)):
    """逐分钟扫一整天，返回「会被提前拉起」的时刻列表。"""
    sched = {"mode": "daily", "at": at, "weekdays": [1, 2, 3, 4, 5]}
    m = {"not_before": not_before}
    conf = S._queue_conf()
    nb = S._not_before_time(m)
    out = []
    for mins in range(24 * 60):
        now = day + timedelta(minutes=mins)
        if now.time() < conf["pull_from"]:
            continue
        slot = S.compute_slot(sched, now)
        if slot is not None and (now - slot).total_seconds() > S.GRACE_SEC:
            if slot.date() != now.date():
                slot = None
        if slot is not None:
            continue                      # 到点了，走正常路径
        nx = S.compute_next(sched, now)
        if nx is None or (nx - now).total_seconds() > conf["pull_ahead_sec"]:
            continue
        if nb is not None and now.time() < nb:
            continue
        out.append(now.strftime("%H:%M"))
    return out


def test_not_before_equals_at_blocks_all_pulls():
    """not_before == at → 一次都不许提前拉。"""
    for at in ("22:37", "23:20", "22:40"):
        assert _pull_window(at, at) == [], "%s 仍被提前拉" % at


def test_not_before_earlier_than_at_opens_window():
    """not_before < at → 提前拉窗 = [not_before, at)。"""
    w = _pull_window("23:20", "22:30")
    assert w, "应该存在提前拉窗"
    assert w[0] == "22:30", w[0]
    assert w[-1] == "23:19", w[-1]
    assert len(w) == 50, len(w)


def test_no_not_before_means_widest_window():
    """不配 not_before → 窗口最宽（提前量 240 分钟）。"""
    w = _pull_window("22:40", None)
    assert w, "应该存在提前拉窗"
    assert w[0] == "18:40", w[0]
    assert w[-1] == "22:39", w[-1]
    assert len(w) == 240, len(w)


def test_not_before_later_than_at_also_blocks():
    """not_before > at：at 之前一直被 nb 挡，等价于不提前拉。"""
    assert _pull_window("22:37", "22:50") == []


def test_example_json_dependency_tasks_have_not_before_eq_at():
    """modules.example.json 里「等全部 producer」的任务必须 not_before == at。"""
    p = os.path.join(HERE, "modules.example.json")
    if not os.path.isfile(p):
        return
    d = json.load(open(p))
    must = {"tech_snapshot", "build"}
    for m in d.get("modules", []):
        k = m.get("key")
        if k not in must:
            continue
        at = str((m.get("sched") or {}).get("at") or "")
        nb = str(m.get("not_before") or "")
        assert nb == at, "%s 的 not_before(%s) 应等于 at(%s)，否则会被提前拉起跑半截数据" % (k, nb, at)


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print("  ✅ %s" % name)
        except AssertionError as e:
            fails += 1
            print("  ❌ %s: %s" % (name, e))
    raise SystemExit(1 if fails else 0)
