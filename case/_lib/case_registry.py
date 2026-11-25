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
# 框架生命周期：有 pipeline 的任务由 case/_lib/runner.py 按这几个阶段驱动
PIPELINE_STAGES = ("fetch", "check", "render", "emit", "dispatch")
VISIBILITIES = ("public", "private")
# 别名过渡期：老任务写的是 internal，语义等同 private。等 precious-task 里
# 17 个 task.json 全部改成 private 之后再删掉这张表（见 case/_seed/visibility.md）。
VISIBILITY_ALIASES = {"internal": "private"}
RUNNER = os.path.join(HERE, "runner.py")


def _read_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def visibility_of(t: Dict) -> str:
    """任务的可见性：public 上牌面，private 只跑与落库。缺省按 public。

    旧值 internal 归一成 private（别名过渡期），不产生错误、不悄悄当 public。
    未知值**原样返回**：这种任务由 validate_task 记一条错误、在调度里以
    「停用 + 配置错误」的形式露出来，绝不静默消失。
    """
    v = str(t.get("visibility") or "public").strip().lower()
    return VISIBILITY_ALIASES.get(v, v)


def task_dirs(base: Optional[str] = None) -> List[str]:
    """任务根目录列表：case/ 在前，CASE_EXTRA_DIRS 里的私有目录追加在后。"""
    dirs = [base or CASE_DIR]
    extra = (os.environ.get("CASE_EXTRA_DIRS") or "").replace(";", ":")
    for p in extra.split(":"):
        p = p.strip()
        if p and os.path.isdir(p) and p not in dirs:
            dirs.append(p)
    return dirs


