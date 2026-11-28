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

SECTIONS = ("stock", "watch", "pick", "futures", "realestate", "macro",
            "liquidity", "bond", "option", "risk", "modules")
# watch=自选股、pick=选股：只在私有页出图的两个板块（公开页只取 visibility=public，
# 这里加白名单不会让它们跑到公开页去）。
# 没写 section / section_domain 的私有任务不丢，归到「未分板块」（私有页专用）。
UNSORTED = "unsorted"
KEY_RE = re.compile(r"^[a-z0-9_]{2,40}$")
AT_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
# 框架生命周期：有 pipeline 的任务由 case/_lib/runner.py 按这几个阶段驱动
PIPELINE_STAGES = ("fetch", "check", "render", "emit", "dispatch")
VISIBILITIES = ("public", "private")
# 别名过渡期：老任务写的是 internal，语义等同 private。等 precious-task 里
# 17 个 task.json 全部改成 private 之后再删掉这张表（见 case/_seed/visibility.md）。
VISIBILITY_ALIASES = {"internal": "private"}
# 私有任务的 section_domain 可能超出面板 Tab（如 credit 落在债券信用下），统一映射。
# 与 build_private.py 的板块顺序保持一个源：新增别名只改这里，不要在两处平行维护。
SECTION_ALIAS = {"credit": "bond", "credit_spread": "bond", "macro_watch": "macro"}
RUNNER = os.path.join(HERE, "runner.py")


def _read_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        return default
    except ValueError as e:
        # JSON 坏掉不能静默吞：调用方照旧拿 default（缺省 None，行为不变），
        # 但必须留痕 —— 否则整份 task.json 会被当成「没有这个任务」悄悄消失，
        # 只表现为任务数少一个，没人查得出来。
        print("[case_registry] JSON 解析失败：%s: %s" % (path, e), file=sys.stderr)
        return default


def _safe_int(v, default: int) -> int:
    """timeout 之类做一次防御式转换；非法值回退默认，别让一个坏字段拖垮全部任务。"""
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def visibility_of(t: Dict) -> str:
    """任务的可见性：public / private。缺省按 public。

    2026-11-25 起公私**同页展示**：public 卡片标「公开」，private 卡片标「私有」，
    两者只差一个标记与代码所在仓库，不再维护两套面板。

    旧值 internal 归一成 private（别名过渡期），不产生错误、不悄悄当 public。
    未知值**原样返回**：这种任务由 validate_task 记一条错误、在调度里以
    「停用 + 配置错误」的形式露出来，绝不静默消失。
    """
    v = str(t.get("visibility") or "public").strip().lower()
    return VISIBILITY_ALIASES.get(v, v)


