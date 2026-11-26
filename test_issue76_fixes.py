#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""issue #76 五条缺陷的最小回归测试（不联网、不连真库）

    python3 test_issue76_fixes.py

覆盖：
  1 build.render_health_section  %d 实参顺序 —— task_health 有行时不能 TypeError
  3 scheduler._datahub_dir()     —— 走 datahub_root()，认 EDB_REPO/同级 checkout
    scheduler._health_record()   —— 记账失败要返回原因，不静默吞掉
  4 manager.cmd_toggle()        —— 真的写进 modules.json（不是改副本）
  5 manager.cmd_sections pw     —— 真的写凭据、弱密码要拦
  6 tech_consensus data_end()   —— 上界默认当天，不写死；None 表示不设上界
    data_fetch.fetch_kline()    —— 不设上界时不被 2026-12-31 截断

第 2 条（publish 只认 case/）的用例在 case/_lib/test_publish.py。
"""
from __future__ import annotations

import datetime
import json
import os
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
TC_DIR = os.path.join(HERE, "case", "tech_consensus")


# ---------------------------------------------------------------- 1 build
class _StubModule(types.ModuleType):
    """build.py 顶层 from data_hub.tech import 一堆名字：一律返回假函数。"""

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *a, **k: None


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *_a, **_k):
        return self

    def fetchall(self):
        return self._rows

    def close(self):
        pass


def _install_datahub_stub(rows, threshold=5):
    """把 data_hub / data_hub.tech / store / health 塞进 sys.modules，返回还原用的旧值。"""
    saved = {k: sys.modules.get(k) for k in
             ("data_hub", "data_hub.tech", "data_hub.tech.store", "data_hub.tech.health")}

    tech = _StubModule("data_hub.tech")
    store = types.ModuleType("data_hub.tech.store")
    store.connect = lambda: _FakeConn(rows)
    health = types.ModuleType("data_hub.tech.health")
    health.THRESHOLD = threshold
    health.record = lambda *a, **k: {"fails": 0, "threshold": threshold, "alerted": False}
    tech.store = store
    tech.health = health
    pkg = _StubModule("data_hub")
    pkg.tech = tech

    sys.modules.update({"data_hub": pkg, "data_hub.tech": tech,
                        "data_hub.tech.store": store, "data_hub.tech.health": health})
    return saved


def _restore(saved, extra=()):
    for k, v in list(saved.items()) + list(extra):
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v


def test_build_health_section_with_rows():
    """task_health 有 1 行以上时必须能渲染（旧代码在此 TypeError）。"""
    rows = [
        {"task_key": "manning_flow", "fails": 2, "first_fail_at": "2026-11-20 18:00:00",
         "last_fail_at": "2026-11-24 18:00:00", "last_ok_at": "2026-11-19 18:00:00",
         "last_error": "指标落库失败", "last_alert_at": None},
        {"task_key": "build", "fails": 0, "first_fail_at": None,
         "last_fail_at": None, "last_ok_at": "2026-11-24 18:10:00",
         "last_error": "", "last_alert_at": None},
    ]
    saved = _install_datahub_stub(rows, threshold=5)
    build_saved = sys.modules.pop("build", None)
    try:
        import build
        html = build.render_health_section()          # 旧代码：TypeError: %d format ...
    finally:
        sys.modules.pop("build", None)
        if build_saved is not None:
            sys.modules["build"] = build_saved
        _restore(saved)

    # 占位符与实参必须一一对应：%d(条) %d(连败) ... %d(阈值)
    assert "2 条 · 1 连败中" in html, html[:200]
    assert "连续 5 次失败发一封邮件" in html, html[-400:]
    assert "manning_flow" in html and "build" in html


def test_build_health_section_empty():
    """空表仍不渲染（保持既有行为，不改契约）。"""
    saved = _install_datahub_stub([])
    sys.modules.pop("build", None)
    try:
        import build
        assert build.render_health_section() == ""
    finally:
        sys.modules.pop("build", None)
        _restore(saved)


# ------------------------------------------------------------ 3 scheduler
def test_scheduler_datahub_dir_uses_datahub_root():
    """_datahub_dir() 必须等于 datahub_path.datahub_root()（含 EDB_REPO 路径）。"""
    import datahub_path
    import scheduler

    tmp = tempfile.mkdtemp()
    os.makedirs(os.path.join(tmp, "data_hub"))        # 足以被 _is_hub_root 认出来
    keep = {k: os.environ.get(k) for k in
            ("DATA_HUB_PATH", "DATAHUB_DIR", "EDB_REPO")}
    try:
        os.environ.pop("DATA_HUB_PATH", None)
        os.environ.pop("DATAHUB_DIR", None)
        os.environ["EDB_REPO"] = tmp
        assert datahub_path.datahub_root() == tmp
        assert scheduler._datahub_dir() == tmp, scheduler._datahub_dir()
    finally:
        for k, v in keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(tmp, ignore_errors=True)


def test_scheduler_health_record_not_silent():
    """记账失败要返回原因（旧代码 return None，日志也看不出根因）。"""
    import scheduler

    health = types.ModuleType("data_hub.tech.health")

    def _boom(*_a, **_k):
        raise RuntimeError("模拟记账失败")

    health.record = _boom
    saved = {k: sys.modules.get(k) for k in
             ("data_hub", "data_hub.tech", "data_hub.tech.health")}
    pkg = _StubModule("data_hub")
    tech = _StubModule("data_hub.tech")
    tech.health = health
    pkg.tech = tech
    sys.modules.update({"data_hub": pkg, "data_hub.tech": tech,
                        "data_hub.tech.health": health})
    try:
        r = scheduler._health_record("issue76_demo", 1, {"cmd": "echo hi"})
    finally:
        _restore(saved)
    assert isinstance(r, dict), r
    assert r.get("recorded") is False, r
    assert "模拟记账失败" in (r.get("reason") or ""), r


# -------------------------------------------------------------- 4 manager
def test_manager_toggle_writes_modules_json():
    """enable/disable 必须落到 modules.json 的原始 dict 上。"""
    import manager

    tmp = tempfile.mkdtemp()
    cfp = os.path.join(tmp, "modules.json")
    shutil.copy(os.path.join(HERE, "modules.json"), cfp)
    orig = manager.CFG
    manager.CFG = cfp
    try:
        manager.cmd_toggle("build", True)
        got = _enabled_map(cfp)
        assert got.get("build") is True, got
        manager.cmd_toggle("build", False)
        got = _enabled_map(cfp)
        assert got.get("build") is False, got
    finally:
        manager.CFG = orig
        shutil.rmtree(tmp, ignore_errors=True)


def _enabled_map(cfp):
    with open(cfp, encoding="utf-8") as f:
        cfg = json.load(f)
    return {m["key"]: m.get("enabled") for m in cfg.get("modules") or []}


# ------------------------------------------------------------ 5 sections pw
def test_manager_sections_pw_writes_secret():
    """sections pw <新密码> 必须真的调 set_value（旧代码是死代码，静默退出 0）。"""
    import manager

    calls = {}

    class _FakeSecrets:
        def set_value(self, section, key, value):
            calls["args"] = (section, key, value)
            return "/tmp/fake-secrets.json"

    orig = manager._secrets
    manager._secrets = lambda: _FakeSecrets()
    try:
        rc = manager.cmd_sections(["pw", "Abcdef123456x"])
    finally:
        manager._secrets = orig
    assert rc == 0
    assert calls.get("args") == ("scheduler", "password", "Abcdef123456x"), calls

    # 弱密码仍要拦
    try:
        manager.cmd_sections(["pw", "123456"])
    except SystemExit as e:
        assert "至少 10 位" in str(e), e
    else:
        raise AssertionError("弱密码没有被拦下")

    # 缺参数仍打用法并非零退出
    try:
        manager.cmd_sections(["pw"])
    except SystemExit as e:
        assert "用法" in str(e), e
    else:
        raise AssertionError("缺参数没有报用法")


# ---------------------------------------------------------- 6 tech_consensus
def test_tech_consensus_data_end_is_dynamic():
    if TC_DIR not in sys.path:
        sys.path.insert(0, TC_DIR)
    import config as tc

    today = datetime.date.today()
    orig = tc.DATA_END
    try:
        assert tc.data_end("dash") == today.isoformat()
        assert tc.data_end("compact") == today.strftime("%Y%m%d")
        tc.DATA_END = ""              # 显式不设上界
        assert tc.data_end() is None
        tc.DATA_END = "2026-12-31"    # 固定窗口复现
        assert tc.data_end("dash") == "2026-12-31"
        assert tc.data_end("compact") == "20261231"
    finally:
        tc.DATA_END = orig


def test_fetch_kline_not_truncated_at_2026():
    """上界不设时 2027 年的数据不能被截断（issue 的复现脚本结论）。"""
    if TC_DIR not in sys.path:
        sys.path.insert(0, TC_DIR)
    import pandas as pd
    import config as tc
    import data_fetch

    dates = pd.bdate_range("2021-01-01", "2027-06-30")
    panel = pd.DataFrame({"date": dates,
                          "open": 1.0, "close": 1.0, "high": 1.0,
                          "low": 1.0, "volume": 1.0})
    orig_src = data_fetch.SOURCE_FETCHERS
    orig_order = data_fetch.SOURCE_ORDER
    orig_end = tc.DATA_END
    data_fetch.SOURCE_FETCHERS = {"fake": lambda code: panel.copy()}
    data_fetch.SOURCE_ORDER = ["fake"]
    try:
        tc.DATA_END = "2026-12-31"
        cut = data_fetch.fetch_kline("sh510300")
        assert str(cut["date"].max().date()) == "2026-12-31"   # 显式覆盖仍然生效
        tc.DATA_END = ""                                       # 不设上界
        full = data_fetch.fetch_kline("sh510300")
        assert str(full["date"].max().date()) == "2027-06-30", full["date"].max()
        tc.DATA_END = None                                     # 默认当天
        now = data_fetch.fetch_kline("sh510300")
        assert str(now["date"].max().date()) <= datetime.date.today().isoformat()
    finally:
        data_fetch.SOURCE_FETCHERS = orig_src
        data_fetch.SOURCE_ORDER = orig_order
        tc.DATA_END = orig_end


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print("ok  %s" % fn.__name__)
    print("%d 个用例全部通过" % len(tests))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())