#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runner.py — case 任务的统一执行入口（框架适配层）

任务不再是一条命令"自己跑完自己发"，而是由框架按固定生命周期驱动：

    fetch     取数        → 产物落 data/
    check     检查        → 结果落 out/check.json，不通过就报警
    render    出报告      → 产物落 out/（报告文件）
    emit      出指标      → 写模板路径 out/latest.json，再落 DataHub 指标仓
    dispatch  分发        → 调任务自己的推送（报告 / 邮件），框架不代发

为什么这样改：面板只读指标仓，所以"数据从哪来、什么时候算、算不出来怎么报警、报告落在哪"
必须由框架统一管；任务只保留自己的业务逻辑（怎么取、怎么算、怎么发），挂到对应阶段上。

两种接法，按任务情况选：

  A. 阶段命令（task.json 里声明，适合原脚本已经能用参数分段的任务）

     "pipeline": {
       "freshness_days": 3, "min_ok_ratio": 0.5, "on_fail": "stop",
       "artifacts": {"data": ["data/*.json"], "report": ["out/report.html"]},
       "stages": [
         {"name": "fetch",    "cmd": "python3 -u fetch.py"},
         {"name": "check",    "builtin": "artifacts"},
         {"name": "render",   "cmd": "python3 -u render.py"},
         {"name": "emit",     "builtin": "emit"},
         {"name": "dispatch", "cmd": "python3 -u dispatch.py", "optional": true}
       ]
     }

  B. 进程内钩子（任务目录放 pipeline.py，需要共享内存里的数据时用）

     def fetch(ctx):    ctx.sh("python3 -u run_daily.py --no-push")   # 或自己写
     def check(ctx):    return checks.artifacts(ctx.task, ctx.started)  # 或自定义
     def render(ctx):   ...
     def emit(ctx):     ctx.emit([{...}, ...])                          # 或返回 list
     def dispatch(ctx): ctx.sh("python3 -u run_daily.py --send")

  同名阶段优先用 pipeline.py 的钩子，没有钩子再看 stages 里的声明。

运行：

    python3 case/_lib/runner.py --task tech_consensus
    python3 case/_lib/runner.py --task tech_consensus --only fetch,check
    python3 case/_lib/runner.py --task tech_consensus --no-publish
    python3 case/_lib/runner.py --task tech_consensus --dry-run     # 只打印阶段，不执行

退出码：0 全通过 / 1 阶段失败 / 3 检查不通过。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from datetime import datetime
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
CASE_DIR = os.path.dirname(HERE)
ROOT = os.path.dirname(CASE_DIR)
STAGES = ("fetch", "check", "render", "emit", "dispatch")
STAGE_CN = {"fetch": "取数", "check": "检查", "render": "出报告",
            "emit": "出指标", "dispatch": "分发"}

if HERE not in sys.path:
    sys.path.insert(0, HERE)
import alert as alert_mod                                          # noqa: E402
import checks as check_mod                                          # noqa: E402


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


def load_task(key: str) -> Optional[Dict]:
    tdir = os.path.join(CASE_DIR, key)
    t = _read_json(os.path.join(tdir, "task.json"), None)
    if not isinstance(t, dict):
        return None
    t["_dir"] = tdir
    return t


def load_pipeline(task: Dict):
    """任务目录下的 pipeline.py（可选）。导入前把它所在目录放进 sys.path。"""
    p = os.path.join(task["_dir"], "pipeline.py")
    if not os.path.isfile(p):
        return None
    d = task["_dir"]
    if d not in sys.path:
        sys.path.insert(0, d)
    spec = importlib.util.spec_from_file_location("case_pipeline_%s" % task["key"], p)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)                      # noqa: PLW0127
        return mod
    except Exception:                                     # noqa: BLE001
        print("[runner] 导入 pipeline.py 失败：\n%s" % traceback.format_exc())
        return None


