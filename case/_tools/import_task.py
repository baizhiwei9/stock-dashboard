#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""import_task.py — 把 Dumate 任务按白名单搬进 case/

数据来源：case/_seed/tasks.json（脱敏清单：name/cron/section/key/cwd/py/deliver）

做三件事：
  1. 按白名单复制任务源码（.py/.sh/.md/.txt/.yaml/.toml/.ini 及小 json），
     跳过 data/out/logs/cache/图片/大文件；每个任务有文件数与体积上限，
     超限按「浅目录优先、源码优先」截断——仓库只存代码，不存数据与产物。
  2. 给每个任务生成/更新 `case/<key>/task.json`（沿用 modules.json 的 sched 规范）。
  3. 已有 task.json 的，保留其 metrics / enabled / 手工改过的字段，只同步调度与命令。

源目录定位顺序：清单里的 cwd → 入口脚本所在目录 → 在 workspace 内按入口文件名搜索。

幂等：可以反复跑。加 --dry-run 只看不写。

用法：
    python3 case/_tools/import_task.py --dry-run
    python3 case/_tools/import_task.py
    python3 case/_tools/import_task.py --only tech_consensus,ot_unified
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from typing import Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
CASE_DIR = os.path.dirname(HERE)
ROOT = os.path.dirname(CASE_DIR)
SEED = os.path.join(CASE_DIR, "_seed", "tasks.json")

WS_TOKEN = "{WS}"

SRC_EXTS = {".py", ".sh", ".md", ".txt", ".yaml", ".yml", ".toml", ".ini", ".cfg"}
EXT_PRIORITY = {".py": 0, ".sh": 1, ".md": 2, ".txt": 2, ".toml": 3, ".ini": 3,
                ".cfg": 3, ".yaml": 4, ".yml": 4, ".json": 5}
JSON_MAX = 200 * 1024
FILE_MAX = 2 * 1024 * 1024
MAX_FILES_PER_TASK = 400
MAX_BYTES_PER_TASK = 20 * 1024 * 1024
SEARCH_DEPTH = 4
SKIP_DIRS = {
    "data", "out", "output", "outputs", "logs", "log", "cache", "__pycache__", ".git",
    "node_modules", "images", "img", "charts", "chart", "fig", "figs", "figures",
    "results", "result", "backtest_out", "plots", "plot", "tmp", "temp", ".venv", "venv",
    ".pytest_cache", ".mypy_cache", "reports_out", "dist", "build", "site-packages",
    "archive", "backup", "history", ".cache", "snapshots",
}


def _read_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def cron_to_sched(cron: str) -> Dict:
    """5 段 cron → modules.json 的 sched 结构。"""
    parts = (cron or "").split()
    if len(parts) != 5:
        return {"mode": "daily", "at": "08:00", "weekdays": [1, 2, 3, 4, 5]}
    mi, hr, dom, _mon, dow = parts
    at = "%02d:%02d" % (int(hr), int(mi))
    if dom.isdigit():
        return {"mode": "monthly", "at": at, "days": [int(dom)]}
    if dow not in ("*", "?"):
        wds: List[int] = []
        for seg in dow.split(","):
            if "-" in seg:
                a, b = seg.split("-", 1)
                wds.extend(range(int(a), int(b) + 1))
            else:
                wds.append(int(seg))
        wds = sorted({7 if x in (0, 7) else x for x in wds})
        return {"mode": "weekly" if len(wds) == 1 else "daily",
                "at": at, "weekdays": wds}
    return {"mode": "daily", "at": at, "weekdays": [1, 2, 3, 4, 5]}


def _skip_dir(name: str) -> bool:
    return name.lower() in SKIP_DIRS


def _wanted(path: str) -> bool:
    ext = os.path.splitext(path)[1].lower()
    try:
        size = os.path.getsize(path)
    except OSError:
        return False
    if size > FILE_MAX:
        return False
    if ext == ".json":
        return size <= JSON_MAX
    return ext in SRC_EXTS


def collect(src_root: str, limit: int = MAX_FILES_PER_TASK) -> Tuple[List[str], bool]:
    """列出 src_root 下白名单内的相对路径，按浅层/源码优先截断。

    返回 (相对路径列表, 是否被截断)。
    """
    cands: List[Tuple[int, int, str]] = []
    for dp, dns, fns in os.walk(src_root):
        dns[:] = [d for d in dns if not _skip_dir(d)]
        rel_dir = os.path.relpath(dp, src_root)
        depth = 0 if rel_dir == "." else rel_dir.count(os.sep) + 1
        if depth > SEARCH_DEPTH + 2:
            dns[:] = []
            continue
        for fn in fns:
            if fn.startswith(".") and fn != ".env.example":
                continue
            p = os.path.join(dp, fn)
            if not _wanted(p):
                continue
            ext = os.path.splitext(fn)[1].lower()
            cands.append((depth, EXT_PRIORITY.get(ext, 9), os.path.relpath(p, src_root)))
    cands.sort()
    truncated = len(cands) > limit
    return [c[2] for c in cands[:limit]], truncated


