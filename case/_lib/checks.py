#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""checks.py — case 任务产出的通用校验

框架的「检查」阶段默认用这里的实现：不关心任务业务，只看产物是否真的可用。

校验项（每项有 fatal 标记，fatal 不通过 → 整次运行判失败并报警）：

  1. data   取数产物存在且非空（data/ 下至少一个非空文件）
  2. report 报告产物存在且是本次运行写的（out/ 下报告文件 mtime >= 本次开始时间）
  3. metrics out/latest.json 存在、points 非空、字段合法
  4. fresh  指标的 obs_date 不早于 today - freshness_days
  5. quality 非 missing 的比例不低于 min_ok_ratio（算不出来可以，但不能全算不出来）
  6. coverage 指标覆盖率硬地板：缺失过半（或全缺失）直接判失败，防止
     min_ok_ratio 被调到 0.3 时 70% 指标缺失仍算通过

任务可以在自己的 pipeline.py 里调 `checks.artifacts(ctx, **opts)` 复用，也可以完全自己写。

命令行：
    python3 checks.py --task <key> [--freshness-days 3] [--min-ok-ratio 0.5]
输出 out/check.json，退出码 0=通过 / 3=不通过。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
CASE_DIR = os.path.dirname(HERE)
PLACEHOLDERS = {"", "n/a", "na", "nan", "none", "null", "-", "--", "tbd", "dash"}
# 指标覆盖率硬地板：与任务自己的 min_ok_ratio 取较大值生效（见 coverage 判据）。
COVERAGE_FLOOR = 0.5


def _read_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def _files(patterns: List[str]) -> List[str]:
    out: List[str] = []
    for pat in patterns or []:
        out.extend(glob.glob(pat))
    return sorted(set(out))


def _is_placeholder(v: Any) -> bool:
    return isinstance(v, str) and v.strip().lower() in PLACEHOLDERS


def _item(name: str, ok: bool, detail: str = "", fatal: bool = True) -> Dict:
    return {"name": name, "ok": bool(ok), "detail": detail, "fatal": bool(fatal)}


