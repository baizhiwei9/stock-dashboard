#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tech_snapshot 回归测试（不联网、不连真库）

    python3 test_tech_snapshot.py

覆盖 4 条：
  1 build._cloud_snapshot_path()  文件名/目录一律问 store 要，不写死
                                  （写死 tech_metrics.db.gz 与 datahub.db.gz 都各错过一次）
  2 build._snapshot_note()        快照不存在时如实说「未生成」，不暗示文件已存在
  3 case/_tools/push_snapshot.py  store.snapshot() 返回空时 exit 1
                                  （`python -m ...store --snapshot` 只打印 None 就 exit 0）
  4 datahub_path.expand_root()     {{REPO_ROOT}} 与 {{DATAHUB_ROOT}} 都能展开
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "case", "_tools"))

import push_snapshot                                          # noqa: E402


class _StubModule(types.ModuleType):
    """build.py 顶层 from data_hub.tech import 一堆名字：一律返回假函数。"""

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *a, **k: None


def _install_store_stub(db_name: str, cloud: str, snapshot_ret=None,
                        snapshot_raises: Exception = None):
    """塞入 data_hub / data_hub.tech / store / core.localdb，返回还原用的旧值。"""
    keys = ("data_hub", "data_hub.tech", "data_hub.tech.store",
            "data_hub.core", "data_hub.core.localdb")
    saved = {k: sys.modules.get(k) for k in keys}

    store = types.ModuleType("data_hub.tech.store")
    store.DB_NAME = db_name
    store.cloud_dir = lambda: cloud

    def _snap():
        if snapshot_raises is not None:
            raise snapshot_raises
        return snapshot_ret

    store.snapshot = _snap

    localdb = types.ModuleType("data_hub.core.localdb")
    localdb.snapshot_path = lambda name, cloud_dir: os.path.join(
        cloud_dir, os.path.basename(name) + ".gz")

    core = _StubModule("data_hub.core")
    core.localdb = localdb
    tech = _StubModule("data_hub.tech")
    tech.store = store
    pkg = _StubModule("data_hub")
    pkg.tech = tech
    pkg.core = core

    sys.modules.update({"data_hub": pkg, "data_hub.tech": tech,
                        "data_hub.tech.store": store,
                        "data_hub.core": core, "data_hub.core.localdb": localdb})
    return saved


def _restore(saved):
    for k, v in saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v