def section_of(t: Dict) -> str:
    """任务归属板块：section 优先，私有任务退到 section_domain，最后映射到已有 Tab。

    私有任务的历史配置习惯把标签写在 section_domain 里（section 留空），
    这里统一成一个口径，面板与模块管理都用它。
    """
    for k in ("section", "section_domain"):
        v = str(t.get(k) or "").strip()
        if v:
            return SECTION_ALIAS.get(v, v)
    return ""


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
    if not isinstance(t.get("enabled", True), bool):
        errs.append("enabled 必须是布尔值")
    to = t.get("timeout", 1800)
    if not isinstance(to, int) or isinstance(to, bool) or to < 60:
        errs.append("timeout 必须是 >=60 的整数秒")
    s = t.get("sched")
    if not isinstance(s, dict):
        errs.append("sched 必须是对象")
    else:
        if s.get("mode") not in ("daily", "weekly", "monthly"):
            errs.append("sched.mode 非法：%r" % s.get("mode"))
        at = s.get("at")
        if isinstance(at, list):
            if not at:
                errs.append('sched.at 数组不能为空：要么写 "07:50"，要么写 ["07:50","19:55"]')
            elif len(at) > 6:
                errs.append("sched.at 数组最多 6 个时间点（实际 %d 个）" % len(at))
            else:
                for i, x in enumerate(at):
                    if not isinstance(x, str) or not AT_RE.match(x):
                        errs.append('sched.at[%d] 必须是 HH:MM（如 "07:50"），实际 %r' % (i, x))
        elif not AT_RE.match(str(at or "")):
            errs.append('sched.at 必须是 HH:MM（如 "19:55"），'
                        '或一天多个时间点的数组（如 ["07:50","19:55"]）')
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
            # 任务目录可能是 case/<key>/ 或 case/<群>/<key>/ 两层：私有仓把几个
            # 宏观周报收在 macro_watch/<key>/ 这类容器目录下，只扫一层会漏。
            # 容器本身没有 task.json 自然跳过，不误当成任务。
            group_dir = os.path.join(root_dir, name)
            candidates = [(name, group_dir)]
            if os.path.isdir(group_dir):
                try:
                    subs = sorted(os.listdir(group_dir))
                except OSError:
                    subs = []
                for sub in subs:
                    if sub.startswith("_") or sub.startswith("."):
                        continue
                    candidates.append((sub, os.path.join(group_dir, sub)))
            for dir_key, d in candidates:
                tj = os.path.join(d, "task.json")
                if not os.path.isfile(tj):
                    continue
                t = _read_json(tj, None)
                if not isinstance(t, dict):
                    continue
                t = dict(t)
                t["_dir"] = d
                t["source"] = "case" if root_dir == CASE_DIR else "case-external"
                # 私有仓（CASE_EXTRA_DIRS）里没写 visibility 的老任务按 private。
                # 只在这一层归一：validate_task / visibility_of / as_workers /
                # as_modules / 私有页看到的是同一个结论，不在各层各自兜底
                # （否则 validate_task 按 public 判、板块留空就成了「配置错误」）。
                if (t["source"] == "case-external"
                        and not str(t.get("visibility") or "").strip()):
                    t["visibility"] = "private"
                errs = validate_task(t, dir_key)
                if dir_key in seen:
                    msg = "key 重复：%s 已由 %s 提供，本次忽略 %s" % (dir_key, seen[dir_key], d)
                    print("[case_registry] " + msg, file=sys.stderr)
                    errs.append(msg)
                    t["_errors"] = errs
                    continue
                seen[dir_key] = d
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
            "domains": [section_of(t) or "modules"],
            "section": section_of(t) or None,
            "visibility": visibility_of(t),
            "cwd": _resolve_cwd(t),
            "cmd": cmd,
            "legacy_cmd": t.get("cmd") if pipe else "",
            "pipeline": bool(pipe),
            "timeout": _safe_int(t.get("timeout"), 1800),
            "enabled": bool(t.get("enabled", True)),
            "sched": t.get("sched"),
            # not_before 是 scheduler 的「不许提前拉」闸门（见 _not_before_time）。
            # 不透传的话，pipeline 任务即使写了 not_before 也会被 pull_forward 提前起。
            "not_before": t.get("not_before"),
            "metrics": t.get("metrics") or [],
            "source": "case",
            "_errors": t.get("_errors") or [],
        })
    return mods


def _q(s: str) -> str:
    return '"%s"' % str(s).replace('"', '\\"')


def _metrics_declared(t: Dict) -> List[Dict]:
    """task.json 里声明的 metrics（允许写成字符串简写）。"""
    out: List[Dict] = []
    for it in (t.get("metrics") or []):
        if isinstance(it, str):
            it = {"key": it}
        if isinstance(it, dict) and it.get("key"):
            out.append(it)
    return out


