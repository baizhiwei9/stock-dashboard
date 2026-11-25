#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""import_py.py — 把有代码的任务搬进 case/，只搬程序文件（.py）

与旧 import_task.py 的区别（旧版已废弃）：
  · 不再整目录白名单拷贝，改用 collect_code 的 import 闭包，只拿这个任务
    真正会 import 到的 .py —— 会话目录里上千个历史实验脚本不会被带进来。
  · 只复制 .py。数据、产物、报告、图片、中间 json 一概不进仓库。
  · 不设文件数上限、不截断；代码多的任务分批次提交（--batch i/n）。
  · 公共库（pylibs）不逐任务复制：只在 task.json 里记 requires，
    并用 `PYLIBS_DIR` 环境变量引用。需要自包含时加 --with-shared 放一份到 _shared/。

清单来源：case/_seed/tasks.json 的 root / entry / skip / needs_llm。

用法：
    python3 case/_tools/import_py.py --dry-run          # 只看统计
    python3 case/_tools/import_py.py                    # 全量导入（先清空旧任务目录）
    python3 case/_tools/import_py.py --batch 1/4        # 只做第 1 批（共 4 批）
    python3 case/_tools/import_py.py --only tda_spd,ot_unified
    python3 case/_tools/import_py.py --with-shared      # 附带一份 pylibs 到 _shared/
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from typing import Dict, List, Optional, Set

HERE = os.path.dirname(os.path.abspath(__file__))
CASE_DIR = os.path.dirname(HERE)
ROOT = os.path.dirname(CASE_DIR)
SEED = os.path.join(CASE_DIR, "_seed", "tasks.json")

sys.path.insert(0, HERE)
import collect_code as cc                                          # noqa: E402

WS_TOKEN = "{WS}"
SKIP_DIRS_PKG = {"tests", "__pycache__", ".git", "docs", "doc", "reports",
                 "output", "outputs", "data", "legacy", "old", "archive"}


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


def external_requires(paths: List[str], ws: str) -> List[str]:
    """root 外依赖的顶层包名去重（pylibs / feishu_router …），不复制文件。"""
    seen: Set[str] = set()
    for p in paths:
        try:
            rel = os.path.relpath(p, ws)
        except ValueError:
            continue
        if rel.startswith(".."):
            continue
        seen.add(rel.split(os.sep)[0])
    return sorted(seen)


def cron_to_sched(cron: str) -> Dict:
    """5 段 cron → modules.json 的 sched 结构（daily / weekly / monthly）。"""
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


def rekey(old: Dict, key: str, task: Dict, entry: str, nfiles: int,
          requires: List[str], extra_note: str = "") -> Dict:
    """生成 task.json；保留手工改过的 cmd / metrics / enabled / deliver。"""
    base = task.get("cmd") or (("python3 -u %s" % entry) if entry else "python3 -u main.py")
    if requires and "PYTHONPATH" not in base:
        # 公共库走环境变量；没设就明确报错，不去猜相对路径（cwd 是 case/<key>）
        base = ('PYTHONPATH="${PYLIBS_DIR:+$PYLIBS_DIR:}${PYTHONPATH:+$PYTHONPATH:}." ' + base)
    cmd = base
    cwd = "."
    if old.get("cmd_manual"):
        cmd = old.get("cmd") or cmd
        cwd = old.get("cwd") or cwd
    t = {
        "key": key,
        "title": task.get("name") or key,
        "desc": old.get("desc") or "",
        "section": task.get("section") or old.get("section") or "macro",
        "metrics": old.get("metrics") or [],
        "sched": old.get("sched") or cron_to_sched(task.get("cron") or ""),
        "cmd": cmd,
        "cwd": cwd,
        "timeout": int(old.get("timeout") or 1800),
        "enabled": bool(old.get("enabled", True)),
        "deliver": old.get("deliver") or task.get("deliver") or {"report": True, "email": False},
        "code_files": nfiles,
        "requires": requires,
        "root": task.get("root") or "",
        "entry": entry,
        "needs_llm": bool(task.get("needs_llm")),
        "imported_from": "case/_seed/tasks.json + _tools/collect_code.py",
    }
    if old.get("cmd_manual"):
        t["cmd_manual"] = True
    if old.get("metrics"):
        t["metrics"] = old["metrics"]
    if task.get("needs_llm") and task.get("note"):
        t["needs_llm_note"] = task["note"]
    if extra_note:
        t["import_note"] = extra_note
    return t


def pkg_files(root: str, maxdepth: int = 5) -> List[str]:
    """包模式（-m 模块入口）用：收集 root 下所有 .py（排除测试/文档/产物）。"""
    out: List[str] = []
    for dp, dns, fns in os.walk(root):
        rel = os.path.relpath(dp, root)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        if depth > maxdepth:
            dns[:] = []
            continue
        dns[:] = [d for d in dns if d.lower() not in SKIP_DIRS_PKG and not d.startswith(".")]
        for f in fns:
            if f.endswith(".py"):
                out.append(os.path.relpath(os.path.join(dp, f), root))
    return sorted(out)


def copy_files(key: str, src_root: str, rels: List[str], dry: bool) -> int:
    dest = os.path.join(CASE_DIR, key)
    n = 0
    for rel in rels:
        src = os.path.join(src_root, rel)
        if not os.path.isfile(src):
            continue
        if not dry:
            dst = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
        n += 1
    return n