class Ctx:
    """阶段上下文：路径、日志、子进程、指标收集，都从这里走。"""

    def __init__(self, task: Dict, run_dir: str, logf, started: float,
                 do_publish: bool = True, dry: bool = False):
        self.task = task
        self.key = task["key"]
        self.dir = task["_dir"]
        self.data_dir = os.path.join(self.dir, "data")
        self.out_dir = os.path.join(self.dir, "out")
        self.run_dir = run_dir
        self.logf = logf
        self.started = started
        self.do_publish = do_publish
        self.dry = dry
        self.alerts: List[Dict] = []
        self.points: List[Dict] = []
        self.stage = ""
        self.done: set = set()

    # ── 日志 ──
    def log(self, msg: str) -> None:
        line = "[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg)
        print(line, flush=True)
        try:
            self.logf.write(line + "\n")
            self.logf.flush()
        except (OSError, ValueError):
            pass

    # ── 子进程 ──
    def sh(self, cmd: str, timeout: Optional[int] = None, env: Optional[Dict] = None,
           check: bool = True) -> int:
        if self.dry:
            self.log("dry-run 不执行：%s" % cmd)
            return 0
        e = os.environ.copy()
        e.setdefault("CASE_TASK", self.key)
        e.setdefault("CASE_DATA_DIR", self.data_dir)
        e.setdefault("CASE_OUT_DIR", self.out_dir)
        e.update(env or {})
        self.log("$ %s" % cmd)
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.out_dir, exist_ok=True)
        t0 = time.time()
        p = subprocess.Popen(cmd, shell=True, executable="/bin/bash", cwd=self.dir, env=e,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, text=True, bufsize=1,
                             start_new_session=True)
        try:
            assert p.stdout is not None
            for line in p.stdout:
                print("   " + line.rstrip("\n"), flush=True)
                try:
                    self.logf.write("   " + line)
                except (OSError, ValueError):
                    pass
            rc = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(p.pid), 9)
            except OSError:
                p.kill()
            rc = -1
            self.log("超时（%ss）已终止" % timeout)
        self.log("退出码 %s，耗时 %.1fs" % (rc, time.time() - t0))
        if check and rc != 0:
            raise RuntimeError("命令退出码 %s：%s" % (rc, cmd))
        return rc

    # ── 指标 ──
    def emit(self, points: Any) -> None:
        if isinstance(points, dict):
            points = [points]
        for p in points or []:
            if isinstance(p, dict):
                self.points.append(p)

    def save_json(self, name: str, obj: Any) -> str:
        p = os.path.join(self.out_dir, name)
        _write_json(p, obj)
        return p

    def load_json(self, name: str, default=None):
        return _read_json(os.path.join(self.data_dir, name), default)

    def warn(self, title: str, lines: List[str], level: str = "warn") -> Dict:
        rec = alert_mod.send(self.key, title, lines, level, dry=self.dry)
        self.alerts.append(rec)
        return rec


def _stage_spec(task: Dict, name: str) -> Dict:
    pipe = task.get("pipeline") or {}
    for st in pipe.get("stages") or []:
        if isinstance(st, dict) and st.get("name") == name:
            return st
    return {}


def _write_metrics(ctx: Ctx, points: List[Dict], as_of: str = "") -> Dict:
    """写模板路径 out/latest.json 并落 DataHub（调 case/_lib/publish.py）。"""
    if not points:
        ctx.log("没有指标可写，跳过 emit")
        return {"written": 0, "published": False}
    obs = as_of or max((str(p.get("obs_date") or "") for p in points), default="")
    payload = {"as_of": obs, "task": ctx.key, "points": points}
    p = os.path.join(ctx.out_dir, "latest.json")
    _write_json(p, payload)
    ctx.log("已写 %s：%d 条" % (os.path.relpath(p, ctx.dir), len(points)))
    res = {"written": len(points), "path": p, "published": False}
    if ctx.dry or not ctx.do_publish or os.environ.get("CASE_NO_PUBLISH") in ("1", "true", "yes"):
        ctx.log("跳过落库（--no-publish / CASE_NO_PUBLISH / dry-run）")
        return res
    pub = os.path.join(HERE, "publish.py")
    rc = ctx.sh('%s %s --task %s' % (_q(sys.executable), _q(pub), _q(ctx.key)), check=False)
    res["rc"] = rc
    res["published"] = rc == 0
    if rc != 0:
        ctx.warn("指标落库失败", ["publish.py 退出码 %s" % rc,
                                  "latest.json 已写：%s" % p,
                                  "指标仓不可用时任务照常出报告，但面板不会更新"], "error")
    return res


def _q(s: str) -> str:
    return '"%s"' % str(s).replace('"', '\\"')


