#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scheduler 的 health 输出与 restart 退出码口径（承接 #82）。

#82 修掉了「pid == -1 被判健康 / 被判已在运行」的静默停摆，但留了两个会误导人的口子：

  1. ``pid == -1`` 且心跳新鲜时打印「健康 pid=-1 心跳 10s 前」——
     -1 不是进程号，看日志的人会以为本机有个 pid 为 -1 的进程。
  2. restart 在 stop 失败后无条件 cmd_start()：如果停不掉的是**本机**实例
     （pid > 0，kill 失败），cmd_start 只会打印「已在运行」并返回 0，
     restart 对外宣称成功，实际什么都没重启。

跑法：
    python3 -m pytest test_scheduler_restart_honesty.py -q
    python3 test_scheduler_restart_honesty.py
"""
from __future__ import annotations

import contextlib
import io
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import scheduler  # noqa: E402


@contextlib.contextmanager
def _stubbed(**attrs):
    """临时替换 scheduler 模块属性，退出时逐一还原。"""
    saved = {k: getattr(scheduler, k) for k in attrs}
    for k, v in attrs.items():
        setattr(scheduler, k, v)
    try:
        yield
    finally:
        for k, v in saved.items():
            setattr(scheduler, k, v)


def _fake_time():
    return types.SimpleNamespace(sleep=lambda s: None,
                                 time=lambda: 0)


def _run_health(pid, age):
    out = io.StringIO()
    with _stubbed(read_pid=lambda: pid, heartbeat_age=lambda: age,
                  api_conf=lambda: {"port": 8767}):
        with contextlib.redirect_stdout(out):
            rc = scheduler.cmd_health()
    return rc, out.getvalue().strip()


def _run_restart(stop_rc, pid_after_stop):
    calls = []

    def _stop():
        calls.append("stop")
        return stop_rc

    def _start():
        calls.append("start")
        return 0

    out = io.StringIO()
    with _stubbed(cmd_stop=_stop, cmd_start=_start,
                  read_pid=lambda: pid_after_stop, time=_fake_time()):
        with contextlib.redirect_stdout(out):
            rc = scheduler.main(["scheduler.py", "restart"])
    return rc, out.getvalue().strip(), calls


# ─────────────────────────── health 输出 ───────────────────────────
def test_health_does_not_print_pid_minus_one():
    """端口响应源的心跳新鲜时判健康，但不能把 -1 当 pid 打印出来。"""
    rc, text = _run_health(-1, 10)
    assert rc == 0, (rc, text)
    assert "pid=-1" not in text, text
    assert "8767" in text and "心跳 10s 前" in text, text


def test_health_stale_foreign_source_also_avoids_pid_minus_one():
    rc, text = _run_health(-1, 999)
    assert rc == 2, (rc, text)
    assert "pid=-1" not in text, text
    assert "僵死" in text, text


def test_health_local_pid_still_prints_pid():
    rc, text = _run_health(4242, 10)
    assert rc == 0, (rc, text)
    assert "pid=4242" in text, text


def test_health_foreign_no_heartbeat_is_not_running():
    """#82 的核心判定不能被这次改动带回去。"""
    out = io.StringIO()
    with _stubbed(read_pid=lambda: -1, heartbeat_age=lambda: None,
                  api_conf=lambda: {"port": 8767},
                  _foreign_instance_hint=lambda: "端口 8767 有响应但本机无调度器进程"):
        with contextlib.redirect_stdout(out):
            rc = scheduler.cmd_health()
    assert rc == 1, out.getvalue()
    assert "未运行" in out.getvalue(), out.getvalue()


# ─────────────────────────── restart 退出码 ───────────────────────────
def test_restart_reports_failure_when_local_pid_survives_stop():
    """停不掉的是本机实例时，不能对外宣称重启成功。"""
    rc, text, calls = _run_restart(stop_rc=1, pid_after_stop=4242)
    assert rc == 1, (rc, text)
    assert calls == ["stop"], calls          # 不该再去 start
    assert "仍在运行 pid=4242" in text, text


def test_restart_continues_when_port_held_by_foreign_instance():
    """端口被别处占着（pid == -1）时仍要拉起本机实例 —— #82 的行为保持。"""
    rc, text, calls = _run_restart(stop_rc=1, pid_after_stop=-1)
    assert rc == 0, (rc, text)
    assert calls == ["stop", "start"], calls


def test_restart_starts_after_clean_stop():
    rc, text, calls = _run_restart(stop_rc=0, pid_after_stop=None)
    assert rc == 0, (rc, text)
    assert calls == ["stop", "start"], calls


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    bad = 0
    for fn in fns:
        try:
            fn()
            print("ok   %s" % fn.__name__)
        except AssertionError as e:                       # noqa: BLE001
            bad += 1
            print("FAIL %s: %s" % (fn.__name__, e))
    print("\n%d passed, %d failed" % (len(fns) - bad, bad))
    raise SystemExit(1 if bad else 0)