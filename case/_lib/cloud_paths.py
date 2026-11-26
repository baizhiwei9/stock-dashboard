#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cloud_paths.py —— 把「云盘绝对路径」变成「可搬迁的根」

问题
────
case/*/ 下的脚本是从 Coze 云主机整体搬过来的，里面写死了三类绝对根：

    /Coze/Drive/...              FUSE 云盘主视图
    /opt/coze/.fuse_drive/...    FUSE 云盘别名视图
    /app/data/...                旧沙箱数据目录

换一台机器，这三条路径全部不存在。而这些路径大多被 try/except 包着，表现不是
报错，而是「某块数据一直空着」—— 比直接崩更难发现。

做法
────
只做一件事：**把旧根换成新根，其余原样拼回去**。三个环境变量各管一个根：

    CASE_DRIVE_ROOT    默认 /Coze/Drive
    CASE_FUSE_ROOT     默认 /opt/coze/.fuse_drive
    CASE_APP_DATA      默认 /app/data

没设环境变量时 translate() 原样返回入参 —— 在云主机上行为零变化，不需要改配置。
换机器时把三个变量指到本机目录，所有 case 脚本一起跟着走。

    export CASE_DRIVE_ROOT=/srv/legacy/drive
    export CASE_FUSE_ROOT=/srv/legacy/fuse
    export CASE_APP_DATA=/srv/legacy/appdata

    python3 case/_lib/cloud_paths.py            # 打印映射表 + 目标是否存在
    python3 case/_lib/cloud_paths.py --check    # 有任何一个根缺失就返回 1

为什么不做「智能探测」
──────────────────────
第四个别名：CASE_WORKSPACE
─────────────────────────
main 上原有 55 处（35 个文件）写成
``os.environ.get('CASE_WORKSPACE') or '/Coze/Drive/蓁宝/所有对话/主对话'``
（DEPLOY_PORTABLE.md 教用户设这个变量）。本轮已全部换成 ``_cp()``，
translate() 额外认这一层是为了让它们行为等价：

    /Coze/Drive/蓁宝/所有对话/主对话[/...]        → $CASE_WORKSPACE[/...]
    /opt/coze/.fuse_drive/蓁宝/所有对话/主对话[/...] → $CASE_WORKSPACE[/...]

这是**最长前缀优先**，所以 CASE_WORKSPACE 设了就以它为准，没设才轮到
CASE_DRIVE_ROOT / CASE_FUSE_ROOT。两套变量不会互相打架：
内联写法换成 _cp() 之后行为完全等价，同时新代码也能一并受益。

探测式解析（哪个存在用哪个）会让同一份配置在不同机器上解析出不同结果，
排查时无法复现。这里只认环境变量，路径算出来是确定的。
"""
from __future__ import annotations

import os
import sys
from typing import List, Tuple

# (名字, 旧根, 环境变量候选)
ROOTS: Tuple[Tuple[str, str, Tuple[str, ...]], ...] = (
    ("drive", "/Coze/Drive", ("CASE_DRIVE_ROOT", "SD_CLOUD_ROOT")),
    ("fuse", "/opt/coze/.fuse_drive", ("CASE_FUSE_ROOT", "SD_FUSE_ROOT")),
    ("appdata", "/app/data", ("CASE_APP_DATA", "SD_APP_DATA")),
)

#: 老「蓁宝/所有对话/主对话」在云盘两个视图下的相对位置。
#: CASE_WORKSPACE 指向的就是这个目录，见模块 docstring「第四个别名」。
WORKSPACE_REL = "蓁宝/所有对话/主对话"
WORKSPACE_ENVS = ("CASE_WORKSPACE",)


def _env(names: Tuple[str, ...]) -> str:
    for n in names:
        v = (os.environ.get(n) or "").strip()
        if v:
            return v.rstrip("/") or "/"
    return ""


def roots() -> List[Tuple[str, str, str]]:
    """[(名字, 旧根, 生效根), ...]；没配环境变量时生效根 == 旧根。"""
    out = []
    for name, legacy, envs in ROOTS:
        out.append((name, legacy, _env(envs) or legacy))
    return out


def workspace_prefixes() -> List[Tuple[str, str]]:
    """[(云盘下的旧 workspace 前缀, 生效 workspace), ...]，只返回真正要换的。"""
    ws = _env(WORKSPACE_ENVS)
    if not ws:
        return []
    out = []
    for _name, legacy, _cur in roots():
        out.append((legacy + "/" + WORKSPACE_REL, ws))
    return out


def translate(path: str) -> str:
    """把旧根前缀换成生效根。不在三类旧根下的路径原样返回。

    纯字符串运算，不 stat、不判断存在性 —— 调用方拿到的路径是确定的。
    最长前缀优先：CASE_WORKSPACE 命中 workspace 前缀时优先于 CASE_DRIVE_ROOT。
    """
    if not path or not isinstance(path, str):
        return path
    for legacy_ws, cur_ws in workspace_prefixes():
        if path == legacy_ws:
            return cur_ws
        if path.startswith(legacy_ws + "/"):
            return cur_ws + path[len(legacy_ws):]
    for _name, legacy, current in roots():
        if current == legacy:
            continue
        if path == legacy:
            return current
        if path.startswith(legacy + "/"):
            return current + path[len(legacy):]
    return path


def rebase(legacy: str, *rel: str) -> str:
    """在某个旧根下拼子路径，并顺手 translate。"""
    return translate(os.path.join(legacy, *rel))


def missing_roots() -> List[Tuple[str, str]]:
    """配了环境变量、但目标目录不存在的根。没配的不算缺失。"""
    bad = []
    ws = _env(WORKSPACE_ENVS)
    if ws and not os.path.isdir(ws):
        bad.append(("workspace", ws))
    for name, legacy, current in roots():
        if current != legacy and not os.path.isdir(current):
            bad.append((name, current))
    return bad


def describe() -> str:
    lines = []
    ws = _env(WORKSPACE_ENVS)
    if ws:
        mark = "存在" if os.path.isdir(ws) else "缺失"
        lines.append("%-8s %-28s → %-32s %s" % ("workspace", WORKSPACE_REL, ws, mark))
    for name, legacy, current in roots():
        if current == legacy:
            lines.append("%-8s %-28s 未改（云主机默认）" % (name, legacy))
        else:
            mark = "存在" if os.path.isdir(current) else "缺失"
            lines.append("%-8s %-28s → %-32s %s" % (name, legacy, current, mark))
    return "\n".join(lines)


def main(argv: List[str]) -> int:
    print(describe())
    if "--check" in argv:
        bad = missing_roots()
        if bad:
            for name, path in bad:
                print("❌ %s 指向的目录不存在: %s" % (name, path), file=sys.stderr)
            return 1
        print("✅ 已配置的根都存在")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