def build_name_index(ws: str) -> Dict[str, List[str]]:
    """扫一遍 workspace（限深），建立 .py 文件名 → 路径列表的索引。"""
    idx: Dict[str, List[str]] = {}
    for dp, dns, fns in os.walk(ws):
        rel_dir = os.path.relpath(dp, ws)
        depth = 0 if rel_dir == "." else rel_dir.count(os.sep) + 1
        if depth > SEARCH_DEPTH:
            dns[:] = []
            continue
        dns[:] = [d for d in dns if not _skip_dir(d)]
        for fn in fns:
            if fn.endswith(".py"):
                idx.setdefault(fn, []).append(os.path.join(dp, fn))
    return idx


def _abs_py(raw: str, ws: str, idx: Optional[Dict[str, List[str]]]) -> Optional[str]:
    """把清单里的 py 描述解析成绝对路径。"""
    p = raw.replace(WS_TOKEN, ws)
    if os.path.isabs(p) and os.path.isfile(p):
        return p
    cand = os.path.join(ws, p)
    if os.path.isfile(cand):
        return cand
    base = os.path.basename(p)
    if idx is None or base not in idx:
        return None
    hits = idx[base]
    if len(hits) == 1:
        return hits[0]
    # 多个同名：优先与清单尾部相对路径吻合的
    tail = os.path.join(*p.split(os.sep)[-3:]) if len(p.split(os.sep)) >= 3 else p
    for h in hits:
        if h.endswith(tail):
            return h
    return hits[0]


def resolve_roots(task: Dict, ws: str, idx: Optional[Dict[str, List[str]]]) -> Tuple[List[str], List[str]]:
    """返回 (cwd 根列表, 额外依赖根列表)，都是绝对路径。

    源根排除 workspace 根本身：那是所有会话目录的父目录，卷进来会把整个工作区搬走。
    """
    cwds = [c.replace(WS_TOKEN, ws) for c in (task.get("cwd") or []) if c]
    pys = [p for p in (_abs_py(x, ws, idx) for x in (task.get("py") or [])) if p]

    def _safe(c: str) -> bool:
        return bool(c) and os.path.isdir(c) and os.path.normpath(c) != os.path.normpath(ws)

    roots = [c for c in cwds if _safe(c)]
    if not roots and pys:
        d = os.path.dirname(pys[0])
        if _safe(d):
            roots = [d]
    if not roots:
        return [], []
    extra: List[str] = []
    for p in pys:
        d = os.path.dirname(p)
        if any(d == r or d.startswith(r + os.sep) for r in roots):
            continue
        if _safe(d) and d not in extra:
            extra.append(d)
    return roots, extra


def pick_entry(task: Dict, roots: List[str], extra: List[str], ws: str,
               idx: Optional[Dict[str, List[str]]]) -> Optional[str]:
    """选入口脚本，返回 case 内的相对路径。

    同名同目录里会有一堆辅助脚本（config / check_trading_day / lib），
    先按文件名打分排序：run/daily/weekly/gen/push/report 加分，config/check/lib 减分。
    """
    good = re.compile(r"(^|[/_\-. ])(run|main|daily|weekly|monthly|gen|push|report|monitor|entry|start)", re.I)
    bad = re.compile(r"(config|check_trading|lib|util|const|settings|__init__|test|smoke)", re.I)

    def _score(p: str) -> int:
        b = os.path.basename(p)
        s = 0
        if bad.search(b):
            s -= 10
        if good.search(b):
            s += 5
        if b.startswith("run_") or b in ("main.py", "run.py"):
            s += 3
        return s

    pys = [p for p in (_abs_py(x, ws, idx) for x in (task.get("py") or [])) if p]
    pys = sorted([p for p in pys if p.endswith((".py", ".sh"))], key=lambda p: -_score(p))
    for p in pys:
        for c in roots:
            if p.startswith(c + os.sep):
                return os.path.relpath(p, c)
        for e in extra:
            if p.startswith(e + os.sep):
                return os.path.join("_deps", os.path.basename(e), os.path.relpath(p, e))
    for c in roots:
        try:
            names = sorted(os.listdir(c))
        except OSError:
            continue
        for fn in names:
            if fn.endswith((".py", ".sh")) and re.match(r"(run|daily|main|weekly)", fn):
                return fn
    return None


