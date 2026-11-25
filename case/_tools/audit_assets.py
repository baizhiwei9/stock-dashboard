#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audit_assets.py — 盘点 case 任务缺了哪些运行资产

迁移只搬了 .py，但任务运行时常常还依赖两类东西：

  · 子包/子目录里的 .py（动态 import，静态闭包抓不到，例如 signals.smc）
  · 非 .py 的运行资产（中文字体、输入模板、静态配置）

这个工具拿 task.json 的 root（迁移前源目录）和 case 目录对比，列出缺口，
只报告、不改动。确认后用：

    python3 case/_tools/audit_assets.py            # 全部任务
    python3 case/_tools/audit_assets.py --only tech_consensus
    python3 case/_tools/audit_assets.py --json

补齐原则：
  · 子包 .py → 直接补进 case/<key>/（仍是程序文件）
  · 字体 → 不入库，由 case/_lib/assets.py 在 fetch 阶段准备
  · 输入模板/配置 → 视情况：config.example 入库，真实数据/凭据不入库
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CASE_DIR = os.path.dirname(HERE)
ROOT = os.path.dirname(CASE_DIR)

SKIP_DIRS = {"__pycache__", ".git", ".deps", "node_modules", "venv", ".venv"}
DATA_HINT = ("data", "out", "output", "logs", "log", "report", "reports",
             "cache", "tmp", "temp", "backtest_out", "state", "figures", "charts")
ASSET_EXT = (".ttf", ".otf", ".ttc", ".json", ".xlsx", ".xls", ".csv", ".xlsm",
             ".html", ".css", ".js", ".png", ".jpg", ".yaml", ".yml", ".toml")


def _read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _source_root(task) -> str:
    raw = str(task.get("root") or "").strip()
    if not raw:
        return ""
    # {WS} 指工作区根（projects/stock-dashboard 的上两级），不是仓库根
    ws = os.path.dirname(os.path.dirname(os.path.abspath(ROOT)))
    for base in (ws, ROOT, os.path.dirname(ROOT)):
        cand = raw.replace("{WS}", base).replace("${WS}", base)
        if os.path.isdir(cand):
            return cand
    return ""


def _pkg_dirs(src: str):
    """源目录下含 .py 的子目录（第一层，排除数据/产物目录）。"""
    out = []
    try:
        names = sorted(os.listdir(src))
    except OSError:
        return out
    for n in names:
        p = os.path.join(src, n)
        if not os.path.isdir(p) or n in SKIP_DIRS or n.startswith("."):
            continue
        if any(h in n.lower() for h in DATA_HINT):
            continue
        has_py = any(f.endswith(".py") for _r, _d, fs in os.walk(p) for f in fs)
        if has_py:
            out.append(n)
    return out


def _assets(src: str):
    out = []
    try:
        names = sorted(os.listdir(src))
    except OSError:
        return out
    for n in names:
        p = os.path.join(src, n)
        if not os.path.isfile(p) or n.startswith("."):
            continue
        if n.endswith(ASSET_EXT) and os.path.getsize(p) < 20 * 1024 * 1024:
            out.append(n)
    return out


def audit_one(key: str) -> dict:
    tdir = os.path.join(CASE_DIR, key)
    task = _read_json(os.path.join(tdir, "task.json"), {}) or {}
    task["_dir"] = tdir
    src = _source_root(task)
    miss_pkgs, miss_assets, notes = [], [], []
    if not src:
        notes.append("task.json 没有可用的 root，跳过资产对比")
    else:
        for d in _pkg_dirs(src):
            if not os.path.isdir(os.path.join(tdir, d)):
                miss_pkgs.append(d)
        for f in _assets(src):
            if not os.path.isfile(os.path.join(tdir, f)):
                miss_assets.append(f)
    return {"key": key, "root": src, "missing_packages": miss_pkgs,
            "missing_assets": miss_assets, "notes": notes}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    keys = [a.only] if a.only else sorted(
        n for n in os.listdir(CASE_DIR)
        if not n.startswith(("_", ".")) and
        os.path.isfile(os.path.join(CASE_DIR, n, "task.json")))
    rows = [audit_one(k) for k in keys]

    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    bad = 0
    for r in rows:
        flag = bool(r["missing_packages"] or r["missing_assets"])
        bad += flag
        print("%-24s %s" % (r["key"], "缺资产" if flag else "OK"))
        if r["missing_packages"]:
            print("    子包（.py）：%s" % ", ".join(r["missing_packages"]))
        if r["missing_assets"]:
            print("    运行资产：%s" % ", ".join(r["missing_assets"]))
        for n in r["notes"]:
            print("    注：%s" % n)
    print("\n共 %d 个任务，%d 个有缺口" % (len(rows), bad))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))