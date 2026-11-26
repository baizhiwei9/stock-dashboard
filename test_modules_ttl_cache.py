#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配置缓存（TTL 30s）：命中 / 过期 / force / 写后失效 / 副本隔离

背景
────
`all_modules()` 每个 tick（15s）和每次 `/api/state` 都会调一次，而它的开销
几乎全在 `_case_modules()`：`case_registry.load_tasks()` 在云盘（FUSE）上要
`listdir case/` + 15 个子目录，实测 4~10 秒。于是 tick 实际周期 ≈ 26s，
控制台首屏 10s+。

按 mtime 做指纹省不掉这笔 IO —— 拿 mtime 本身就得先 listdir 一遍。只有
「根本不扫」才省得掉，所以用 TTL 缓存。

这里守的就是缓存不能把行为改坏：
1. 命中时不重扫（否则白缓存）
2. TTL 过期要重扫（新增/改 task.json 最多 30s 生效）
3. force=True 绕过缓存（注册/批准/手动跑要立刻看到刚写下去的东西）
4. save_cfg / save_registry 立即失效（本进程自己写的不等 30s）
5. 取出来的是副本（run_all 会给 mod 打 _manual，不能污染缓存）
6. 真 tick() 连跑两次只扫一次盘
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "case", "_lib"))

import scheduler as S
import case_registry


def _fresh(monkeypatch):
    """清空缓存 + 打桩 case_registry.as_modules，返回调用计数器。"""
    S._cache_drop()
    calls = {"n": 0}

    def fake_as_modules(base=None):
        calls["n"] += 1
        return [{"key": "five_strategy", "title": "五策略", "cmd": "true",
                 "enabled": True, "sched": {"mode": "daily", "at": "08:30",
                                            "weekdays": [1, 2, 3, 4, 5]}}]

    monkeypatch.setattr(case_registry, "as_modules", fake_as_modules)
    return calls


# ── 一、命中 / 过期 / force ───────────────────────────────────────────

def test_cache_hit_does_not_rescan(monkeypatch):
    calls = _fresh(monkeypatch)
    S._case_modules()
    S._case_modules()
    S._case_modules()
    assert calls["n"] == 1, calls


def test_ttl_expiry_rescans(monkeypatch):
    calls = _fresh(monkeypatch)
    S._case_modules()
    # 直接改过期时刻，别真等 30 秒
    with S._MOD_CACHE_LOCK:
        exp, val = S._MOD_CACHE["case"]
        S._MOD_CACHE["case"] = (time.time() - 1, val)
    S._case_modules()
    assert calls["n"] == 2, calls


def test_force_bypasses_cache(monkeypatch):
    calls = _fresh(monkeypatch)
    S._case_modules()
    S._case_modules(force=True)
    assert calls["n"] == 2, calls
    # force 也要回填缓存，之后的普通调用仍然命中
    S._case_modules()
    assert calls["n"] == 2, calls


def test_all_modules_shares_case_cache(monkeypatch):
    """all_modules() 是 tick 与 /api/state 的真实入口，必须吃到同一份缓存。"""
    calls = _fresh(monkeypatch)
    S.all_modules()
    S.all_modules()
    assert calls["n"] == 1, calls


# ── 二、写后失效 ─────────────────────────────────────────────────────

def test_save_cfg_invalidates_cfg_cache(monkeypatch, tmp_path):
    _fresh(monkeypatch)
    cfg_path = str(tmp_path / "modules.json")
    monkeypatch.setattr(S, "CFG_PATH", cfg_path)

    S.save_cfg({"modules": [{"key": "a"}]})
    assert [m["key"] for m in S.load_cfg()["modules"]] == ["a"]
    S.save_cfg({"modules": [{"key": "b"}]})
    assert [m["key"] for m in S.load_cfg()["modules"]] == ["b"]


def test_save_registry_invalidates_registry_cache(monkeypatch, tmp_path):
    _fresh(monkeypatch)
    reg_path = str(tmp_path / "registry.json")
    monkeypatch.setattr(S, "REG_PATH", reg_path)

    S.save_registry({"workers": [{"key": "w1", "status": "approved"}]})
    assert [w["key"] for w in S.load_registry()["workers"]] == ["w1"]
    S.save_registry({"workers": []})
    assert S.load_registry()["workers"] == []


def test_cfg_edit_by_other_process_waits_for_ttl(monkeypatch, tmp_path):
    """别的进程改 modules.json，TTL 内看不到 —— 这是刻意接受的代价。

    为什么不做 mtime 指纹：这块是 FUSE 云盘，mtime 本身不可靠（实测
    registry.json / state.json 的 mtime 时而 1970-01-01、时而真实时间，
    同一个文件隔两秒 stat 两次结果不同）。用不可靠的指纹做失效判断，
    正确性仍要靠 TTL 兜底，只是多一层不好预测的行为。

    本进程自己写的不受这条限制 —— save_cfg / save_registry 主动失效。
    """
    _fresh(monkeypatch)
    cfg_path = str(tmp_path / "modules.json")
    monkeypatch.setattr(S, "CFG_PATH", cfg_path)
    S.save_cfg({"modules": [{"key": "a"}]})
    assert [m["key"] for m in S.load_cfg()["modules"]] == ["a"]

    # 绕过 save_cfg 直接写盘，模拟「别的进程改的」
    time.sleep(0.01)
    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write('{"modules": [{"key": "zzz"}]}')
    assert [m["key"] for m in S.load_cfg()["modules"]] == ["a"]   # TTL 内仍是老值

    with S._MOD_CACHE_LOCK:
        exp, val = S._MOD_CACHE["cfg"]
        S._MOD_CACHE["cfg"] = (time.time() - 1, val)
    assert [m["key"] for m in S.load_cfg()["modules"]] == ["zzz"]  # 过期后生效