def import_one(task: Dict, ws: str, dry: bool, idx: Optional[Dict[str, List[str]]]) -> Dict:
    key = task["key"]
    dest = os.path.join(CASE_DIR, key)
    roots, extra = resolve_roots(task, ws, idx)
    if not roots and not extra:
        return {"key": key, "status": "no-source", "root": None, "copied": 0, "entry": None}

    entry = pick_entry(task, roots, extra, ws, idx)
    copied = 0
    bytes_used = 0
    truncated: List[str] = []
    for c in roots:
        rels, cut = collect(c)
        if cut:
            truncated.append(os.path.basename(c))
        for rel in rels:
            src = os.path.join(c, rel)
            try:
                bytes_used += os.path.getsize(src)
            except OSError:
                continue
            if bytes_used > MAX_BYTES_PER_TASK:
                truncated.append("%s(体积)" % os.path.basename(c))
                break
            if not dry:
                dst = os.path.join(dest, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
            copied += 1
    for e in extra:
        alias = os.path.basename(e.rstrip(os.sep))
        rels, cut = collect(e, limit=80)
        if cut:
            truncated.append("%s(_deps)" % alias)
        for rel in rels:
            src = os.path.join(e, rel)
            try:
                bytes_used += os.path.getsize(src)
            except OSError:
                continue
            if bytes_used > MAX_BYTES_PER_TASK:
                break
            if not dry:
                dst = os.path.join(dest, "_deps", alias, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
            copied += 1

    tj_path = os.path.join(dest, "task.json")
    old = _read_json(tj_path, {}) or {}
    if entry and entry.endswith(".sh"):
        cmd = "bash %s" % entry
    else:
        cmd = ("python3 -u %s" % entry) if entry else "python3 -u main.py"
    cwd = "."
    if old.get("cmd_manual"):
        # 手工改过的入口（多脚本串联、-m 模块、带环境变量）重跑导入时不能被覆盖
        cmd = old.get("cmd") or cmd
        cwd = old.get("cwd") or cwd
    tj = {
        "key": key,
        "title": task.get("name") or key,
        "desc": old.get("desc") or "",
        "section": task.get("section") or old.get("section") or "macro",
        "metrics": old.get("metrics") or [],
        "sched": cron_to_sched(task.get("cron") or ""),
        "cmd": cmd,
        "cwd": cwd,
        "timeout": int(old.get("timeout") or 1800),
        "enabled": bool(old.get("enabled", True)),
        "deliver": old.get("deliver") or task.get("deliver") or {"report": True, "email": False},
        "original_cwd": (task.get("cwd") or [""])[0],
        "original_entry": (task.get("py") or [""])[0],
        "imported_from": "case/_seed/tasks.json",
    }
    if old.get("cmd_manual"):
        tj["cmd_manual"] = True
    if old.get("env"):
        tj["env"] = old["env"]
    if truncated:
        tj["import_note"] = "源码按上限截断：" + "、".join(sorted(set(truncated)))
    if not dry:
        _write_json(tj_path, tj)
    return {"key": key, "status": "ok", "root": roots[0] if roots else extra[0],
            "entry": entry, "copied": copied, "mb": round(bytes_used / 1048576.0, 1),
            "cut": bool(truncated), "sched": tj["sched"]}


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default="", help="逗号分隔的 key，只处理这些")
    ap.add_argument("--clean", action="store_true", help="导入前删掉目标目录（重建时用）")
    args = ap.parse_args(argv)

    seed = _read_json(SEED, {})
    tasks = seed.get("tasks") or []
    ws_root = os.path.dirname(os.path.dirname(ROOT))       # 仓库同级：workspace 根
    ws = (seed.get("workspace") or WS_TOKEN).replace(WS_TOKEN, ws_root)
    if not os.path.isdir(ws):
        ws = ws_root
    only = {x.strip() for x in args.only.split(",") if x.strip()}

    idx: Optional[Dict[str, List[str]]] = None
    if any(not t.get("cwd") for t in tasks if not only or t.get("key") in only):
        idx = build_name_index(ws)

    rows = []
    for t in tasks:
        if only and t.get("key") not in only:
            continue
        if t.get("skip"):
            rows.append({"key": t.get("key"), "status": "SKIP", "root": None,
                         "entry": None, "copied": 0, "mb": 0, "cut": False,
                         "sched": None, "why": t.get("skip_reason") or "清单标记 skip"})
            continue
        if args.clean and not args.dry_run:
            d = os.path.join(CASE_DIR, t["key"] or "_")
            if t.get("key") and os.path.isdir(d):
                shutil.rmtree(d)
        rows.append(import_one(t, ws, args.dry_run, idx))

    ok = [r for r in rows if r["status"] == "ok"]
    no = [r for r in rows if r["status"] not in ("ok", "SKIP")]
    total_mb = sum(r.get("mb") or 0 for r in ok)
    skipped = [r for r in rows if r["status"] == "SKIP"]
    print("%s导入 %d 个任务，成功 %d，跳过 %d，无源 %d，合计 %.1f MB"
          % ("[dry-run] " if args.dry_run else "", len(rows), len(ok),
             len(skipped), len(no), total_mb))
    for r in rows:
        print("  %-24s %-9s %4s 个文件 %5s MB %-4s %s"
              % (r["key"], r["status"], r.get("copied") or 0, r.get("mb") or 0,
                 "CUT" if r.get("cut") else "", r.get("entry") or "-"))
    for r in rows:
        if r["status"] == "SKIP":
            print("  - %-24s 跳过：%s" % (r["key"], r.get("why") or ""))
    for r in no:
        print("  ! %s 找不到源目录（cwd 缺失且按入口文件名也没搜到）" % r["key"])
    return 0 if not no else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))