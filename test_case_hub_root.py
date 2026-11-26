#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""case 层 DataHub 根解析的回归用例（2026-11-26）。

背景
────
PR #102 把 21 条日更/周更任务迁进 ``case/`` 后，这些脚本仍各自写死
``/opt/coze/.fuse_drive/dataHub_三级体系`` 或 ``<主对话>/codeact/scripts``。
本机部署下先命中云盘那份旧副本 —— 不报错，只是数字不对（静默连错库）。

修法：统一走 ``case/_lib/hub_root.py``，口径与仓库根 ``datahub_path.py`` 一致
（环境变量 → 仓库根解析 → 同级 checkout → 云盘兜底）。

本用例守四件事：
  1. 每个 case 脚本的候选第一项必须是本地 checkout，不是云盘；
  2. 不许按「上层目录里有 datahub-multi-tier」猜（残留副本会连错库）；
  3. 云盘路径已废弃：框架层不再兜底（case 脚本的旧根走 `_cp()` 重定向）；
  4. 每个脚本里的 prologue 能真跑起来，且不再有裸的云盘字面量。
"""
import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "case", "_lib"))

import hub_root  # noqa: E402

CLOUD = "/opt/coze/.fuse_drive/dataHub_三级体系"

#: 已收口的脚本。新增同类脚本时加到这里，用例自动覆盖。
TARGETS = [
    "case/circulation_daily/scripts/circulation_daily.py",
    "case/cycle_monitor/scripts/cycle_monitor.py",
    "case/hurst_daily/hurst_daily.py",
    "case/oil_monitor/scripts/oil_monitor.py",
    "case/top200_concept/top200_concept_report.py",
    "case/unified_screener/src/unified_screener.py",
    "case/unified_screener/src/screening_akshare_v8.py",
    "case/unified_screener/src/merge_report.py",
    "case/sector_screening_v4/concept_map.py",
    "case/midline_preference/midline_preference_monitor.py",
    "case/vol_monitor_daily/vol_monitor_daily.py",
]
for _d in ("risk_forecast_daily", "style_drift_monitor", "liquidity_risk_monitor"):
    for _f in ("risk_forecast_daily.py", "style_drift_monitor.py", "liquidity_risk_monitor.py"):
        TARGETS.append("case/%s/scripts/%s" % (_d, _f))


def test_local_checkout_wins(tmp_path, monkeypatch):
    """仓库旁有 datahub-multi-tier 时，候选第一项必须是它。"""
    monkeypatch.delenv("DATA_HUB_PATH", raising=False)
    monkeypatch.delenv("DATAHUB_DIR", raising=False)
    monkeypatch.delenv("EDB_REPO", raising=False)
    ws = tmp_path / "ws"
    repo = ws / "stock-dashboard"
    (ws / "datahub-multi-tier" / "data_hub").mkdir(parents=True)
    task = repo / "case" / "some_task"
    task.mkdir(parents=True)
    (repo / "datahub_path.py").write_text(
        "import os\n"
        "def datahub_root():\n"
        "    here = os.path.dirname(os.path.abspath(__file__))\n"
        "    return os.path.join(os.path.dirname(here), 'datahub-multi-tier')\n",
        encoding="utf-8",
    )
    cands = hub_root.hub_candidates(str(task))
    assert cands[0] == str(ws / "datahub-multi-tier"), cands
    assert hub_root.hub_root(str(task)) == str(ws / "datahub-multi-tier")


def test_no_guessing_by_stray_directory(tmp_path, monkeypatch):
    """没有 datahub_path.py 时，不能因为上层有同名目录就认它。"""
    for n in ("DATA_HUB_PATH", "DATAHUB_DIR", "EDB_REPO"):
        monkeypatch.delenv(n, raising=False)
    stray = tmp_path / "stray"
    (stray / "datahub-multi-tier" / "data_hub").mkdir(parents=True)
    task = stray / "stock-dashboard" / "case" / "some_task"
    task.mkdir(parents=True)
    cands = hub_root.hub_candidates(str(task))
    assert str(stray / "datahub-multi-tier") not in cands, cands


def test_env_override_first(tmp_path, monkeypatch):
    """显式环境变量优先级最高（换机器只改环境变量）。"""
    hub = tmp_path / "explicit_hub"
    (hub / "data_hub").mkdir(parents=True)
    monkeypatch.setenv("DATA_HUB_PATH", str(hub))
    assert hub_root.hub_root(str(tmp_path / "nowhere")) == str(hub)


def test_no_cloud_fallback_left(tmp_path):
    """云盘旧路径已废弃：hub_root 的候选里不许再出现（框架层不再兜底）。"""
    cands = hub_root.hub_candidates(str(tmp_path / "nowhere"))
    bad = [c for c in cands if CLOUD in c or "/Coze/Drive" in c]
    assert not bad, "云盘路径不该再作为候选：%r" % (bad,)


@pytest.mark.parametrize("rel", TARGETS)
def test_script_has_no_bare_cloud_literal(rel):
    """脚本里云盘路径只允许出现在 fallback 常量里（否则又会抢在本地之前）。"""
    src = open(os.path.join(HERE, rel), encoding="utf-8").read()
    assert "_HUB_ROOT" in src, "%s 没有接上 hub_root" % rel
    lines = [l for l in src.splitlines() if CLOUD in l]
    # 只允许 prologue 里那一处兜底 return，别的都不能有
    bad = [l for l in lines
           if not l.strip().startswith("return ")]
    assert not bad, "%s 仍有裸云盘路径：%r" % (rel, bad)


@pytest.mark.parametrize("rel", TARGETS)
def test_script_prologue_runs_and_resolves(rel):
    """把脚本里的 prologue 真跑一遍：不能崩，且解析结果不是云盘兜底。"""
    path = os.path.join(HERE, rel)
    src = open(path, encoding="utf-8").read()
    m = re.search(r"# DataHub 根（唯一口径.*?_HUB_ROOT = _hub_root_path\(\)\n", src, re.S)
    assert m, "%s 缺少 hub_root prologue" % rel
    ns = {"__file__": path, "os": os, "sys": sys}
    exec(compile("import os\nimport sys\n" + m.group(0), rel, "exec"), ns)
    root = ns["_HUB_ROOT"]
    assert root and os.path.isdir(os.path.join(root, "data_hub")), (
        "%s 解析到不可用的 DataHub 根：%r" % (rel, root))
    assert root != CLOUD or not os.path.isdir(os.path.join(HERE, "..", "datahub-multi-tier")), (
        "%s 有本地 checkout 却仍落到云盘：%r" % (rel, root))


@pytest.mark.parametrize("rel", TARGETS)
def test_script_calls_shared_helper(rel):
    """不许各写一套：prologue 必须调 hub_root.hub_root()。"""
    src = open(os.path.join(HERE, rel), encoding="utf-8").read()
    assert "from hub_root import hub_root" in src, rel
    assert "_HUB_ROOT = _hub_root_path()" in src, rel
