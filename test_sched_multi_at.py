#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sched.at 支持「一天多个时间点」的回归测试。

覆盖两件事：
  1. 字符串写法（旧）行为一字不变 —— 这是本改造的回归重点。
  2. 数组写法（新）在一天两档（早报 07:50 + 晚报 19:55）下的 slot / next 正确。
  3. 边界：空数组 / 非法元素 / 缺 at 不抛异常，沿用「没 at 就不调度」。

跑法：
    python3 -m pytest test_sched_multi_at.py -q
    python3 test_sched_multi_at.py
"""

import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import scheduler as S  # noqa: E402


def _dt(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M")


def _fmt(x):
    return x.strftime("%Y-%m-%d %H:%M") if x else None


WEEKDAYS = [1, 2, 3, 4, 5]          # 2026-11-26 是周四，11-28 是周六

# ── 1. 字符串写法（旧）回归基线 ────────────────────────────────────────
# 下面这些期望值是在**改造前**的 scheduler.py 上跑出来的，必须逐条一致。
OLD_CASES = [
    # (说明, sched, now, 期望 slot, 期望 next)
    ("早于计划点", {"mode": "daily", "at": "19:55", "weekdays": WEEKDAYS},
     "2026-11-26 08:00", "2026-11-25 19:55", "2026-11-26 19:55"),
    ("晚于计划点", {"mode": "daily", "at": "19:55", "weekdays": WEEKDAYS},
     "2026-11-26 20:00", "2026-11-26 19:55", "2026-11-27 19:55"),
    ("恰好等于计划点", {"mode": "daily", "at": "19:55", "weekdays": WEEKDAYS},
     "2026-11-26 19:55", "2026-11-26 19:55", "2026-11-27 19:55"),
    ("周六无计划点", {"mode": "daily", "at": "19:55", "weekdays": WEEKDAYS},
     "2026-11-28 09:00", "2026-11-27 19:55", "2026-11-30 19:55"),
    ("monthly 跨月 31 号", {"mode": "monthly", "at": "09:00", "days": [31]},
     "2026-04-01 08:00", "2026-03-31 09:00", "2026-05-31 09:00"),
    ("monthly 当月", {"mode": "monthly", "at": "09:00", "days": [15]},
     "2026-11-26 12:00", "2026-11-15 09:00", "2026-12-15 09:00"),
    ("weekdays 缺省=工作日", {"mode": "daily", "at": "21:00"},
     "2026-11-28 22:00", "2026-11-27 21:00", "2026-11-30 21:00"),
]

# ── 2. 数组写法（新） ────────────────────────────────────────────────
NEW_CASES = [
    # now 在第一个点之前：今天还没到点，slot 落到昨天最后一个点
    ("07:00 → next 今天 07:50", {"mode": "daily", "at": ["07:50", "19:55"], "weekdays": WEEKDAYS},
     "2026-11-26 07:00", "2026-11-25 19:55", "2026-11-26 07:50"),
    # 两档之间：slot 是今早的，next 是今晚的
    ("08:00 → slot 07:50 / next 19:55", {"mode": "daily", "at": ["07:50", "19:55"], "weekdays": WEEKDAYS},
     "2026-11-26 08:00", "2026-11-26 07:50", "2026-11-26 19:55"),
    # 今天两档都过了：next 落到明天第一个点
    ("20:00 → slot 19:55 / next 明早 07:50", {"mode": "daily", "at": ["07:50", "19:55"], "weekdays": WEEKDAYS},
     "2026-11-26 20:00", "2026-11-26 19:55", "2026-11-27 07:50"),
    # 周六：next 跳到周一第一个点
    ("周六 09:00 → next 周一 07:50", {"mode": "daily", "at": ["07:50", "19:55"], "weekdays": WEEKDAYS},
     "2026-11-28 09:00", "2026-11-27 19:55", "2026-11-30 07:50"),
    # 恰好停在第二个点上：slot 取它，next 是明天第一个点（严格大于）
    ("19:55 整 → slot 19:55", {"mode": "daily", "at": ["07:50", "19:55"], "weekdays": WEEKDAYS},
     "2026-11-26 19:55", "2026-11-26 19:55", "2026-11-27 07:50"),
    # 乱序 + 重复：解析后要去重并排序
    ("乱序去重", {"mode": "daily", "at": ["19:55", "07:50", "12:00", "07:50"], "weekdays": WEEKDAYS},
     "2026-11-26 13:00", "2026-11-26 12:00", "2026-11-26 19:55"),
    # monthly 也要支持数组
    ("monthly 数组", {"mode": "monthly", "at": ["07:50", "19:55"], "days": [15]},
     "2026-11-26 12:00", "2026-11-15 19:55", "2026-12-15 07:50"),
]

# ── 3. 边界：一律 None，且不抛异常 ─────────────────────────────────────
EDGE_CASES = [
    ("空数组", {"mode": "daily", "at": [], "weekdays": WEEKDAYS}),
    ("元素全非法", {"mode": "daily", "at": ["25:99", "abc"], "weekdays": WEEKDAYS}),
    ("at=None", {"mode": "daily", "at": None, "weekdays": WEEKDAYS}),
    ("缺 at", {"mode": "daily", "weekdays": WEEKDAYS}),
    ("字符串非法", {"mode": "daily", "at": "25:99", "weekdays": WEEKDAYS}),
]


def test_old_string_at_unchanged():
    bad = []
    for name, sched, now, ws, wn in OLD_CASES:
        gs, gn = _fmt(S.compute_slot(sched, _dt(now))), _fmt(S.compute_next(sched, _dt(now)))
        if (gs, gn) != (ws, wn):
            bad.append("%s: got slot=%r next=%r, want slot=%r next=%r" % (name, gs, gn, ws, wn))
    assert not bad, "\n".join(bad)


def test_new_array_at():
    bad = []
    for name, sched, now, ws, wn in NEW_CASES:
        gs, gn = _fmt(S.compute_slot(sched, _dt(now))), _fmt(S.compute_next(sched, _dt(now)))
        if (gs, gn) != (ws, wn):
            bad.append("%s: got slot=%r next=%r, want slot=%r next=%r" % (name, gs, gn, ws, wn))
    assert not bad, "\n".join(bad)


def test_at_times_normalization():
    assert S._at_times({"at": "07:50"}) == [S.dtime(7, 50)]
    assert S._at_times({"at": ["19:55", "07:50", "07:50"]}) == [S.dtime(7, 50), S.dtime(19, 55)]
    assert S._at_times({"at": []}) == []
    assert S._at_times({}) == []
    assert S._at_times({"at": ["bad", None, "25:99"]}) == []
    # 宽松口径与旧实现一致："7:5" 照样认
    assert S._at_times({"at": "7:5"}) == [S.dtime(7, 5)]


def test_edge_no_raise():
    now = _dt("2026-11-26 08:00")
    for name, sched in EDGE_CASES:
        assert S._at_times(sched) == [], name
        assert S.compute_slot(sched, now) is None, name
        assert S.compute_next(sched, now) is None, name


if __name__ == "__main__":
    fns = [test_old_string_at_unchanged, test_new_array_at,
           test_at_times_normalization, test_edge_no_raise]
    fail = 0
    for fn in fns:
        try:
            fn()
            print("✅ %s" % fn.__name__)
        except AssertionError as e:
            fail += 1
            print("❌ %s\n%s" % (fn.__name__, e))
    print("\n%s" % ("全部通过" if not fail else "%d 个失败" % fail))
    sys.exit(1 if fail else 0)