def _fresh_build():
    """按**本仓库**路径重新加载 build，不受其它用例（或同名模块）污染。

    全量 pytest 时 `build` 这个名字可能已经被别的用例塞进 sys.modules，
    直接 `import build` 拿到的未必是仓库根这一份。
    """
    import importlib.util
    sys.modules.pop("build", None)
    spec = importlib.util.spec_from_file_location("build", os.path.join(HERE, "build.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["build"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------- 1 快照路径
def test_snapshot_path_follows_store_not_hardcoded():
    """文件名/目录都来自 store：换 DB_NAME 或 cloud_dir，路径跟着变。"""
    saved = _install_store_stub("tech_metrics.db", "/tmp/xx/output")
    try:
        build = _fresh_build()
        assert build._cloud_snapshot_path() == "/tmp/xx/output/tech_metrics.db.gz"
    finally:
        sys.modules.pop("build", None)
        _restore(saved)

    # 库里若改成别的 DB_NAME，页面必须跟着改，而不是继续写死 tech_metrics
    saved = _install_store_stub("datahub.db", "/tmp/yy/out")
    try:
        build = _fresh_build()
        assert build._cloud_snapshot_path() == "/tmp/yy/out/datahub.db.gz"
    finally:
        sys.modules.pop("build", None)
        _restore(saved)


def test_snapshot_path_empty_when_store_broken():
    """store 不可用时返回空串（调用方据此报「路径解析失败」，不编一个假路径）。"""
    keys = ("data_hub", "data_hub.tech", "data_hub.tech.store")
    saved = {k: sys.modules.get(k) for k in keys}
    for k in keys:
        sys.modules.pop(k, None)
    sys.modules["data_hub"] = _StubModule("data_hub")
    sys.modules["data_hub.tech"] = _StubModule("data_hub.tech")
    broken = types.ModuleType("data_hub.tech.store")

    def _boom():
        raise RuntimeError("库不可用")

    broken.cloud_dir = _boom
    sys.modules["data_hub.tech.store"] = broken
    try:
        build = _fresh_build()
        assert build._cloud_snapshot_path() == ""
        assert "路径解析失败" in build._snapshot_note()
    finally:
        sys.modules.pop("build", None)
        _restore(saved)


# ---------------------------------------------------------- 2 快照文案
def test_snapshot_note_says_not_generated_when_missing():
    tmp = tempfile.mkdtemp()
    saved = _install_store_stub("tech_metrics.db", tmp)
    try:
        build = _fresh_build()
        note = build._snapshot_note()
        assert "未生成" in note, note
        assert "tech_snapshot" in note, note
        assert os.path.join(tmp, "tech_metrics.db.gz") in note, note
    finally:
        sys.modules.pop("build", None)
        _restore(saved)
        shutil.rmtree(tmp, ignore_errors=True)


def test_snapshot_note_reports_size_and_mtime_when_present():
    tmp = tempfile.mkdtemp()
    p = os.path.join(tmp, "tech_metrics.db.gz")
    with open(p, "wb") as f:
        f.write(b"x" * (2 * 1024 * 1024))
    saved = _install_store_stub("tech_metrics.db", tmp)
    try:
        build = _fresh_build()
        note = build._snapshot_note()
        assert "未生成" not in note, note
        assert "2.0 MB" in note, note
        assert "20" in note and ":" in note, note        # 带时间
    finally:
        sys.modules.pop("build", None)
        _restore(saved)
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------- 3 push_snapshot
def test_push_snapshot_exits_nonzero_when_snapshot_fails():
    """快照推不上去必须 exit 1 —— 否则 health 永远记成功，没人知道快照没生成。"""
    saved = _install_store_stub("tech_metrics.db", "/tmp/zz/output", snapshot_ret=None)
    try:
        rc = push_snapshot.main([])
    finally:
        _restore(saved)
    assert rc == 1, rc


def test_push_snapshot_exits_zero_and_checks_path():
    tmp = tempfile.mkdtemp()
    target = os.path.join(tmp, "tech_metrics.db.gz")
    with open(target, "wb") as f:
        f.write(b"ok")
    saved = _install_store_stub("tech_metrics.db", tmp, snapshot_ret=target)
    try:
        rc = push_snapshot.main([])
    finally:
        _restore(saved)
        shutil.rmtree(tmp, ignore_errors=True)
    assert rc == 0, rc


def test_push_snapshot_rejects_unexpected_path():
    """store.snapshot() 落到别处说明口径已经不一致，不能当成功。"""
    tmp = tempfile.mkdtemp()
    other = os.path.join(tmp, "somewhere_else.db.gz")
    with open(other, "wb") as f:
        f.write(b"ok")
    saved = _install_store_stub("tech_metrics.db", tmp, snapshot_ret=other)
    try:
        rc = push_snapshot.main([])
    finally:
        _restore(saved)
        shutil.rmtree(tmp, ignore_errors=True)
    assert rc == 1, rc


def test_push_snapshot_print_only():
    saved = _install_store_stub("tech_metrics.db", "/tmp/pp/output")
    try:
        rc = push_snapshot.main(["--print"])
    finally:
        _restore(saved)
    assert rc == 0, rc


# ----------------------------------------------------- 4 占位符展开
def test_expand_root_handles_repo_root():
    import datahub_path

    got = datahub_path.expand_root(
        'cd {{REPO_ROOT}} && PYTHONPATH={{DATAHUB_ROOT}} python3 x.py',
        root="/hub", repo="/repo")
    assert got == "cd /repo && PYTHONPATH=/hub python3 x.py", got


def test_expand_module_keeps_other_fields():
    import datahub_path

    m = {"key": "tech_snapshot", "cwd": "{{REPO_ROOT}}",
         "cmd": "PYTHONPATH={{DATAHUB_ROOT}} python3 x.py", "timeout": 900}
    out = datahub_path.expand_module(m, root="/hub", repo="/repo")
    assert out["cwd"] == "/repo" and "/hub" in out["cmd"], out
    assert out["timeout"] == 900 and out["key"] == "tech_snapshot", out
    assert m["cwd"] == "{{REPO_ROOT}}", "expand_module 必须返回副本，不改原对象"


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print("ok  %s" % fn.__name__)
    print("%d 个用例全部通过" % len(tests))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