def test_new_case_dir_appears_after_ttl(monkeypatch):
    """新增 case 目录最多延迟 30s 生效 —— 这条把「可接受的代价」钉死。"""
    S._cache_drop()
    seen = {"mods": [{"key": "five_strategy", "title": "五策略", "cmd": "true",
                      "enabled": True,
                      "sched": {"mode": "daily", "at": "08:30",
                                "weekdays": [1, 2, 3, 4, 5]}}]}

    monkeypatch.setattr(case_registry, "as_modules", lambda base=None: list(seen["mods"]))

    assert [m["key"] for m in S._case_modules()] == ["five_strategy"]
    seen["mods"].append({"key": "brand_new", "title": "新任务", "cmd": "true",
                         "enabled": True,
                         "sched": {"mode": "daily", "at": "09:00",
                                   "weekdays": [1, 2, 3, 4, 5]}})
    # TTL 内还是老结果
    assert [m["key"] for m in S._case_modules()] == ["five_strategy"]
    # 过期后立刻能看到
    with S._MOD_CACHE_LOCK:
        exp, val = S._MOD_CACHE["case"]
        S._MOD_CACHE["case"] = (time.time() - 1, val)
    assert [m["key"] for m in S._case_modules()] == ["five_strategy", "brand_new"]


# ── 三、副本隔离 ────────────────────────────────────────────────────

def test_cached_value_is_copy(monkeypatch):
    """run_all 会给 mod 打 _manual；不能把这份改动带进缓存。"""
    _fresh(monkeypatch)
    first = S._case_modules()
    first[0]["_manual"] = True
    first.append({"key": "ghost"})
    second = S._case_modules()
    assert "_manual" not in second[0], second
    assert [m["key"] for m in second] == ["five_strategy"], second


def test_all_modules_result_is_copy(monkeypatch):
    calls = _fresh(monkeypatch)
    a = S.all_modules()
    a[0]["enabled"] = False
    b = S.all_modules()
    assert b[0]["enabled"] is True, b
    assert calls["n"] == 1, calls


# ── 四、并发：过期瞬间只扫一次 ──────────────────────────────────────

def test_concurrent_callers_scan_once(monkeypatch):
    """TTL 刚过期时 tick 线程和几个 HTTP 线程会同时进来。

    没有单飞的话它们各扫一遍盘（云盘每次 4~10s），比不缓存还慢；
    有单飞时后来者等锁、拿到别人刚填好的结果。
    """
    import threading
    S._cache_drop()
    calls = {"n": 0}
    lock = threading.Lock()

    def slow_as_modules(base=None):
        with lock:
            calls["n"] += 1
        time.sleep(0.05)                       # 模拟扫盘
        return [{"key": "five_strategy", "title": "五策略", "cmd": "true",
                 "enabled": True, "sched": {"mode": "daily", "at": "08:30",
                                            "weekdays": [1, 2, 3, 4, 5]}}]

    monkeypatch.setattr(case_registry, "as_modules", slow_as_modules)

    results = []
    barrier = threading.Barrier(6)

    def worker():
        barrier.wait()                          # 尽量同时起跑
        results.append(len(S._case_modules()))

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert calls["n"] == 1, calls
    assert results == [1] * 6, results


# ── 五、真 tick() 只扫一次盘 ────────────────────────────────────────

def test_real_tick_scans_once_for_two_ticks(monkeypatch):
    """tick 每 15s 一次；TTL 30s ⇒ 相邻两个 tick 只应扫一次盘。"""
    from datetime import datetime as _dt
    calls = _fresh(monkeypatch)

    t = {"v": _dt(2026, 11, 26, 12, 0, 0)}

    class _DT(_dt):
        @classmethod
        def now(cls, tz=None):
            return t["v"]

    monkeypatch.setattr(S, "datetime", _DT)
    monkeypatch.setattr(S, "load_state", lambda: {"scheduler": {}, "modules": {}})
    monkeypatch.setattr(S, "save_state", lambda st: None)
    monkeypatch.setattr(S, "log", lambda msg: None)
    monkeypatch.setattr(S, "_health_async", lambda *a, **kw: None)
    monkeypatch.setattr(S, "spawn", lambda *a, **kw: True)
    S.PROCS.clear()
    S.ORPHANS.clear()

    S.tick()
    t["v"] = _dt(2026, 11, 26, 12, 0, 15)
    S.tick()
    assert calls["n"] == 1, calls

    # 第三个 tick 跨过 30s TTL → 必须重扫（新增 case 才能生效）
    # TTL 用的是 time.time()（真实时钟），这里直接把它改成已过期，别真等 30 秒
    with S._MOD_CACHE_LOCK:
        exp, val = S._MOD_CACHE["case"]
        S._MOD_CACHE["case"] = (time.time() - 1, val)
    t["v"] = _dt(2026, 11, 26, 12, 0, 40)
    S.tick()
    assert calls["n"] == 2, calls
