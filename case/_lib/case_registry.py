#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""case_registry.py — case 任务注册表

读 `case/<key>/task.json`，给两处共用：
  · scheduler.py（TaskManager）：当成模块纳入调度
  · build.py（看板）：当成「case worker」渲染到对应 section

只做读取与校验，不写盘、不执行任务。

用法：
    python3 case/_lib/case_registry.py            # 打印清单
    python3 case/_lib/case_registry.py --json     # 输出 JSON
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
CASE_DIR = os.path.dirname(HERE)
ROOT = os.path.dirname(CASE_DIR)

SECTIONS = ("stock", "futures", "realestate", "macro", "liquidity", "bond",
            "option", "risk", "modules")
KEY_RE = re.compile(r"^[a-z0-9_]{2,40}$")
AT_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _read_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def validate_task(t: Dict, dir_key: Optional[str] = None) -> List[str]:
    """返回错误列表，空列表表示通过。"""
    errs: List[str] = []
    if not isinstance(t, dict):
        return ["task.json 不是对象"]
    k = t.get("key")
    if not isinstance(k, str) or not KEY_RE.match(k):
        errs.append("key 非法（要求 [a-z0-9_]{2,40}）")
    if dir_key and k != dir_key:
        errs.append("key 与目录名不一致：%s != %s" % (k, dir_key))
    if not str(t.get("title") or "").strip():
        errs.append("缺 title")
    if t.get("section") not in SECTIONS:
        errs.append("section 非法：%r（可选 %s）" % (t.get("section"), "/".join(SECTIONS)))
    if not str(t.get("cmd") or "").strip():
        errs.append("缺 cmd")
    s = t.get("sched")
    if not isinstance(s, dict):
        errs.append("sched 必须是对象")
    else:
        if s.get("mode") not in ("daily", "weekly", "monthly"):
            errs.append("sched.mode 非法：%r" % s.get("mode"))
        if not AT_RE.match(str(s.get("at") or "")):
            errs.append("sched.at 必须是 HH:MM")
        if s.get("mode") == "monthly":
            ds = s.get("days")
            if not (isinstance(ds, list) and ds and all(isinstance(x, int) and 1 <= x <= 31 for x in ds)):
                errs.append("monthly 需要 sched.days（1-31 整数数组）")
        else:
            wds = s.get("weekdays")
            if wds is not None and not (isinstance(wds, list) and wds
                                        and all(isinstance(x, int) and 1 <= x <= 7 for x in wds)):
                errs.append("weekdays 必须是 1-7 整数数组")
    metrics = t.get("metrics", [])
    if not isinstance(metrics, list):
        errs.append("metrics 必须是数组")
    else:
        for i, m in enumerate(metrics):
            if not isinstance(m, dict) or not m.get("key") or not m.get("sec"):
                errs.append("metrics[%d] 缺 key/sec" % i)
    return errs


def load_tasks(base: Optional[str] = None, only_enabled: bool = False) -> List[Dict]:
    """扫描 case/*/task.json。坏配置跳过并写进 errors（不中断整体）。"""
    base = base or CASE_DIR
    out: List[Dict] = []
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return out
    for name in names:
        if name.startswith("_") or name.startswith("."):
            continue
        d = os.path.join(base, name)
        tj = os.path.join(d, "task.json")
        if not os.path.isfile(tj):
            continue
        t = _read_json(tj, None)
        if not isinstance(t, dict):
            continue
        errs = validate_task(t, name)
        t = dict(t)
        t["_dir"] = d
        t["_errors"] = errs
        t["source"] = "case"
        t.setdefault("cwd", d)
        elif_off = not bool(t.get("enabled", True))
        if only_enabled and (errs or elif_off):
            continue
        out.append(t)
    return out


def _resolve_cwd(t: Dict) -> str:
    """cwd 解析：缺省或 "." 就是任务目录，相对路径也相对任务目录。"""
    raw = str(t.get("cwd") or ".").strip() or "."
    if raw == ".":
        return t["_dir"]
    if os.path.isabs(raw):
        return raw
    return os.path.normpath(os.path.join(t["_dir"], raw))


def as_modules(base: Optional[str] = None) -> List[Dict]:
    """转成 scheduler 认的模块结构（key/title/desc/domains/cwd/cmd/timeout/enabled/sched）。"""
    mods: List[Dict] = []
    for t in load_tasks(base):
        mods.append({
            "key": t["key"],
            "title": t.get("title") or t["key"],
            "desc": t.get("desc") or "",
            "domains": [t.get("section") or "modules"],
            "section": t.get("section"),
            "cwd": _resolve_cwd(t),
            "cmd": t["cmd"],
            "timeout": int(t.get("timeout") or 1800),
            "enabled": bool(t.get("enabled", True)),
            "sched": t.get("sched"),
            "metrics": t.get("metrics") or [],
            "source": "case",
            "_errors": t.get("_errors") or [],
        })
    return mods


def as_workers(base: Optional[str] = None) -> List[Dict]:
    """转成 build.py 的 worker 结构，用于在对应 section 渲染 case 任务结果。"""
    out: List[Dict] = []
    for t in load_tasks(base, only_enabled=True):
        if not t.get("metrics"):
            continue                      # 首跑还没回填指标的任务不上面板
        out.append({
            "key": t["key"],
            "app_id": "case:%s" % t["key"],
            "title": t.get("title") or t["key"],
            "desc": t.get("desc") or "",
            "sec": t.get("section"),
            "sched": t.get("sched"),
            "metrics": t.get("metrics") or [],
            "deliver": t.get("deliver") or {},
        })
    return out


def main(argv: List[str]) -> int:
    mods = as_modules()
    bad = [m for m in mods if m["_errors"]]
    if "--json" in argv:
        print(json.dumps({"tasks": mods}, ensure_ascii=False, indent=2))
        return 0
    print("case 任务 %d 个（有问题 %d 个）" % (len(mods), len(bad)))
    for m in mods:
        print("  %-24s %-11s %-8s %s" % (m["key"], m["section"] or "-",
                                          "on " if m["enabled"] else "off",
                                          m["title"]))
        for e in m["_errors"]:
            print("      ! %s" % e)
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))