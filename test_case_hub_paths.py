#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""case 层 DataHub 根解析的回归用例。

背景（2026-11-26）：``case/`` 下十几个脚本各自写死了
``/opt/coze/.fuse_drive/dataHub_三级体系`` 作为**首选**候选。迁移到本机盘
（``/root/work/datahub-multi-tier``）后，这些脚本仍然先命中云盘那份 —— 云盘上
的 ``data_hub`` 是旧副本，读出来的库和调度器用的不是同一个，属于「静默连错库」，
不报错、只是数字不对。

修法统一成：**先问仓库根的 ``datahub_path.py``**（build.py / scheduler.py 用的
同一份代码），它不在才退回「同级 checkout」，最后才退老云盘路径。

本用例守两件事：
  1. 有本地 checkout 时，必须命中本地，不能被云盘默认值抢走；
  2. **不能**靠「上层目录里存在 datahub-multi-tier 就认」来猜 —— 机器上残留的
     同名旧副本（``/tmp/datahub-multi-tier`` 这种）会让它连错库。
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))

#: 所有带 _hub_candidates() 的脚本。新增同类脚本时加到这里，用例自动覆盖。
TARGETS = [
    "case/_shared/limit_up_predictor/daily_runner.py",
    "case/_shared/limit_up_predictor/data_fetcher_tencent.py",
    "case/five_strategy/daily_report.py",
    "case/factor_daily/daily_factor_report.py",
    "case/factor_daily/chanlun_backtest/run_index_factor.py",
]


def _load_helper(rel_path):
    """把文件里的 _hub_candidates() 单独 exec 出来（不执行整个脚本）。

    整脚本会 import xgboost / akshare / pytdx 等重依赖，测试里不能碰。
    """
    src = open(os.path.join(HERE, rel_path), encoding="utf-8").read()
    start = src.index("def _hub_candidates(")
    end = src.index("\n_HUB_", start)
    ns = {"os": os}
    exec(compile(src[start:end], rel_path, "exec"), ns)
    return ns["_hub_candidates"]


@pytest.mark.parametrize("rel", TARGETS)
def test_local_checkout_wins_over_cloud_default(rel, tmp_path):
    """仓库旁有 datahub-multi-tier 时，候选第一项必须是它，而不是云盘路径。"""
    ws = tmp_path / "ws"
    repo = ws / "stock-dashboard"
    task = repo / "case" / "some_task"
    (ws / "datahub-multi-tier" / "data_hub").mkdir(parents=True)
    task.mkdir(parents=True)
    # datahub_path.py 认「同级 checkout」：<repo>/../datahub-multi-tier
    (repo / "datahub_path.py").write_text(
        "import os\n"
        "def datahub_root():\n"
        "    here = os.path.dirname(os.path.abspath(__file__))\n"
        "    return os.path.join(os.path.dirname(here), 'datahub-multi-tier')\n",
        encoding="utf-8",
    )
    cands = _load_helper(rel)(str(task))
    assert cands, "候选列表不能为空"
    assert cands[0] == str(ws / "datahub-multi-tier"), (
        "本地 checkout 必须是第一候选，实际：%r" % (cands,))
    assert not cands[0].startswith("/opt/coze"), "云盘路径不能排在本地之前"


@pytest.mark.parametrize("rel", TARGETS)
def test_no_guessing_by_stray_directory(rel, tmp_path):
    """没有 datahub_path.py 时，不能因为上层目录有同名目录就认它。"""
    stray = tmp_path / "stray_parent"
    (stray / "datahub-multi-tier" / "data_hub").mkdir(parents=True)
    task = stray / "stock-dashboard" / "case" / "some_task"
    task.mkdir(parents=True)
    cands = _load_helper(rel)(str(task))
    assert str(stray / "datahub-multi-tier") not in cands, (
        "不能按目录存在性猜 DataHub 根（残留副本会连错库）：%r" % (cands,))


@pytest.mark.parametrize("rel", TARGETS)
def test_env_override_first(rel, tmp_path, monkeypatch):
    """显式环境变量优先级最高（换机器只改环境变量）。"""
    hub = tmp_path / "explicit_hub"
    (hub / "data_hub").mkdir(parents=True)
    monkeypatch.setenv("DATA_HUB_PATH", str(hub))
    cands = _load_helper(rel)(str(tmp_path / "nowhere"))
    assert cands[0] == str(hub)


@pytest.mark.parametrize("rel", TARGETS)
def test_cloud_path_kept_as_last_resort(rel, tmp_path):
    """老云盘路径要留作最后兜底 —— 直接删掉会让原机部署彻底崩。"""
    cands = _load_helper(rel)(str(tmp_path / "nowhere"))
    assert cands[-1] == "/opt/coze/.fuse_drive/dataHub_三级体系"
