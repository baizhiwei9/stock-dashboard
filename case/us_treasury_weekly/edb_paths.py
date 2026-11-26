#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EDB 库路径解析 —— ``case/us_treasury_weekly`` 内唯一口径。

背景
────
2026-11-26 起 EDB 各表并入 DataHub **统一库**（datahub-multi-tier #93 的
``data_hub/core/dbpath.py``）。旧文件 ``<repo>/data_hub/edb/data/edb.sqlite3``
不再写入、但迁移设计上原样保留，所以「只按 ``os.path.exists`` 探旧文件」会永远
命中，每周静默读到冻结数据（PR #89 已改成统一库优先）。

本模块把「统一库在哪、能不能读」收敛到一处，口径与 ``data_hub.core.dbpath``
对齐：

1. ``DB_PATH`` → ``DB_Path`` → ``DATAHUB_DB_PATH``，取第一个非空；**只要设置了
   就以它为准**（不因为文件尚未创建就掉到默认库 —— 那会读到另一个库的数据）；
2. 值是已存在的目录或以路径分隔符结尾 → 拼 ``datahub.db``；
3. 相对路径 → 按当前工作目录展开为绝对（与 data_hub 的 ``abspath`` 一致）；
4. 都没设 → ``${DATAHUB_HOME:-~/.local/share/datahub}/datahub.db``；
5. **统一库必须存在且真的有 ``edb_points`` 表才选它**。否则
   （``DATAHUB_DB_AUTO_MIGRATE=0`` / 迁移失败 / 新机器 / ``DB_PATH`` 指到
   别的项目的库）回退旧 ``edb.sqlite3`` 并打一条 warning —— 避免「读到空
   却以为没有数据」，也避免把旧库里还有效的数据挡在外面。

只解析路径，**不 import data_hub**：报告脚本不该因为查路径触发
``ensure_db()`` 的建库/迁移副作用。也不做进程内缓存 —— 同进程里环境变量可能变。
"""
from __future__ import annotations

import importlib.util
import logging
import os
import sqlite3
import sys
from typing import List, Optional

__all__ = [
    "ENV_KEYS",
    "LEGACY_DB_REL",
    "unified_db_path",
    "has_edb_points",
    "legacy_candidates",
    "edb_db_path",
]

#: 环境变量探测顺序（与 data_hub.core.dbpath.ENV_KEYS 一致）
ENV_KEYS = ("DB_PATH", "DB_Path", "DATAHUB_DB_PATH")

#: 旧 EDB 库相对 DataHub 根的路径
LEGACY_DB_REL = os.path.join("data_hub", "edb", "data", "edb.sqlite3")

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))  # .../stock-dashboard
_log = logging.getLogger(__name__)
_WARNED = set()


def _warn_once(message: str, *args) -> None:
    """同一条告警每进程只打一次（_edb_db_path 会被每个序列各调一次）。"""
    key = (message, args)
    if key in _WARNED:
        return
    _WARNED.add(key)
    _log.warning(message, *args)


def _default_db_file() -> str:
    """默认统一库：``${DATAHUB_HOME:-~/.local/share/datahub}/datahub.db``。"""
    home = (os.environ.get("DATAHUB_HOME") or "").strip()
    base = (os.path.expanduser(home) if home
            else os.path.join(os.path.expanduser("~"), ".local", "share", "datahub"))
    return os.path.join(base, "datahub.db")


def unified_db_path() -> str:
    """按 data_hub 口径解析统一库路径（不保证文件已存在）。"""
    for key in ENV_KEYS:
        raw = (os.environ.get(key) or "").strip()
        if not raw:
            continue
        path = os.path.expanduser(raw)
        if os.path.isdir(path) or raw.endswith(os.sep):
            path = os.path.join(path, "datahub.db")
        return os.path.abspath(path)
    return _default_db_file()


def has_edb_points(path: str) -> bool:
    """统一库 ``path`` 是否可用作 EDB 源（存在 + 有 ``edb_points`` 表）。"""
    if not (path and os.path.exists(path)):
        return False
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=10)
        try:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='edb_points'"
            ).fetchone()
        finally:
            conn.close()
        return row is not None
    except Exception:                                    # noqa: BLE001
        return False


def _datahub_root() -> str:
    """复用仓库根 ``datahub_path.py`` 的 DataHub 根口径（不污染 sys.path）。"""
    path = os.path.join(_REPO_ROOT, "datahub_path.py")
    if not os.path.exists(path):
        return ""
    try:
        spec = importlib.util.spec_from_file_location("_sd_datahub_path", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)                  # type: ignore[union-attr]
        return module.datahub_root() or ""
    except Exception:                                    # noqa: BLE001
        return ""


def legacy_candidates() -> List[str]:
    """旧库探测用的 DataHub 根候选（EDB_REPO 优先，其次仓库统一口径 + 历史挂载）。"""
    out: List[str] = []
    env = (os.environ.get("EDB_REPO") or "").strip()
    if env:
        out.append(env)
    root = _datahub_root()
    if root:
        out.append(root)
    out += [
        "/opt/coze/.fuse_drive/dataHub_三级体系",
        os.path.join(_REPO_ROOT, "dataHub_三级体系"),
    ]
    seen, uniq = set(), []
    for p in out:
        ap = os.path.abspath(os.path.expanduser(p))
        if ap not in seen:
            seen.add(ap)
            uniq.append(ap)
    return uniq


def edb_db_path() -> Optional[str]:
    """EDB 现用库：统一库（校验过 ``edb_points``）优先，否则旧 ``edb.sqlite3``。

    Returns:
        库绝对路径；两边都没有时返回 ``None``（调用方按「无 EDB」处理）。
    """
    unified = unified_db_path()
    if has_edb_points(unified):
        return unified
    if os.path.exists(unified):
        _warn_once(
            "[edb_paths] 统一库 %s 里没有 edb_points 表（迁移没跑？），"
            "回退旧 edb.sqlite3", unified)
    for repo in legacy_candidates():
        p = os.path.join(repo, LEGACY_DB_REL)
        if os.path.exists(p):
            return p
    return None


if __name__ == "__main__":  # pragma: no cover - 排障用
    print("unified:", unified_db_path(), "| edb:", edb_db_path())
    print("legacy candidates:", legacy_candidates())
    print("python:", sys.version.split()[0])
