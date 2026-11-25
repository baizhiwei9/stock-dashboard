#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_imports.py — 静态检查 case 任务能不能 import 通

只搬 .py 之后最容易踩的坑：动态 import 的子包（signals/、pylib/、scripts/）没带进来，
任务一跑就 ModuleNotFoundError。这个工具把每个任务目录下所有 .py 的 import 抽出来，
按「任务目录 → 仓库根 → pylibs → 标准库」的顺序解析，列出解析不到的模块。

用法：
    python3 case/_tools/check_imports.py                # 全部
    python3 case/_tools/check_imports.py --only tda_spd
    python3 case/_tools/check_imports.py --json

环境变量 PYLIBS_DIR 指向第三方包目录；不设则只看任务目录与标准库。
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CASE_DIR = os.path.dirname(HERE)
ROOT = os.path.dirname(CASE_DIR)
STDLIB = set(getattr(sys, "stdlib_module_names", ())) | {"__future__"}
# 本项目内跨目录引用的模块（不在任务目录里，但仓库里有）
REPO_PATHS = [ROOT, os.path.join(ROOT, "case"), os.path.join(ROOT, "case", "_lib")]


def _search_dirs(task_dir: str) -> list:
    dirs = [task_dir] + REPO_PATHS
    # PYLIBS_DIR 支持多目录（冒号分隔），与 runner.py 的解析口径一致
    for pl in (os.environ.get("PYLIBS_DIR") or "").split(":"):
        pl = pl.strip()
        if pl and os.path.isdir(pl) and pl not in dirs:
            dirs.append(pl)
    return dirs


def _resolvable(mod: str, dirs) -> bool:
    name = mod.split(".")[0]
    if not name or name in STDLIB:
        return True
    for d in dirs:
        if os.path.isfile(os.path.join(d, name + ".py")):
            return True
        if os.path.isdir(os.path.join(d, name)):
            return True
    # 已安装到当前解释器的第三方包
    try:
        if name in sys.builtin_module_names:
            return True
        import importlib.util
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        return False


def _imports_of(path: str):
    try:
        tree = ast.parse(open(path, encoding="utf-8", errors="replace").read(), filename=path)
    except SyntaxError:
        return []
    mods = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:            # 相对 import，同包内，静态看不出来
                continue
            if node.module:
                mods.append(node.module)
    return mods


def _module_file(mod: str, *roots):
    rel = os.path.join(*mod.split("."))
    for r in roots:
        for cand in (rel + ".py", os.path.join(rel, "__init__.py")):
            p = os.path.join(r, cand)
            if os.path.isfile(p):
                return p
    return None


def _closure(tdir: str, entry: str):
    """从入口出发的 import 闭包：只跟任务目录内能解析到的模块。

    历史脚本（bt_*.py / diag_*.py）里的 import 不算——它们不影响定时任务的入口运行。
    """
    seed = os.path.join(tdir, entry) if entry else ""
    if not os.path.isfile(seed):
        seed = os.path.join(tdir, "pipeline.py")
    if not os.path.isfile(seed):
        return []
    files, seen = [seed], {os.path.abspath(seed)}
    i = 0
    while i < len(files):
        p = files[i]
        i += 1
        roots = [tdir, os.path.dirname(p)]
        for m in _imports_of(p):
            q = _module_file(m, *roots)
            if q and os.path.abspath(q) not in seen:
                seen.add(os.path.abspath(q))
                files.append(q)
    return files


def check_one(key: str, closure: bool = True) -> dict:
    tdir = os.path.join(CASE_DIR, key)
    task = {}
    try:
        task = json.load(open(os.path.join(tdir, "task.json"), encoding="utf-8"))
    except (OSError, ValueError):
        pass
    entry = str(task.get("entry") or "")
    if closure:
        rels = [os.path.relpath(p, tdir) for p in _closure(tdir, entry)]
    else:
        rels = []
        for root, dnames, files in os.walk(tdir):
            dnames[:] = [d for d in dnames if d not in ("__pycache__", "out", "data", "logs")]
            rels += [os.path.relpath(os.path.join(root, f), tdir)
                     for f in files if f.endswith(".py")]

    bad: dict = {}
    for rel in rels:
        p = os.path.join(tdir, rel)
        dirs = _search_dirs(tdir) + [os.path.dirname(p)]
        for m in _imports_of(p):
            if not _resolvable(m, dirs):
                bad.setdefault(m, []).append(rel)
    return {"key": key, "files": len(rels), "entry": entry,
            "missing": {k: sorted(set(v))[:3] for k, v in sorted(bad.items())}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    keys = [a.only] if a.only else sorted(
        n for n in os.listdir(CASE_DIR)
        if not n.startswith(("_", ".")) and
        os.path.isfile(os.path.join(CASE_DIR, n, "task.json")))
    rows = [check_one(k) for k in keys]

    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    bad_n = 0
    for r in rows:
        if not r["missing"]:
            print("%-24s OK（%d 个 .py）" % (r["key"], r["files"]))
            continue
        bad_n += 1
        print("%-24s 未解析 %d 个模块（%d 个 .py）" % (r["key"], len(r["missing"]), r["files"]))
        for m, where in r["missing"].items():
            print("    %-24s ← %s" % (m, ", ".join(where)))
    print("\n共 %d 个任务，%d 个 import 不全" % (len(rows), bad_n))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
