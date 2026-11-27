#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""去云盘化的回归用例（2026-11-26）。

背景
────
21 条日更/周更任务从 Coze 云主机搬进 ``case/`` 后，脚本里仍写死三类绝对根：

    /Coze/Drive/...              FUSE 云盘主视图
    /opt/coze/.fuse_drive/...    FUSE 云盘别名视图
    /app/data/...                旧沙箱数据目录

换一台机器这三条都不存在。而它们大多被 try/except 或 ``os.path.isdir`` 包着，
表现不是崩溃，而是「某块数据一直是空的」—— 比直接报错更难查。

现在统一走 ``case/_lib/cloud_paths.py``：字面量必须包在 ``_cp(...)`` 里，
由 ``CASE_DRIVE_ROOT`` / ``CASE_FUSE_ROOT`` / ``CASE_APP_DATA`` 重定向；
三个变量都不设时 ``translate()`` 原样返回，云主机行为零变化。

本用例守两件事：
  1. 切换清单里的 21 个 key，.py / .sh 里没有「裸」旧根字面量（注释和文档字符串不算）；
  2. ``cloud_paths.translate`` 在设了变量时确实换根，没设时原样返回。
"""
import ast
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "case", "_lib"))

import cloud_paths  # noqa: E402

#: 与 scripts/switch_crontab.sh 的 KEYS 保持一致（顺序也要一致，末尾对账）。
SWITCH_KEYS = [
    "midline_preference", "circulation_daily", "unified_screener",
    "sector_screening_v4", "risk_forecast_daily", "liquidity_risk_monitor",
    "merged_analysis", "top200_concept", "concept_tracker_top20",
    "bond_four_factor", "cycle_monitor", "oil_monitor",
    "style_drift_monitor", "vol_monitor_daily", "credit_spread_weekly",
    "margin_season_weekly", "hurst_daily", "tugou_archive",
    "weekly_stock_monitor", "tech_consensus_daily",
]

#: 改造前的历史快照，原样留档，不参与本轮收口。
EXCLUDE_DIRS = ("datahub_refactor", "__pycache__")

LEGACY_ROOTS = ("/Coze/Drive", "/opt/coze/.fuse_drive", "/app/data")


def _bare_literals(path):
    """返回 [(行号, 字面量)]：不在 _cp(...) 里、也不是 docstring 的旧根字符串。"""
    src = open(path, encoding="utf-8", errors="replace").read()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
    wrapped = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "_cp":
            for arg in node.args:
                # 既认 _cp("...")，也认 _cp(f"...{VAR}...")：f-string 是 JoinedStr，
                # 里面的每段 Constant 都算「已经包好了」。
                if isinstance(arg, ast.Constant):
                    wrapped.add(id(arg))
                elif isinstance(arg, ast.JoinedStr):
                    for part in arg.values:
                        if isinstance(part, ast.Constant):
                            wrapped.add(id(part))
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in docstrings and id(node) not in wrapped:
            if any(root in node.value for root in LEGACY_ROOTS):
                hits.append((node.lineno, node.value[:80]))
    return hits


def _iter_files(key):
    base = os.path.join(HERE, "case", key)
    for root, dnames, files in os.walk(base):
        dnames[:] = [d for d in dnames if d not in EXCLUDE_DIRS]
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(root, name)


@pytest.mark.parametrize("key", SWITCH_KEYS)
def test_no_bare_cloud_paths(key):
    assert os.path.isdir(os.path.join(HERE, "case", key)), "切换清单里的 key 必须存在"
    bad = []
    for path in _iter_files(key):
        for lineno, value in _bare_literals(path):
            bad.append("%s:%d %s" % (os.path.relpath(path, HERE), lineno, value))
    assert not bad, "旧云盘根没有包进 _cp(...)：\n" + "\n".join(bad)


@pytest.mark.parametrize("key", SWITCH_KEYS)
def test_shell_entries_do_not_cd_into_cloud(key):
    """入口是 .sh 的任务（hurst / tugou），不能 cd 到云盘旧目录跑云端旧码。"""
    base = os.path.join(HERE, "case", key)
    for root, dnames, files in os.walk(base):
        dnames[:] = [d for d in dnames if d not in EXCLUDE_DIRS]
        for name in files:
            if not name.endswith(".sh"):
                continue
            path = os.path.join(root, name)
            for lineno, line in enumerate(open(path, encoding="utf-8", errors="replace"), 1):
                code = line.split("#", 1)[0]
                if code.strip().startswith("cd ") and any(r in code for r in LEGACY_ROOTS):
                    pytest.fail("%s:%d 仍 cd 到云盘：%s" % (os.path.relpath(path, HERE), lineno, line.strip()))


def test_translate_is_noop_without_env(monkeypatch):
    for name in ("CASE_DRIVE_ROOT", "CASE_FUSE_ROOT", "CASE_APP_DATA",
                 "SD_CLOUD_ROOT", "SD_FUSE_ROOT", "SD_APP_DATA"):
        monkeypatch.delenv(name, raising=False)
    for path in ("/Coze/Drive/a/b", "/opt/coze/.fuse_drive/x", "/app/data/y", "/srv/other"):
        assert cloud_paths.translate(path) == path


def test_translate_rebases_only_the_root(monkeypatch):
    monkeypatch.setenv("CASE_DRIVE_ROOT", "/srv/drive")
    monkeypatch.setenv("CASE_FUSE_ROOT", "/srv/fuse")
    monkeypatch.setenv("CASE_APP_DATA", "/srv/appdata")
    assert cloud_paths.translate("/Coze/Drive/a/b") == "/srv/drive/a/b"
    assert cloud_paths.translate("/opt/coze/.fuse_drive/x") == "/srv/fuse/x"
    assert cloud_paths.translate("/app/data/y") == "/srv/appdata/y"
    assert cloud_paths.translate("/Coze/Drive") == "/srv/drive"
    # 不是旧根的路径不许动 —— 否则会把本机目录也「翻译」坏
    assert cloud_paths.translate("/srv/drive/a/b") == "/srv/drive/a/b"
    assert cloud_paths.translate("/Coze/Driven/x") == "/Coze/Driven/x"


def test_switch_keys_match_switch_script():
    """清单两处各写一份，容易漂；这里直接对账。"""
    script = os.path.join(HERE, "scripts", "switch_crontab.sh")
    if not os.path.isfile(script):
        pytest.skip("switch_crontab.sh 不在")
    src = open(script, encoding="utf-8").read()
    start = src.find("KEYS=(")
    if start < 0:
        pytest.skip("switch_crontab.sh 里找不到 KEYS=(")
    end = src.find(")", start)
    block = src[start:end]
    keys = [w.strip().strip('"').strip("'") for w in block.split("(", 1)[1].split()]
    keys = [k for k in keys if k and not k.startswith("#")]
    assert keys == SWITCH_KEYS, "switch_crontab.sh 的 KEYS 与本用例不一致：%r" % (keys,)