def _validate_pipeline(p) -> List[str]:
    """pipeline 结构校验：阶段名合法、每阶段有 cmd 或内置实现。"""
    errs: List[str] = []
    if p is None:
        return errs
    if not isinstance(p, dict):
        return ["pipeline 必须是对象"]
    if p.get("on_fail") not in (None, "stop", "continue"):
        errs.append("pipeline.on_fail 只能是 stop/continue")
    ratio = p.get("min_ok_ratio")
    if ratio is not None and not (isinstance(ratio, (int, float)) and 0 <= ratio <= 1):
        errs.append("pipeline.min_ok_ratio 必须在 0-1")
    stages = p.get("stages")
    if not (isinstance(stages, list) and stages):
        return errs + ["pipeline.stages 必须是非空数组"]
    for i, st in enumerate(stages):
        if not isinstance(st, dict):
            errs.append("pipeline.stages[%d] 不是对象" % i)
            continue
        name = st.get("name")
        if name not in PIPELINE_STAGES:
            errs.append("pipeline.stages[%d].name 非法：%r（可选 %s）"
                        % (i, name, "/".join(PIPELINE_STAGES)))
        if (not st.get("cmd") and not st.get("builtin") and not st.get("hook")
                and name not in ("check", "emit")):
            errs.append("pipeline.stages[%d]（%s）缺 cmd/builtin/hook" % (i, name))
    return errs


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
    vis = str(t.get("visibility") or "public").strip().lower()
    # internal 是 private 的旧名，过渡期内直接认，不算错误（否则 precious-task
    # 里 17 个旧值会让任务带着 _errors 静默停跑）。
    vis = VISIBILITY_ALIASES.get(vis, vis)
    if vis not in VISIBILITIES:
        errs.append("visibility 非法：%r（可选 %s；internal 是 private 的旧别名）"
                    % (t.get("visibility"), "/".join(VISIBILITIES)))
    sec = t.get("section")
    if vis == "public":
        if sec not in SECTIONS:
            errs.append("section 非法：%r（public 必须给 %s 之一）"
                        % (sec, "/".join(SECTIONS)))
    elif sec and sec not in SECTIONS:
        errs.append("section 非法：%r（private 可以留空，给了就必须在 %s 内）"
                    % (sec, "/".join(SECTIONS)))
    dep = t.get("depends_on")
    if dep is not None:
        if not isinstance(dep, list) or not all(isinstance(x, str) and KEY_RE.match(x) for x in dep):
            errs.append("depends_on 必须是由任务 key 组成的数组")
    if not str(t.get("cmd") or "").strip() and not isinstance(t.get("pipeline"), dict):
        errs.append("缺 cmd（或给一个 pipeline 让 runner 分阶段执行）")
    errs.extend(_validate_pipeline(t.get("pipeline")))
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
    """扫描任务目录（case/ + CASE_EXTRA_DIRS）。坏配置记错误但不中断整体。

    同一个 key 出现在多个目录时，先扫描到的生效（case/ 优先），后出现的记一条冲突。
    """
    out: List[Dict] = []
    seen: Dict[str, str] = {}
    for root_dir in task_dirs(base):
        try:
            names = sorted(os.listdir(root_dir))
        except OSError:
            continue
        for name in names:
            if name.startswith("_") or name.startswith("."):
                continue
            d = os.path.join(root_dir, name)
            tj = os.path.join(d, "task.json")
            if not os.path.isfile(tj):
                continue
            t = _read_json(tj, None)
            if not isinstance(t, dict):
                continue
            errs = validate_task(t, name)
            t = dict(t)
            t["_dir"] = d
            t["source"] = "case" if root_dir == CASE_DIR else "case-external"
            if name in seen:
                msg = "key 重复：%s 已由 %s 提供，本次忽略 %s" % (name, seen[name], d)
                print("[case_registry] " + msg, file=sys.stderr)
                errs.append(msg)
                t["_errors"] = errs
                continue
            seen[name] = d
            t["_errors"] = errs
            t.setdefault("cwd", d)
            if only_enabled and (errs or not bool(t.get("enabled", True))):
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
    """转成 scheduler 认的模块结构（key/title/desc/domains/cwd/cmd/timeout/enabled/sched）。

    有 pipeline 的任务，cmd 统一换成 runner 入口，由框架按阶段驱动；
    没有 pipeline 的沿用原 cmd 直跑（兼容尚未改造的任务）。
    """
    mods: List[Dict] = []
    for t in load_tasks(base):
        pipe = t.get("pipeline") if isinstance(t.get("pipeline"), dict) else None
        if pipe:
            cmd = "%s %s --task %s" % (_q(sys.executable), _q(RUNNER), _q(t["key"]))
        else:
            cmd = t["cmd"]
        mods.append({
            "key": t["key"],
            "title": t.get("title") or t["key"],
            "desc": t.get("desc") or "",
            "domains": [t.get("section") or "modules"],
            "section": t.get("section"),
            "cwd": _resolve_cwd(t),
            "cmd": cmd,
            "legacy_cmd": t.get("cmd") if pipe else "",
            "pipeline": bool(pipe),
            "timeout": int(t.get("timeout") or 1800),
            "enabled": bool(t.get("enabled", True)),
            "sched": t.get("sched"),
            "metrics": t.get("metrics") or [],
            "source": "case",
            "_errors": t.get("_errors") or [],
        })
    return mods


def _q(s: str) -> str:
    return '"%s"' % str(s).replace('"', '\\"')


def as_workers(base: Optional[str] = None) -> List[Dict]:
    """转成 build.py 的 worker 结构，用于在对应 section 渲染 case 任务结果。

    只出 visibility=public 且有 section 的任务：private 任务照跑照落库，但不上牌面。
    """
    out: List[Dict] = []
    for t in load_tasks(base, only_enabled=True):
        if t.get("_errors"):
            continue                      # 配置错误的进模块管理页（标红），不上数据板块
        if not t.get("metrics"):
            continue                      # 首跑还没回填指标的任务不上面板
        if visibility_of(t) != "public" or not t.get("section"):
            continue                      # 私有任务：运行与落库照旧，只是不展示
        if t.get("build_workers") is False:
            continue                      # 已有专用 render_* 页面的任务，不再出通用 case 块
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