def copy_shared(ws: str, dry: bool) -> int:
    """--with-shared：把 pylibs 的 .py 全量放一份到 case/_shared/pylibs。"""
    src = os.path.join(ws, "pylibs")
    if not os.path.isdir(src):
        return 0
    dest = os.path.join(CASE_DIR, "_shared", "pylibs")
    if not dry and os.path.isdir(dest):
        shutil.rmtree(dest)
    n = 0
    for dp, dns, fns in os.walk(src):
        dns[:] = [d for d in dns if d != "__pycache__" and not d.startswith(".")]
        for f in fns:
            if not f.endswith(".py"):
                continue
            if not dry:
                rel = os.path.relpath(os.path.join(dp, f), src)
                dst = os.path.join(dest, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(os.path.join(dp, f), dst)
            n += 1
    return n


def clean_task_dirs(dry: bool = False) -> List[str]:
    """删掉 case 下所有任务目录（保留 _ 开头的工具/共享目录）。"""
    gone: List[str] = []
    for n in sorted(os.listdir(CASE_DIR)):
        if n.startswith("_") or n.startswith("."):
            continue
        d = os.path.join(CASE_DIR, n)
        if not os.path.isdir(d):
            continue
        if not dry:
            shutil.rmtree(d)
        gone.append(n)
    return gone


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--batch", default="", help="i/n，例如 1/4")
    ap.add_argument("--keep", action="store_true", help="不清理已有任务目录")
    ap.add_argument("--with-shared", action="store_true", help="附带 pylibs 到 _shared/")
    a = ap.parse_args(argv)

    seed = _read_json(SEED, {})
    ws_root = os.path.dirname(os.path.dirname(ROOT))
    ws = (seed.get("workspace") or WS_TOKEN).replace(WS_TOKEN, ws_root)
    if not os.path.isdir(ws):
        ws = ws_root
    tasks = seed.get("tasks") or []
    only = {x.strip() for x in a.only.split(",") if x.strip()}
    if only:
        tasks = [t for t in tasks if t["key"] in only]
    if a.batch:
        i, n = [int(x) for x in a.batch.split("/")]
        size = (len(tasks) + n - 1) // n
        tasks = tasks[(i - 1) * size: i * size]

    if not a.keep and not only and not a.batch:
        gone = clean_task_dirs(dry=a.dry_run)
        print("清理旧任务目录 %d 个（保留 _lib/_schema/_seed/_tools）" % len(gone))

    rows = []
    for t in tasks:
        key = t["key"]
        if t.get("skip") or not t.get("root"):
            rows.append({"key": key, "status": "SKIP", "n": 0, "kb": 0,
                         "why": t.get("skip_reason") or "无程序代码"})
            continue
        root = t["root"].replace(WS_TOKEN, ws)
        if not os.path.isdir(root):
            rows.append({"key": key, "status": "NO-SRC", "n": 0, "kb": 0,
                         "why": "源目录不存在"})
            continue

        entry = t.get("entry") or ""
        old = _read_json(os.path.join(CASE_DIR, key, "task.json"), {}) or {}
        note = ""
        requires: List[str] = []
        if entry:
            r = cc.collect(root, entry, extra_roots=[os.path.join(ws, "pylibs")])
            if r.get("err"):
                rows.append({"key": key, "status": "NO-ENTRY", "n": 0, "kb": 0,
                             "why": r["err"]})
                continue
            rels = r["files"]
            requires = external_requires(r.get("external") or [], ws)
            if r.get("dyn"):
                note = "含动态 import，按入口目录模块集兜底"
        else:
            rels = pkg_files(root)
            note = "包模式（-m 模块入口），收集包内全部 .py"

        n = copy_files(key, root, rels, a.dry_run)
        kb = 0
        for rel in rels:
            try:
                kb += os.path.getsize(os.path.join(root, rel))
            except OSError:
                pass
        if requires:
            note = (note + "；" if note else "") + "外部依赖：%s" % ",".join(requires)
        tj = rekey(old, key, t, entry, n, requires, note)
        if not a.dry_run:
            _write_json(os.path.join(CASE_DIR, key, "task.json"), tj)
        rows.append({"key": key, "status": "ok", "n": n, "kb": round(kb / 1024.0, 1),
                     "req": ",".join(requires), "why": note})

    shared = 0
    if a.with_shared:
        shared = copy_shared(ws, a.dry_run)
        print("_shared/pylibs：%d 个 .py%s" % (shared, "（dry-run）" if a.dry_run else ""))

    ok = [r for r in rows if r["status"] == "ok"]
    print("%s导入 %d 个任务：有代码 %d，跳过/无源 %d"
          % ("[dry-run] " if a.dry_run else "", len(rows), len(ok), len(rows) - len(ok)))
    for r in rows:
        print("  %-24s %-8s %4s 个 .py %8s KB %s"
              % (r["key"], r["status"], r.get("n") or 0, r.get("kb") or 0,
                 ("req=%s " % r["req"] if r.get("req") else "") + (r.get("why") or "")))
    print("合计 %d 个 .py" % sum(r.get("n") or 0 for r in ok))
    return 0 if len(ok) == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))