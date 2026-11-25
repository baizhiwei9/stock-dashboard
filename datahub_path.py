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
3. 兜底 /opt/coze/.fuse_drive/dataHub_三级体系 —— 可能不存在，
   由调用方在 import / cd 失败时按自己的方式报错。

只解析路径，不改 sys.path、不改环境变量。环境变量请给绝对路径。

    python3 datahub_path.py      # 打印解析结果，给 shell 脚本用
"""
import os

DEFAULT_ROOT = "/opt/coze/.fuse_drive/dataHub_三级体系"
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
    out.append(("default", DEFAULT_ROOT))
    return out


def datahub_root(project=None):
    """返回实际可用的 DataHub 根；都没命中时返回兜底常量（不保证存在）。"""
    for _source, path in candidates(project):
        if _is_hub_root(path):
            return path
    return DEFAULT_ROOT


def expand_root(value, root=None):
    """把 modules.json 的 cwd/cmd 里的 {{DATAHUB_ROOT}} 换成解析结果。"""
    if not isinstance(value, str) or "{{DATAHUB_ROOT}}" not in value:
        return value
    return value.replace("{{DATAHUB_ROOT}}", root if root is not None else datahub_root())


def expand_module(module, root=None):
    """返回副本：把模块 cwd/cmd 里的 {{DATAHUB_ROOT}} 展开，其余字段原样保留。"""
    root = root if root is not None else datahub_root()
    out = dict(module)
    for field in ("cwd", "cmd"):
        value = out.get(field)
        if isinstance(value, str):
            out[field] = expand_root(value, root)
    return out


if __name__ == "__main__":
    print(datahub_root())
