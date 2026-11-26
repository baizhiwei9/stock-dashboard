#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""期权日更任务薄包装：算法在 options_utility 仓，本文件只负责定位、拼环境、防双跑。

设计约束（2026-11-26 与老板对齐）：
- **不复制策略代码**。options_utility 更新后，这里 7 个包装同时生效。
- 换机器只需 clone 两个仓（stock-dashboard + options_utility）并放好 secrets，
  options 根按 `OPTIONS_UTILITY_ROOT` → 同级 checkout → 老云盘路径 依次查找。
- 老机器上 crontab 还挂着同名任务时**主动跳过**，避免「crontab + task」双跑双推。
  新机器没有 crontab，`crontab -l` 失败或为空，正常执行。
- DataHub 根复用 `case/_lib/hub_root.py` 的统一口径，避免静默连到云盘旧库。
"""
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))   # <repo>/case/_shared -> <repo>

_MARKER = os.path.join("alert", "engine.py")            # options_utility 仓特征文件


def _bootstrap_libs() -> None:
    """把 case/_lib 挂进 sys.path —— 任务子进程不会继承 runner 的 sys.path。"""
    lib = os.path.join(REPO_ROOT, "case", "_lib")
    if os.path.isdir(lib) and lib not in sys.path:
        sys.path.insert(0, lib)


def options_root() -> str:
    """定位 options_utility 仓根，找不到返回空串。"""
    cands = []
    env = (os.environ.get("OPTIONS_UTILITY_ROOT") or "").strip()
    if env:
        cands.append(env)
    cands += [
        os.path.join(os.path.dirname(REPO_ROOT), "options_utility"),
        os.path.join(os.path.dirname(REPO_ROOT), "options-utility"),
        "/opt/coze/.fuse_drive/dataHub_三级体系/options",   # 老部署兜底，迁移期保留
    ]
    for c in cands:
        c = os.path.abspath(os.path.expanduser(c))
        if os.path.isfile(os.path.join(c, _MARKER)):
            return c
    return ""


def _hub_root() -> str:
    _bootstrap_libs()
    try:
        from hub_root import hub_root          # noqa: PLC0415
        root = hub_root() or ""
    except Exception:
        root = os.environ.get("DATA_HUB_PATH", "")
    return root if os.path.isdir(os.path.join(root, "data_hub")) else ""


def _crontab_has(name: str) -> bool:
    """crontab 里是否还有同名任务（只认未注释的 --name）。"""
    if not name:
        return False
    try:
        out = subprocess.run(["crontab", "-l"], capture_output=True,
                             text=True, timeout=5).stdout
    except Exception:
        return False
    needle = '--name "{}"'.format(name)
    for line in out.splitlines():
        s = line.strip()
        if s and not s.startswith("#") and needle in s:
            return True
    return False


def run(steps, args=(), crontab_name="", title=""):
    """顺序执行 options_utility 里的入口脚本；任一步非 0 即返回该退出码。"""
    label = title or os.path.basename(sys.argv[0])
    if _crontab_has(crontab_name):
        print("[skip] crontab 仍挂着「{}」，本任务跳过以免双跑；"
              "移除该 crontab 行后由 task 调度接管。".format(crontab_name), flush=True)
        return 0

    root = options_root()
    if not root:
        print("[error] 找不到 options_utility 仓（特征文件 {}/alert/engine.py）。\n"
              "        请与 stock-dashboard 同级 clone，或设 OPTIONS_UTILITY_ROOT。"
              .format("<options_utility>"), file=sys.stderr, flush=True)
        return 2

    _bootstrap_libs()
    try:
        import app_secrets                       # noqa: PLC0415
        app_secrets.export_env()                 # 不覆盖已有环境变量
    except Exception:
        pass

    env = os.environ.copy()
    path_parts = [root]
    hub = _hub_root()
    if hub:
        path_parts.append(hub)
        env.setdefault("DATA_HUB_PATH", hub)
    else:
        print("[warn] 未找到 data_hub 仓；若入口需要 data_hub 会直接报 ImportError。",
              file=sys.stderr, flush=True)
    if env.get("PYTHONPATH"):
        path_parts.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(path_parts)

    if isinstance(steps, str):
        steps = [steps]
    for step in steps:
        cmd = [sys.executable, "-u", step] + list(args)
        print("[run] {}  (cwd={})".format(" ".join(cmd), root), flush=True)
        rc = subprocess.run(cmd, cwd=root, env=env).returncode
        if rc != 0:
            print("[fail] {} -> exit {}".format(step, rc), file=sys.stderr, flush=True)
            return rc
    return 0
