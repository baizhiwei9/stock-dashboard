#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``case/us_treasury_weekly/edb_paths.py`` 的回归测试（不联网、不连真库）。

    python3 -m pytest test_edb_paths.py -q

覆盖 PR #89 复核后补的口径：

1. 统一库有 ``edb_points`` → 选统一库；
2. 统一库存在但没有 ``edb_points``（迁移关掉/失败、DB_PATH 指到别的库）
   → **回退旧 edb.sqlite3**，不能静默读空；
3. ``DB_PATH`` 指一个还没创建的库时 → 不许掉到默认库（那会读到另一个库）；
4. ``DATAHUB_HOME`` 参与默认路径；
5. 目录 / 相对路径 / ``EDB_REPO`` 兜底；
6. 统一库**有表但 0 行**（迁移没跑/还没采）同样回退，且告警每进程一次。
"""
from __future__ import annotations

import logging
import os
import sqlite3
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
CASE = os.path.join(HERE, "case", "us_treasury_weekly")
if CASE not in sys.path:
    sys.path.insert(0, CASE)

import edb_paths  # noqa: E402


def _make_db(path: str, *, edb_points: bool = True) -> str:
    """造一个库：默认带 edb_points 一行数据，edb_points=False 时只有别的表。"""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        if edb_points:
            conn.execute(
                "CREATE TABLE edb_points(series_id TEXT, date TEXT, value REAL)")
            conn.execute(
                "INSERT INTO edb_points(series_id, date, value)"
                " VALUES('RATE.US.10Y', '2026-11-25', 4.2)")
        else:
            conn.execute("CREATE TABLE hub_points(data_type TEXT)")
        conn.commit()
    finally:
        conn.close()
    return path


@pytest.fixture()
def clean_env(monkeypatch):
    for key in ("DB_PATH", "DB_Path", "DATAHUB_DB_PATH", "DATAHUB_HOME", "EDB_REPO"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _reset_warn_cache():
    """每条用例前清掉 _warn_once 的去重缓存，保证告警计数可断言。"""
    edb_paths._WARNED.clear()
    yield
    edb_paths._WARNED.clear()


def _legacy_edb(tmp_path) -> str:
    return _make_db(str(tmp_path / "repo" / "data_hub" / "edb" / "data" / "edb.sqlite3"))


def test_unified_with_edb_points_is_preferred(tmp_path, monkeypatch, clean_env):
    db = _make_db(str(tmp_path / "main.db"))
    monkeypatch.setenv("DB_PATH", db)
    assert edb_paths.unified_db_path() == db
    assert edb_paths.edb_db_path() == db


def test_unified_without_edb_points_falls_back_to_legacy(tmp_path, monkeypatch, clean_env):
    unified = _make_db(str(tmp_path / "main.db"), edb_points=False)
    old = _legacy_edb(tmp_path)
    monkeypatch.setenv("DB_PATH", unified)
    monkeypatch.setenv("EDB_REPO", str(tmp_path / "repo"))
    assert edb_paths.has_edb_points(unified) is False
    assert edb_paths.edb_db_path() == old


def test_env_pointing_to_missing_file_does_not_use_default(tmp_path, monkeypatch, clean_env):
    # 默认库存在（另一次实验/另一台机器的库），但 DB_PATH 指的是尚未创建的库
    _make_db(str(tmp_path / "home" / ".local" / "share" / "datahub" / "datahub.db"))
    old = _legacy_edb(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "not-yet" / "datahub.db"))
    monkeypatch.setenv("EDB_REPO", str(tmp_path / "repo"))
    assert edb_paths.unified_db_path() == str(tmp_path / "not-yet" / "datahub.db")
    assert edb_paths.edb_db_path() == old


def test_datahub_home_is_honored(tmp_path, monkeypatch, clean_env):
    db = _make_db(str(tmp_path / "custom" / "datahub.db"))
    monkeypatch.setenv("DATAHUB_HOME", str(tmp_path / "custom"))
    assert edb_paths.unified_db_path() == db
    assert edb_paths.edb_db_path() == db


def test_directory_and_relative_paths(tmp_path, monkeypatch, clean_env):
    d = tmp_path / "hubdir"
    db = _make_db(str(d / "datahub.db"))
    monkeypatch.setenv("DB_PATH", str(d))
    assert edb_paths.unified_db_path() == db

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DB_PATH", "rel/datahub.db")
    assert edb_paths.unified_db_path() == str(tmp_path / "rel" / "datahub.db")


def test_alias_env_and_legacy_repo(tmp_path, monkeypatch, clean_env):
    db = _make_db(str(tmp_path / "alias.db"))
    monkeypatch.setenv("DB_Path", db)          # 历史别名
    assert edb_paths.unified_db_path() == db

    old = _legacy_edb(tmp_path)
    monkeypatch.setenv("EDB_REPO", str(tmp_path / "repo"))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "missing.db"))
    assert edb_paths.edb_db_path() == old


def test_no_db_anywhere_returns_none(tmp_path, monkeypatch, clean_env):
    """两边都没有 → None。

    2026-11-26（#92 复核）：原来只把 EDB_REPO / DATA_HUB_PATH 指到空 tmp，
    但 ``legacy_candidates()`` 末尾还会追加 ``datahub_path.DEFAULT_ROOT``
    （``/opt/coze/.fuse_drive/dataHub_三级体系``）。在真跑生产的机器上那里
    有旧库，这条用例会**误失败**。所以把仓库兜底也堵掉，只测「两边都没有」。
    """
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("EDB_REPO", str(tmp_path / "nowhere"))
    monkeypatch.setattr(edb_paths, "_datahub_root", lambda: "")
    monkeypatch.setattr(edb_paths, "legacy_candidates", lambda: [])
    assert edb_paths.edb_db_path() is None


def test_unified_empty_edb_points_falls_back(tmp_path, monkeypatch, clean_env, caplog):
    """表在但 0 行 = 不可用：回退旧库，且同一条告警每进程只打一次。"""
    unified = _make_db(str(tmp_path / "main.db"), edb_points=False)
    conn = sqlite3.connect(unified)
    conn.execute("CREATE TABLE edb_points(series_id TEXT, date TEXT, value REAL)")
    conn.commit()
    conn.close()
    old = _legacy_edb(tmp_path)
    monkeypatch.setenv("DB_PATH", unified)
    monkeypatch.setenv("EDB_REPO", str(tmp_path / "repo"))

    assert edb_paths.edb_points_state(unified) == "empty"
    assert edb_paths.has_edb_points(unified) is False
    with caplog.at_level(logging.WARNING):
        assert edb_paths.edb_db_path() == old
        assert edb_paths.edb_db_path() == old          # 第二次调用不再重复告警
    msgs = [r.getMessage() for r in caplog.records
            if r.name.endswith("edb_paths") and "空表" in r.getMessage()]
    assert len(msgs) == 1


def test_edb_points_state_missing_and_ok(tmp_path, clean_env):
    assert edb_paths.edb_points_state(str(tmp_path / "nope.db")) == "missing"
    assert edb_paths.edb_points_state("") == "missing"
    ok = _make_db(str(tmp_path / "ok.db"))
    assert edb_paths.edb_points_state(ok) == "ok"
