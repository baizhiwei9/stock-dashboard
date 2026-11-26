#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DataHub 仓库根路径解析（唯一口径）。

build.py / snapshot.py / scheduler.py / manager.py 都从这里取 DataHub 根；
run_daily.sh / sd_watchdog.sh 直接 `python3 datahub_path.py` 取同一结果。
换机器时只改环境变量或 checkout 位置，所有入口一起跟着变。

优先级
──────
1. 显式环境变量 DATA_HUB_PATH → DATAHUB_DIR → EDB_REPO，
   取第一个「目录存在且含 data_hub/」的。只存在、但没有 data_hub/ 的路径
   会跳过 —— 选了它后面 import 也必然失败，跳过才有机会落到可用路径。
2. 同级 checkout：<本仓库>/../datahub-multi-tier；
   再兼容 <本仓库>/datahub-multi-tier 的旧布局。
3. 都没有时返回空串 —— 调用方必须显式报错。代码里不再保留云盘旧副本
   （/opt/coze/.fuse_drive/...）兜底：那条路径已废弃，退过去只会静默连错库。

只解析路径，不改 sys.path、不改环境变量。环境变量请给绝对路径。

    python3 datahub_path.py      # 打印解析结果，给 shell 脚本用
"""
import os
import sys

ENV_NAMES = ("DATA_HUB_PATH", "DATAHUB_DIR", "EDB_REPO")
SIBLING_DIRS = ("../datahub-multi-tier", "datahub-multi-tier")


def _project_dir():
    return os.path.dirname(os.path.abspath(__file__))


def _is_hub_root(path):
    return bool(path) and os.path.isdir(os.path.join(path, "data_hub"))


def candidates(project=None):
    """按优先级返回 [(来源, 路径), ...]，排障时可打印出来看为什么选了某条。"""
    project = project or _project_dir()
    out = []
    for name in ENV_NAMES:
        val = os.environ.get(name)
        if val:
            out.append(("env:" + name, val))
    for rel in SIBLING_DIRS:
        out.append(("sibling", os.path.normpath(os.path.join(project, rel))))
    return out


def datahub_root(project=None):
    """返回实际可用的 DataHub 根；都没命中时返回空串（由调用方显式报错）。"""
    for _source, path in candidates(project):
        if _is_hub_root(path):
            return path
    return ""


def repo_root(project=None):
    """本仓库（stock-dashboard）根：{{REPO_ROOT}} 的展开值。"""
    return _project_dir() if project is None else os.path.abspath(project)


def expand_root(value, root=None, repo=None):
    """把 modules.json 的 cwd/cmd 里的占位换成解析结果。

    支持两个占位：
      {{DATAHUB_ROOT}}  → datahub_root()（DataHub 仓库根）
      {{REPO_ROOT}}     → 本仓库根（cleanup / tech_snapshot 这类框架自带模块用）
    """
    if not isinstance(value, str):
        return value
    if "{{DATAHUB_ROOT}}" in value:
        value = value.replace("{{DATAHUB_ROOT}}",
                              root if root is not None else datahub_root())
    if "{{REPO_ROOT}}" in value:
        value = value.replace("{{REPO_ROOT}}",
                              repo if repo is not None else repo_root())
    return value


def expand_module(module, root=None, repo=None):
    """返回副本：把模块 cwd/cmd 里的 {{DATAHUB_ROOT}} / {{REPO_ROOT}} 展开，其余字段原样保留。"""
    root = root if root is not None else datahub_root()
    repo = repo if repo is not None else repo_root()
    out = dict(module)
    for field in ("cwd", "cmd"):
        value = out.get(field)
        if isinstance(value, str):
            out[field] = expand_root(value, root, repo)
    return out


if __name__ == "__main__":
    _root = datahub_root()
    if not _root:
        sys.stderr.write(
            "找不到 DataHub（需要含 data_hub/ 的仓库）：设 DATA_HUB_PATH，"
            "或把 datahub-multi-tier clone 到本仓库同级\n")
        raise SystemExit(1)
    print(_root)