def artifacts(task: Dict, run_start: Optional[float] = None, *,
              freshness_days: int = 3, min_ok_ratio: float = 0.5,
              require_data: bool = True, require_report: bool = True,
              require_metrics: bool = True) -> Dict:
    """通用产物校验。task 是 task.json 字典；run_start 是本次运行的 time.time()。"""
    tdir = task.get("_dir") or CASE_DIR
    data_dir = os.path.join(tdir, "data")
    out_dir = os.path.join(tdir, "out")
    pipe = task.get("pipeline") or {}
    arts = pipe.get("artifacts") or {}
    rep_pat = arts.get("report") or ["out/report.*", "out/*.html", "out/*.md"]
    dat_pat = arts.get("data") or ["data/*", "data/**/*"]
    now = datetime.now().timestamp()
    items: List[Dict] = []

    # 1) 取数产物
    if require_data:
        found = [p for p in _files([os.path.join(tdir, x) for x in dat_pat])
                 if os.path.isfile(p)]
        nonempty = [p for p in found if os.path.getsize(p) > 0]
        if nonempty:
            items.append(_item("data", True, "%d 个文件，最新 %s" % (
                len(nonempty), os.path.basename(max(nonempty, key=os.path.getmtime)))))
        elif found:
            items.append(_item("data", False, "取数产物全是空文件：%s" % ", ".join(
                os.path.basename(p) for p in found[:5])))
        else:
            items.append(_item("data", False, "data/ 下没有任何取数产物"))

    # 2) 报告产物
    if require_report:
        reps = [p for p in _files([os.path.join(tdir, x) for x in rep_pat])
                if os.path.isfile(p) and os.path.getsize(p) > 200]
        fresh = [p for p in reps if run_start is None or os.path.getmtime(p) >= run_start - 1]
        if fresh:
            items.append(_item("report", True, os.path.basename(max(fresh, key=os.path.getmtime))))
        elif reps:
            items.append(_item("report", False, "报告是旧文件，本次没有重新生成：%s" % ", ".join(
                os.path.basename(p) for p in reps[:3])))
        else:
            items.append(_item("report", False, "out/ 下没有报告产物（%s）" % "/".join(rep_pat)))

    # 3/4/5) 指标
    if require_metrics:
        p = os.path.join(out_dir, "latest.json")
        d = _read_json(p, None)
        if not isinstance(d, dict):
            items.append(_item("metrics", False, "缺少 out/latest.json 或不是 JSON 对象"))
        else:
            pts = d.get("points")
            if not isinstance(pts, list) or not pts:
                items.append(_item("metrics", False, "latest.json 的 points 为空"))
            else:
                bad = [i for i, it in enumerate(pts)
                       if not isinstance(it, dict)
                       or not (it.get("metric_key") or it.get("key"))
                       or (it.get("value_num", it.get("value")) is None
                           and it.get("value_text") is None
                           and (it.get("quality") or "ok") == "ok")]
                if bad:
                    items.append(_item("metrics", False,
                                       "%d 条指标缺 key/值（下标 %s）" % (len(bad), bad[:5])))
                else:
                    ok_n = sum(1 for it in pts
                               if (it.get("quality") or "ok") == "ok"
                               and not _is_placeholder(it.get("value_text")))
                    ratio = ok_n / float(len(pts))
                    items.append(_item("quality", ratio >= min_ok_ratio,
                                       "ok %d/%d（%.0f%%，门槛 %.0f%%）"
                                       % (ok_n, len(pts), ratio * 100, min_ok_ratio * 100)))
                    # 闸门收紧（保守）：原先只比 min_ok_ratio，个别任务把它调到 0.3，
                    # 于是 70% 指标缺失也能判通过。这里再压一条覆盖率下限 —— 缺失过半
                    # 即失败、全缺失必然失败。只加这一条新判据，不改 quality 的语义、
                    # 不动默认参数与各 task.json 里的既有取值。
                    floor = max(COVERAGE_FLOOR, min_ok_ratio)
                    missing_n = len(pts) - ok_n
                    if ok_n == 0:
                        cov_detail = "关键指标全缺失：0/%d 可用" % len(pts)
                    else:
                        cov_detail = ("缺失 %d/%d 条指标（可用 %d，覆盖率 %.0f%%，下限 %.0f%%）"
                                      % (missing_n, len(pts), ok_n,
                                         ratio * 100, floor * 100))
                    items.append(_item("coverage", ratio >= floor, cov_detail))
                    asof = str(d.get("as_of") or "").strip()
                    if not asof:
                        asof = max((str(it.get("obs_date") or "") for it in pts), default="")
                    try:
                        d0 = datetime.strptime(asof[:10], "%Y-%m-%d").date()
                        age = (date.today() - d0).days
                        items.append(_item("fresh", age <= freshness_days,
                                           "as_of=%s，落后 %d 天（上限 %d）"
                                           % (asof, age, freshness_days)))
                    except ValueError:
                        items.append(_item("fresh", False, "as_of 缺失或不是 YYYY-MM-DD：%r" % asof))
    ok = all(it["ok"] for it in items if it["fatal"])
    return {"ok": ok, "items": items, "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True)
    ap.add_argument("--freshness-days", type=int, default=3)
    ap.add_argument("--min-ok-ratio", type=float, default=0.5)
    ap.add_argument("--json", action="store_true", help="只打印结果 JSON，不落盘")
    a = ap.parse_args(argv)
    tdir = os.path.join(CASE_DIR, a.task)
    task = _read_json(os.path.join(tdir, "task.json"), None)
    if not isinstance(task, dict):
        print("找不到 %s/task.json" % a.task)
        return 1
    task["_dir"] = tdir
    res = artifacts(task, None, freshness_days=a.freshness_days, min_ok_ratio=a.min_ok_ratio)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        _write_json(os.path.join(tdir, "out", "check.json"), res)
        for it in res["items"]:
            print("  [%s] %-8s %s" % ("OK  " if it["ok"] else "FAIL", it["name"], it["detail"]))
        print("检查%s" % ("通过" if res["ok"] else "不通过"))
    return 0 if res["ok"] else 3


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))