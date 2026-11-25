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
# 平台会话目录（ses_xxxx）：任务源码的原始位置，**不是** case 任务目录。
# 有人把 CASE_EXTRA_DIRS 指到 workspace 根时会把它们当成任务扫，导致每个
# 会话目录都报「key 与目录名不一致」——这里直接跳过，不参与校验也不报错。
SESSION_DIR_RE = re.compile(r"^ses_[0-9a-f]{6,}$")
# 框架生命周期：有 pipeline 的任务由 case/_lib/runner.py 按这几个阶段驱动
PIPELINE_STAGES = ("fetch", "check", "render", "emit", "dispatch")
VISIBILITIES = ("public", "internal")
RUNNER = os.path.join(HERE, "runner.py")


def _read_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def visibility_of(t: Dict) -> str:
    """任务的可见性：public 上牌面，internal 只跑与落库。缺省按 public。"""
    v = str(t.get("visibility") or "public").strip().lower()
    return v if v in VISIBILITIES else "public"


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


def validate_task(t: Dict, dir_key: Optional[str] = None,
                  autofix_key: bool = True) -> List[str]:
    """返回错误列表，空列表表示通过。

    key 与目录名不一致**不再判失败**：目录名是唯一标识，task.json 里的 key
    只是它的一个副本。历史上从会话目录搬过来的任务普遍带着旧 key，按旧规则
    会让整个数据层加载失败——这不是「任务有问题」，是配置搬运的残留。
    默认以目录名为准自动纠正并把原值留在 _key_renamed_from 里备查；
    需要严格模式时传 autofix_key=False。
    """
    errs: List[str] = []
    if not isinstance(t, dict):
        return ["task.json 不是对象"]
    k = t.get("key")
    if dir_key and k != dir_key:
        if autofix_key:
            t["_key_renamed_from"] = k
            t["key"] = dir_key
            k = dir_key
        else:
            errs.append("key 与目录名不一致：%s != %s" % (k, dir_key))
    if not isinstance(k, str) or not KEY_RE.match(k):
        errs.append("key 非法（要求 [a-z0-9_]{2,40}）")
    if not str(t.get("title") or "").strip():
        errs.append("缺 title")
    vis = t.get("visibility") or "public"
    if vis not in VISIBILITIES:
        errs.append("visibility 非法：%r（可选 %s）" % (vis, "/".join(VISIBILITIES)))
    sec = t.get("section")
    if vis == "public":
        if sec not in SECTIONS:
            errs.append("section 非法：%r（public 必须给 %s 之一）"
                        % (sec, "/".join(SECTIONS)))
    elif sec and sec not in SECTIONS:
        errs.append("section 非法：%r（internal 可以留空，给了就必须在 %s 内）"
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
            if SESSION_DIR_RE.match(name):
                # 会话目录不是任务目录：CASE_EXTRA_DIRS 指到了 workspace 根。
                # 直接跳过，既不加载也不计入失败（否则每个会话目录一条错误）。
                print("[case_registry] 跳过会话目录 %s：CASE_EXTRA_DIRS 应指向 case/ 这一层，"
                      "不是 workspace 根" % os.path.join(root_dir, name), file=sys.stderr)
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
            "queue": t.get("queue") or "default",
            "metrics": t.get("metrics") or [],
            "source": "case",
            "_errors": t.get("_errors") or [],
        })
    return mods


def _q(s: str) -> str:
    return '"%s"' % str(s).replace('"', '\\"')


def as_workers(base: Optional[str] = None) -> List[Dict]:
    """转成 build.py 的 worker 结构，用于在对应 section 渲染 case 任务结果。

    只出 visibility=public 且有 section 的任务：internal 任务照跑照落库，但不上牌面。
    """
    out: List[Dict] = []
    for t in load_tasks(base, only_enabled=True):
        if not t.get("metrics"):
            continue                      # 首跑还没回填指标的任务不上面板
        if visibility_of(t) != "public" or not t.get("section"):
            continue                      # 私有任务：运行与落库照旧，只是不展示
        out.append({
            "key": t["key"],
            "app_id": "case:%s" % t["key"],
            "title": t.get("title") or t["key"],
            "desc": t.get("desc") or "",
            "sec": t.get("section"),
            "sched": t.get("sched"),
            "queue": t.get("queue") or "default",
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