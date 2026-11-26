#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""直接驱动真 tick()：宽限文案不该覆盖成功状态 + not_before 拦提前拉

背景
────
这两件事的共同点是「只复刻 tick 里某一段的过滤，测不出真问题」：

1. `missed_slot` 那段写在 `if ms["last_slot"] == slotkey: continue` **之前**。
   08:30 的计划点 08:34 跑成功后，6 小时宽限一过，`compute_slot` 又把 08:30
   回看回来，于是 tick 把 note 补写成「超过宽限未执行」—— last_status=ok、
   finished=08:34、note 却说没执行，自相矛盾，一直挂到次日 spawn 才被清
   （`_finish` 不碰 note）。看板每天宽限之后开始说假话。

2. `not_before` 的拦截只在 tick 的提前拉分支里。原来的用例复刻了这段过滤，
   把 tick 里真正的 `if nb is not None and now.time() < nb: continue` 删掉
   测试照样全过 —— 以后有人在 tick 层动它拦不住。

所以这里一律 monkeypatch 掉外部副作用（读写 state、起进程、写日志、发 health），
直接调 `S.tick()`，断言真实行为。
"""
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "case", "_lib"))

import scheduler as S


def _run_tick(monkeypatch, mods, state, now):
    """驱动真 tick()，返回被 spawn 的 [(key, pulled), ...]。"""
    spawned = []
    S.PROCS.clear()
    S.ORPHANS.clear()

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(S, "datetime", _DT)
    monkeypatch.setattr(S, "load_state", lambda: state)
    monkeypatch.setattr(S, "save_state", lambda st: None)
    monkeypatch.setattr(S, "all_modules", lambda: mods)
    monkeypatch.setattr(S, "log", lambda msg: None)
    monkeypatch.setattr(S, "_health_async", lambda *a, **kw: None)

    def _spawn(key, m, slot, st, pulled=False):
        spawned.append((key, pulled))
        return True

    monkeypatch.setattr(S, "spawn", _spawn)
    S.tick()
    return spawned


def _mod(key="five_strategy", at="08:30", **extra):
    m = {"key": key, "enabled": True, "cmd": "true",
         "sched": {"mode": "daily", "at": at, "weekdays": [1, 2, 3, 4, 5]}}
    m.update(extra)
    return m


# ── 一、宽限文案不该覆盖成功状态 ──────────────────────────────────────

def test_success_not_marked_missed_after_grace(monkeypatch):
    """08:30 跑成功、15:00 tick（跨过 6h 宽限）：note 必须保持空，不能写「超过宽限未执行」。"""
    now = datetime(2026, 11, 26, 15, 0)
    state = {"modules": {"five_strategy": {
        "last_slot": "2026-11-26 08:30",
        "last_status": "ok",
        "finished": "2026-11-26 08:34:12",
        "note": "",
    }}, "scheduler": {}}
    _run_tick(monkeypatch, [_mod()], state, now)
    ms = state["modules"]["five_strategy"]
    assert ms.get("note") == "", "成功任务被写成「%s」" % ms.get("note")
    assert not ms.get("missed_slot"), "成功任务被记 missed_slot=%s" % ms.get("missed_slot")
    assert ms.get("last_status") == "ok"


def test_failed_but_ran_not_marked_missed(monkeypatch):
    """当天失败过但 last_slot 已写：同样不该再刷超宽限，也别改状态。"""
    now = datetime(2026, 11, 26, 15, 0)
    state = {"modules": {"five_strategy": {
        "last_slot": "2026-11-26 08:30",
        "last_status": "fail",
        "note": "",
    }}, "scheduler": {}}
    _run_tick(monkeypatch, [_mod()], state, now)
    ms = state["modules"]["five_strategy"]
    assert ms.get("note") == "", "note 被覆盖成「%s」" % ms.get("note")
    assert ms.get("last_status") == "fail", "状态被改了"


def test_truly_missed_still_marked(monkeypatch):
    """真没跑（last_slot 是昨天）：必须照旧记 missed_slot + note，别把提醒改没了。"""
    now = datetime(2026, 11, 26, 15, 0)
    state = {"modules": {"five_strategy": {"last_slot": "2026-11-25 08:30"}}, "scheduler": {}}
    _run_tick(monkeypatch, [_mod()], state, now)
    ms = state["modules"]["five_strategy"]
    assert ms.get("missed_slot") == "2026-11-26 08:30", ms.get("missed_slot")
    assert "超过宽限未执行" in (ms.get("note") or ""), ms.get("note")


def test_within_grace_still_spawns(monkeypatch):
    """08:35 tick（未超宽限）：照常起任务，别误伤正常路径。"""
    now = datetime(2026, 11, 26, 8, 35)
    state = {"modules": {}, "scheduler": {}}
    got = _run_tick(monkeypatch, [_mod()], state, now)
    assert ("five_strategy", False) in got, got


def test_past_grace_not_spawned(monkeypatch):
    """15:00 tick（超 6h 宽限）且没跑过：不补跑。"""
    now = datetime(2026, 11, 26, 15, 0)
    state = {"modules": {}, "scheduler": {}}
    got = _run_tick(monkeypatch, [_mod()], state, now)
    assert got == [], "超宽限不该起：%s" % got


# ── 二、not_before 在真 tick() 里拦得住（补 tick 层覆盖）────────────────

def test_real_tick_not_before_blocks_pull(monkeypatch):
    """at=23:20 / nb=23:20，22:50 的 tick 不许提前拉起。"""
    now = datetime(2026, 11, 26, 22, 50)
    state = {"modules": {}, "scheduler": {}}
    got = _run_tick(monkeypatch,
                    [_mod("tech_snapshot", at="23:20", not_before="23:20")], state, now)
    assert got == [], "被提前拉起：%s" % got


def test_real_tick_not_before_earlier_allows_pull(monkeypatch):
    """at=23:20 / nb=22:30，22:50 应该提前拉起（pulled=True）。"""
    now = datetime(2026, 11, 26, 22, 50)
    state = {"modules": {}, "scheduler": {}}
    got = _run_tick(monkeypatch,
                    [_mod("tech_snapshot", at="23:20", not_before="22:30")], state, now)
    assert ("tech_snapshot", True) in got, got


def test_real_tick_no_not_before_pulls_widest(monkeypatch):
    """不配 not_before：20:00 就该被提前拉起（提前量 240 分钟够到 23:20）。"""
    now = datetime(2026, 11, 26, 20, 0)
    state = {"modules": {}, "scheduler": {}}
    got = _run_tick(monkeypatch, [_mod("tech_snapshot", at="23:20")], state, now)
    assert ("tech_snapshot", True) in got, got

def test_real_tick_pull_forward_survives_yesterday_slot(monkeypatch):
    """昨天按点跑过、今天还没到点：提前拉取必须照常生效。

    #95 第一版把「已跑过就 continue」放在了 `slot.date() == now.date()` 外层。
    那时 slot 是**昨天**的计划点，提前 continue 就再也走不到下面
    `slot = None` → 提前拉取分支，等于 16:00 之后整个 pull_forward 被关掉：
    12 个任务全部退回卡点跑（实测 16:00~22:00 一个都不提前起）。
    """
    now = datetime(2026, 11, 26, 16, 0)
    state = {"modules": {"option": {
        "last_slot": "2026-11-25 18:10", "last_status": "ok", "note": ""}},
        "scheduler": {}}
    got = _run_tick(monkeypatch, [_mod("option", at="18:10")], state, now)
    assert ("option", True) in got, "提前拉取被关掉了：%s" % got