def _metrics_from_latest(t: Dict) -> List[Dict]:
    """没声明 metrics 时，从 out/latest.json 的 points 反推声明。

    私有任务（precious-task）历来只写 out/latest.json、不维护 metrics 清单；
    有了这条兜底，它们不改 task.json 也能上同一块牌面。
    """
    d = _read_json(os.path.join(t["_dir"], "out", "latest.json"), None)
    if not isinstance(d, dict):
        return []
    out: List[Dict] = []
    seen = set()
    for p in (d.get("points") or []):
        if not isinstance(p, dict):
            continue
        k, s = p.get("key"), p.get("sec")
        if not k or not s or (k, s) in seen:
            continue
        seen.add((k, s))
        out.append({"key": k, "sec": s, "name": p.get("name") or k,
                    "unit": p.get("unit") or "", "dec": p.get("dec", 2)})
    return out


def as_workers(base: Optional[str] = None, visibility: str = "public",
               include_disabled: Optional[bool] = None,
               include_unsorted: bool = False) -> List[Dict]:
    """转成 build.py 的 worker 结构：两个页面各取自己那一半，互不混排。

    2026-11-25 的口径是**两套页面**：
      · 公开页 `build.py → index.html` 只出 visibility=public（开源单日报）；
      · 私有页 `build_private.py → private.html` 只出 private（含旧别名 internal）。
    两页共用同一套样式、卡片渲染与 data_hub 取数，不复制框架、不另算数据。
      · 板块取 section，私有任务没有 section 时退到 section_domain；
      · 指标优先用 task.json 的 metrics 声明，没声明就从 out/latest.json 反推；
      · 私有任务没有指标也先出任务卡（跑一次、publish 回填后自动出数值）；
        公开任务仍等回填后才占位，避免空壳铺满板块。

    私有页（build_private）传 ``include_unsorted=True``：没写 section/section_domain
    的任务归「未分板块」而不是被丢；「modules」也保留，单独占一个板块。
    """
    if visibility not in ("public", "private", "all"):
        raise ValueError("visibility 只能是 public/private/all")
    if include_disabled is None:
        # 公开页沿用旧行为（停用任务不上牌面）；私有页要看历史产物，停用也上
        include_disabled = visibility != "public"
    out: List[Dict] = []
    for t in load_tasks(base, only_enabled=not include_disabled):
        if t.get("_errors"):
            continue                      # 配置错误的进模块管理页（标红），不上数据板块
        vis = visibility_of(t)            # 外部仓缺 visibility 的归一已在 load_tasks 做
        if visibility != "all" and vis != visibility:
            continue                      # 两套页面各取自己那一半，不混排
        sec = section_of(t)
        if not sec and include_unsorted:
            sec = UNSORTED                # 没写板块的任务不丢，归「未分板块」
        if not sec:
            continue
        if sec == "modules":
            if not include_unsorted:
                continue                  # 工具类任务只进模块管理页，不单独占一个数据板块
        elif sec != UNSORTED and sec not in SECTIONS:
            continue                      # 未知板块不上牌面
        if t.get("build_workers") is False and not include_unsorted:
            continue                      # 已有专用 render_* 页面的任务，不再出通用 case 块；
                                          # 私有页没有专用渲染，照常列出（与 #73 口径一致）
        metrics = _metrics_declared(t) or _metrics_from_latest(t)
        if not metrics and vis == "public":
            continue                      # 公开任务回填指标前不占位
        out.append({
            "key": t["key"],
            "app_id": "case:%s" % t["key"],
            "dir": t["_dir"],                 # 私有页据此扫 out/ charts/ runs/ 里的图
            "title": t.get("title") or t["key"],
            "desc": t.get("desc") or "",
            "sec": sec,
            "tab": sec,
            "visibility": vis,
            "source": "case",
            "sched": t.get("sched"),
            # not_before 是 scheduler 的「不许提前拉」闸门（见 _not_before_time）。
            # 不透传的话，pipeline 任务即使写了 not_before 也会被 pull_forward 提前起。
            "not_before": t.get("not_before"),
            # metrics 取声明或 out/latest.json 反推（私有任务不维护清单）
            "metrics": metrics,
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