def run_stage(name: str, ctx: Ctx, hook, spec: Dict, verify: bool) -> Dict:
    """跑一个阶段，返回 {name, ok, rc, sec, detail}。异常向外抛，由 main 统一处理。"""
    t0 = time.time()
    ctx.stage = name
    ctx.log("▶ 阶段 %s（%s）" % (name, STAGE_CN.get(name, name)))
    detail = ""
    rc = 0

    if name == "check":
        res = None
        if callable(hook):
            res = hook(ctx)
        if res is None:
            pipe = ctx.task.get("pipeline") or {}
            # 只校验"已经跑过的阶段"的产物：check 排在 render 前时不去查报告/指标，
            # 报告与指标的完整性由末尾的收尾校验负责。
            res = check_mod.artifacts(
                ctx.task, ctx.started,
                freshness_days=int(pipe.get("freshness_days") or 3),
                min_ok_ratio=float(pipe.get("min_ok_ratio", 0.5)),
                require_data=pipe.get("require_data", True),
                require_report=pipe.get("require_report", True) and ("render" in ctx.done),
                require_metrics=pipe.get("require_metrics", True) and ("emit" in ctx.done))
        if isinstance(res, bool):
            res = {"ok": res, "items": []}
        if ctx.dry:
            res = {"ok": True, "items": [], "dry": True}
        ctx.save_json("check.json", res)
        for it in res.get("items") or []:
            ctx.log("  [%s] %-8s %s" % ("OK  " if it.get("ok") else "FAIL",
                                        it.get("name"), it.get("detail") or ""))
        ok = bool(res.get("ok"))
        detail = "检查%s" % ("通过" if ok else "不通过")
        if not ok:
            failed = ["%s：%s" % (it.get("name"), it.get("detail"))
                      for it in (res.get("items") or []) if not it.get("ok")]
            ctx.warn("数据检查不通过", failed, "error")
            rc = 3 if not verify else 0
        return {"name": name, "ok": ok, "rc": rc, "sec": round(time.time() - t0, 1),
                "detail": detail, "hard": not ok}

    if name == "emit":
        if ctx.dry:
            ctx.log("  dry-run 不执行 emit")
            return {"name": name, "ok": True, "rc": 0, "sec": 0.0, "detail": "dry-run 跳过",
                    "skipped": True}
        points = None
        if callable(hook):
            points = hook(ctx)
        if isinstance(points, dict):
            points = [points]
        merged = list(points or []) + ctx.points
        builtin = spec.get("builtin") or "emit"
        as_of = spec.get("as_of") or ""
        out = _write_metrics(ctx, merged, as_of) if builtin == "emit" else {}
        detail = "写入 %d 条指标" % len(merged)
        if not merged:
            ctx.warn("没有产出任何指标", ["emit 阶段拿到 0 条 points",
                                          "检查任务是否把结果交回 ctx.emit / 返回 list"], "warn")
        return {"name": name, "ok": True, "rc": 0, "sec": round(time.time() - t0, 1),
                "detail": detail, "result": out}

    # fetch / render / dispatch：钩子优先，其次 stages 里的 cmd
    if ctx.dry and callable(hook):
        ctx.log("  dry-run 不执行 %s 钩子" % name)
        return {"name": name, "ok": True, "rc": 0, "sec": 0.0, "detail": "dry-run 跳过",
                "skipped": True}
    if callable(hook):
        ret = hook(ctx)
        if isinstance(ret, int) and ret != 0:
            raise RuntimeError("%s 钩子返回 %s" % (name, ret))
        detail = "钩子执行完成"
    elif spec.get("cmd"):
        rc = ctx.sh(str(spec["cmd"]), timeout=int(spec.get("timeout") or 0) or None,
                    check=False)
        detail = "命令退出码 %s" % rc
        if rc != 0:
            optional = bool(spec.get("optional"))
            if optional:
                ctx.warn("%s 阶段失败（允许继续）" % STAGE_CN.get(name, name),
                         ["命令：%s" % spec["cmd"], "退出码：%s" % rc], "warn")
                return {"name": name, "ok": False, "rc": rc,
                        "sec": round(time.time() - t0, 1), "detail": detail, "soft": True}
            raise RuntimeError("%s 阶段失败（退出码 %s）：%s" % (name, rc, spec["cmd"]))
    else:
        if spec.get("hook"):
            raise RuntimeError("%s 阶段声明了 hook，但 pipeline.py 里没有 %s()" % (name, name))
        ctx.log("  未配置 %s 阶段，跳过" % name)
        return {"name": name, "ok": True, "rc": 0, "sec": 0.0, "detail": "未配置，跳过",
                "skipped": True}

    return {"name": name, "ok": True, "rc": rc, "sec": round(time.time() - t0, 1),
            "detail": detail}


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True)
    ap.add_argument("--only", default="", help="只跑这些阶段，逗号分隔")
    ap.add_argument("--from", dest="from_stage", default="", help="从某阶段开始")
    ap.add_argument("--no-publish", action="store_true", help="emit 只写 latest.json，不落库")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify", action="store_true",
                    help="检查不通过只报警、不中断（人工排障用）")
    ap.add_argument("--freshness-days", type=int, default=None)
    a = ap.parse_args(argv)

    task = load_task(a.task)
    if task is None:
        print("找不到 case/%s/task.json" % a.task)
        return 1
    if a.freshness_days is not None:
        task.setdefault("pipeline", {})["freshness_days"] = a.freshness_days

    pipe = task.get("pipeline") or {}
    on_fail = str(pipe.get("on_fail") or "stop")
    stages = [s for s in STAGES if s in (pipe.get("stages") and
                                         [x.get("name") for x in pipe["stages"]] or STAGES)]
    if pipe.get("stages"):
        stages = [x.get("name") for x in pipe["stages"] if isinstance(x, dict)]
    if a.only:
        want = [s.strip() for s in a.only.split(",") if s.strip()]
        stages = [s for s in stages if s in want] or want
    if a.from_stage and a.from_stage in stages:
        stages = stages[stages.index(a.from_stage):]

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = os.path.join(task["_dir"], "logs")
    os.makedirs(run_dir, exist_ok=True)
    logp = os.path.join(run_dir, "run-%s.log" % run_id)
    logf = open(logp, "a", encoding="utf-8")
    started = time.time()

    ctx = Ctx(task, run_dir, logf, started, do_publish=not a.no_publish, dry=a.dry_run)
    ctx.log("任务 %s（%s）开始，阶段：%s" % (task["key"], task.get("section"), " → ".join(stages)))
    for req in task.get("requires") or []:
        if req == "pylibs" and os.environ.get("PYLIBS_DIR"):
            d = os.environ["PYLIBS_DIR"]
            if d not in sys.path:
                sys.path.insert(0, d)
            ctx.log("依赖 pylibs → %s" % d)

    mod = load_pipeline(task)
    results: List[Dict] = []
    failed = False
    for name in stages:
        hook = getattr(mod, name, None) if mod is not None else None
        spec = _stage_spec(task, name)
        if name == "check" and a.freshness_days is not None:
            pass
        try:
            r = run_stage(name, ctx, hook if callable(hook) else None, spec, a.verify)
        except Exception as e:                             # noqa: BLE001
            tb = traceback.format_exc()
            ctx.log("✗ 阶段 %s 失败：%s" % (name, e))
            try:
                logf.write(tb)
            except (OSError, ValueError):
                pass
            ctx.warn("%s 阶段失败" % STAGE_CN.get(name, name), [str(e), tb.splitlines()[-3]])
            r = {"name": name, "ok": False, "rc": 1, "detail": str(e)[:200], "hard": True}
            results.append(r)
            failed = True
            if on_fail != "continue":
                break
            continue
        results.append(r)
        if r.get("ok") and not r.get("skipped"):
            ctx.done.add(name)
        if r.get("hard") and not r.get("ok"):
            failed = True
            if on_fail != "continue":
                break

    # 收尾校验：报告与指标是面板与投递的前提，跑完再核一次（check 阶段排在它们之前时靠这里兜底）
    final_check = None
    if ("render" in ctx.done or "emit" in ctx.done) and not a.dry_run:
        pipe2 = task.get("pipeline") or {}
        final_check = check_mod.artifacts(
            task, started,
            freshness_days=int(pipe2.get("freshness_days") or 3),
            min_ok_ratio=float(pipe2.get("min_ok_ratio", 0.5)),
            require_data=pipe2.get("require_data", True),
            require_report=bool(pipe2.get("require_report", True) and "render" in ctx.done),
            require_metrics=bool(pipe2.get("require_metrics", True) and "emit" in ctx.done))
        ctx.save_json("check.json", final_check)
        for it in final_check.get("items") or []:
            ctx.log("  [%s] %-8s %s" % ("OK  " if it.get("ok") else "FAIL",
                                        it.get("name"), it.get("detail") or ""))
        if not final_check["ok"]:
            ctx.warn("产物收尾校验不通过",
                     ["%s：%s" % (it.get("name"), it.get("detail"))
                      for it in final_check["items"] if not it["ok"]], "error")

    # 没有任何指标产出也算"面板不会有东西"，但不能覆盖其它失败判定
    hard_fail = any((not r.get("ok")) and not r.get("soft") and not r.get("skipped")
                    for r in results) or bool(final_check and not final_check["ok"])
    check_fail = any(r.get("name") == "check" and not r.get("ok") for r in results) \
        or bool(final_check and not final_check["ok"])
    status = {
        "task": task["key"],
        "run_id": run_id,
        "started": datetime.fromtimestamp(started).strftime("%Y-%m-%d %H:%M:%S"),
        "finished": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "duration": round(time.time() - started, 1),
        "ok": not hard_fail,
        "stages": results,
        "alerts": ctx.alerts,
        "log": os.path.relpath(logp, ROOT),
    }
    ctx.save_json("status.json", status)
    ctx.log("完成：%s（%.1fs），状态见 out/status.json"
            % ("通过" if status["ok"] else "失败", status["duration"]))
    logf.close()

    if not status["ok"]:
        return 3 if check_fail else 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))