#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fix_imports.py — 把迁移时漏掉的本地 .py 模块补回 case 任务目录

只搬入口 import 闭包会漏两类文件，这个工具补第一类（程序模块）：

  · 动态 import 的模块：signals.smc、chanlib.*、report_stats、markers ……
  · 包内绝对 import 的兄弟模块：signals/daily_views.py 里的 `import dxke`

外部依赖不补，只记录：
  · data_hub / data_hub.collectors.* → 目标机 DataHub 提供，用 PYTHONPATH 指过去
  · feishu_config / feishu_pusher / ts_config → 凭据或代理配置，走环境变量

用法：
    python3 case/_tools/fix_imports.py --dry-run
    python3 case/_tools/fix_imports.py
    python3 case/_tools/fix_imports.py --only tda_spd
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CASE_DIR = os.path.dirname(HERE)
ROOT = os.path.dirname(CASE_DIR)
sys.path.insert(0, HERE)
import check_imports as CI                                          # noqa: E402

EXTERNAL_PREFIX = ("data_hub", )
CREDENTIAL = ("feishu_config", "feishu_router", "feishu_pusher", "ts_config",
              "ts_session", "ts_http", "email_config", "tk")


def _source_root(task) -> str:
    raw = str(task.get("root") or "").strip()
    if not raw:
        return ""
    ws = os.path.dirname(os.path.dirname(os.path.abspath(ROOT)))
    for base in (ws, ROOT, os.path.dirname(ROOT)):
        cand = raw.replace("{WS}", base).replace("${WS}", base)
        if os.path.isdir(cand):
            return cand
    return ""


def _find(roots, mod: str):
    """在候选目录里找模块，返回 (kind, rel, src)；kind=file|dir。"""
    rel = os.path.join(*mod.split("."))
    for src in roots:
        for cand in (rel + ".py", os.path.join(rel, "__init__.py")):
            if os.path.isfile(os.path.join(src, cand)):
                return "file", cand, src
        d = os.path.join(src, rel)
        if os.path.isdir(d) and any(f.endswith(".py") for f in os.listdir(d)):
            return "dir", rel, src
    base = mod.split(".")[-1]
    for src in roots:
        seen = 0
        for root, dirs, files in os.walk(src):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", "node_modules")]
            seen += 1
            if seen > 400:
                break
            if base + ".py" in files:
                return "file", os.path.relpath(os.path.join(root, base + ".py"), src), src
            if base in dirs:
                return "dir", os.path.relpath(os.path.join(root, base), src), src
    return None, None, None


def fix_one(key: str, apply: bool) -> dict:
    tdir = os.path.join(CASE_DIR, key)
    task = CI.json.load(open(os.path.join(tdir, "task.json"), encoding="utf-8"))
    src = _source_root(task)
    res = {"key": key, "copied": [], "external": [], "not_found": [], "src": src}
    if not src:
        res["not_found"].append("（没有可用的 root，无法补）")
        return res
    missing = CI.check_one(key)["missing"]
    for mod in missing:
        top = mod.split(".")[0]
        if top.startswith(EXTERNAL_PREFIX):
            res["external"].append(mod)
            continue
        if top in CREDENTIAL:
            res["external"].append(mod)
            continue
        # 共享模块常放在任务源目录的上级（会话根），所以往上找两层
        roots = [src, os.path.dirname(src), os.path.dirname(os.path.dirname(src))]
        roots = [r for r in roots if r and os.path.isdir(r)]
        kind, rel, found_in = _find(roots, mod)
        if not rel:
            res["not_found"].append(mod)
            continue
        if kind == "dir":
            ddir = os.path.join(found_in, rel)
            n = 0
            for root, dirs, files in os.walk(ddir):
                dirs[:] = [d for d in dirs if d != "__pycache__"]
                for f in files:
                    if not f.endswith(".py"):
                        continue
                    r2 = os.path.relpath(os.path.join(root, f), found_in)
                    dst = os.path.join(tdir, r2)
                    if os.path.isfile(dst):
                        continue
                    if apply:
                        os.makedirs(os.path.dirname(dst) or tdir, exist_ok=True)
                        shutil.copy2(os.path.join(found_in, r2), dst)
                    res["copied"].append(r2)
                    n += 1
            if not n:
                res["not_found"].append(mod + "（空包目录）")
            continue
        dst = os.path.join(tdir, rel)
        if os.path.isfile(dst):
            continue
        if apply:
            os.makedirs(os.path.dirname(dst) or tdir, exist_ok=True)
            shutil.copy2(os.path.join(found_in, rel), dst)
        res["copied"].append(rel)
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    keys = [a.only] if a.only else sorted(
        n for n in os.listdir(CASE_DIR)
        if not n.startswith(("_", ".")) and
        os.path.isfile(os.path.join(CASE_DIR, n, "task.json")))
    rows = [fix_one(k, not a.dry_run) for k in keys]

    for r in rows:
        if not (r["copied"] or r["not_found"]):
            continue
        print("%-24s %s" % (r["key"], "将补" if a.dry_run else "已补"))
        for p in r["copied"]:
            print("    + %s" % p)
        for m in r["not_found"]:
            print("    ! 源目录也没有：%s" % m)
    ext = sorted({m for r in rows for m in r["external"]})
    print("\n外部依赖（不补，运行时由 DataHub/环境变量提供）：%s" % ", ".join(ext))
    n_copy = sum(len(r["copied"]) for r in rows)
    print("合计%s %d 个文件；%d 个任务有未解决模块"
          % ("将补" if a.dry_run else "已补", n_copy,
             sum(1 for r in rows if r["not_found"])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))