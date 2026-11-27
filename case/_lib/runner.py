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
import threading
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

# 共享代码库（report_stats / broad5_risk / portfolio_stats 这类多任务复用的模块）：
# 由框架统一注入 sys.path，任务不用各自拼绝对路径；私有仓库用 CASE_SHARED_DIR 指过来。
SHARED_DIR = os.environ.get("CASE_SHARED_DIR") or os.path.join(CASE_DIR, "_shared")
if os.path.isdir(SHARED_DIR) and SHARED_DIR not in sys.path:
    sys.path.insert(0, SHARED_DIR)
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
    """按 key 找任务：先 case/，再 CASE_EXTRA_DIRS 里的私有目录。"""
    dirs = [CASE_DIR]
    try:
        import case_registry as CR
        dirs = CR.task_dirs(None)
    except Exception:                                     # noqa: BLE001
        extra = (os.environ.get("CASE_EXTRA_DIRS") or "").replace(";", ":")
        dirs += [x.strip() for x in extra.split(":") if x.strip() and os.path.isdir(x.strip())]
    for base in dirs:
        tdir = os.path.join(base, key)
        t = _read_json(os.path.join(tdir, "task.json"), None)
        if isinstance(t, dict):
            t["_dir"] = tdir
            t["source"] = "case" if base == CASE_DIR else "case-external"
            return t
    return None


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
        # 最近一次子进程退出码：pipeline.py 的钩子普遍写成
        # `ctx.sh(..., check=False)` 且不接收返回值，失败会被吞成成功。
        # 这里记一份，run_stage 在钩子返回 None 时据此判定。
        self.last_rc = 0

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
        e.setdefault("CASE_SHARED_DIR", SHARED_DIR)
        e.update(env or {})
        self.log("$ %s" % cmd)
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.out_dir, exist_ok=True)
        t0 = time.time()
        p = subprocess.Popen(cmd, shell=True, executable="/bin/bash", cwd=self.dir, env=e,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, text=True, bufsize=1,
                             start_new_session=True)
        # 读 stdout 必须放独立线程：子进程只要不关 stdout（卡住 / 一直输出），
        # 主线程里 `for line in p.stdout` 就永远不返回，后面 p.wait(timeout) 根本没机会跑，
        # timeout 形同虚设。改成「线程转发输出 + 主线程 wait(timeout)」，
        # 超时在主线程里 killpg 整组（start_new_session=True，子进程的孙进程一起收）。
        def _pump(pipe) -> None:
            try:
                for line in pipe:
                    print("   " + line.rstrip("\n"), flush=True)
                    try:
                        self.logf.write("   " + line)
                    except (OSError, ValueError):
                        pass
            except (OSError, ValueError):
                pass

        assert p.stdout is not None
        reader = threading.Thread(target=_pump, args=(p.stdout,), daemon=True)
        reader.start()
        try:
            rc = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(p.pid), 9)
            except OSError:
                p.kill()
            rc = -1
            self.log("超时（%ss）已终止" % timeout)
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        reader.join(timeout=5)
        try:
            p.stdout.close()
        except (OSError, ValueError):
            pass
        self.log("退出码 %s，耗时 %.1fs" % (rc, time.time() - t0))
        self.last_rc = rc
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
        rec = alert_mod.send(self.key, title, lines, level, dry=self.dry, task_dir=self.dir)
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
    ctx.last_rc = 0
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
        # publish.py 返回非零时 emit 必须跟着失败：否则 runner 退出 0 → 调度器记 ok
        # → health 连败清零，落库失败被吞成成功。跳过落库（--no-publish / dry-run）
        # 时 out 里没有 rc 键，不算失败。
        prc = out.get("rc")
        ok = prc in (None, 0)
        return {"name": name, "ok": ok, "rc": int(prc or 0), "sec": round(time.time() - t0, 1),
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
        # 钩子返回 None 时按 last_rc 兜底：pipeline.py 普遍是
        # `ctx.sh(cmd, check=False)` 不接收返回值，不兜底的话子进程退出码 1
        # 会被当成通过 → 调度器记 ok → 连败清零 → 失败彻底静默。
        if ret is None and ctx.last_rc not in (0, None):
            raise RuntimeError("%s 阶段的命令退出码 %s（钩子未检查返回值）"
                               % (name, ctx.last_rc))
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


def _task_uses_feishu(task: Dict) -> bool:
    """任务目录下是否有脚本引用飞书推送入口。

    不靠 task.json 加字段：各 case 的推送方式不统一（feishu_config /
    feishu_creds / feishu_router 都有），扫源码比加一个容易忘填的开关可靠。
    """
    d = task.get("_dir") or ""
    try:
        names = os.listdir(d)
    except OSError:
        return False
    for n in names:
        if not n.endswith(".py"):
            continue
        try:
            with open(os.path.join(d, n), encoding="utf-8", errors="ignore") as f:
                src = f.read()
        except OSError:
            continue
        # 关键词放宽到 "feishu"：margin_detail 这类是 feishu_push -> feishu_config
        # 的间接引用，只认直接引用会漏；多校验一次凭据没有副作用。
        if "feishu" in src.lower():
            return True
    return False


def _preflight_feishu(task: Dict) -> Optional[str]:
    """推送类任务的凭据前置校验。返回错误描述，就绪返回 None。

    为什么前移到启动阶段：pmi_export 全链路要跑 8 分钟，凭据缺失时旧行为是
    跑完全部取数与渲染，最后在 pusher 阶段才炸——最贵的一种失败。
    """
    if not _task_uses_feishu(task):
        return None
    lib = os.path.dirname(os.path.abspath(__file__))
    if lib not in sys.path:
        sys.path.insert(0, lib)
    try:
        import feishu_creds
        feishu_creds.require("webhook_url")
    except Exception as e:                                 # noqa: BLE001
        return "飞书凭据未就绪：%s" % str(e)[:180]
    return None


def _check_depends(ctx: Ctx, task: Dict) -> List[str]:
    """检查 depends_on 的上游产物：存在、非空、够新、ok 占比达标。

    任一条不满足就返回问题列表，调用方据此跳过执行并报警——下游不拿过期数据硬算。
    """
    deps = task.get("depends_on") or []
    if not deps:
        return []
    try:
        sys.path.insert(0, HERE)
        import case_registry as CR
        ups = {t["key"]: t for t in CR.load_tasks(None)}
    except Exception as e:                                 # noqa: BLE001
        return ["无法读取任务注册表：%s" % e]
    pipe = task.get("pipeline") or {}
    fresh_days = int(pipe.get("freshness_days") or 3)
    min_ratio = float(pipe.get("min_ok_ratio", 0.5))
    bad: List[str] = []
    for key in deps:
        up = ups.get(key)
        if not up:
            bad.append("%s：上游任务不存在（检查 key 或 CASE_EXTRA_DIRS）" % key)
            continue
        d = _read_json(os.path.join(up["_dir"], "out", "latest.json"), None)
        pts = (d or {}).get("points") if isinstance(d, dict) else None
        if not isinstance(pts, list) or not pts:
            bad.append("%s：没有 out/latest.json 或 points 为空（上游还没跑）" % key)
            continue
        asof = str(d.get("as_of") or "")
        if not asof:
            asof = max((str((it or {}).get("obs_date") or "") for it in pts), default="")
        try:
            age = (datetime.now().date() - datetime.strptime(asof[:10], "%Y-%m-%d").date()).days
            if age > fresh_days:
                bad.append("%s：产物 as_of=%s，落后 %d 天（上限 %d）" % (key, asof, age, fresh_days))
        except ValueError:
            bad.append("%s：as_of 缺失或格式不对（%r）" % (key, asof))
        ok_n = sum(1 for it in pts if (isinstance(it, dict)
                                       and (it.get("quality") or "ok") == "ok"))
        ratio = ok_n / float(len(pts))
        if ratio < min_ratio:
            bad.append("%s：ok %d/%d（%.0f%%），低于门槛 %.0f%%"
                       % (key, ok_n, len(pts), ratio * 100, min_ratio * 100))
    if bad:
        ctx.warn("上游依赖不满足", bad, "error")
    return bad


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
    # import_py.py 只搬 .py，data/ 这类空目录不进仓库；任务里直接
    # os.listdir(data/xxx) 会 FileNotFoundError。跑前把常用工作目录先建好。
    for _sub in ("data", "out", "logs"):
        os.makedirs(os.path.join(task["_dir"], _sub), exist_ok=True)
    logp = os.path.join(run_dir, "run-%s.log" % run_id)
    logf = open(logp, "a", encoding="utf-8")
    started = time.time()

    ctx = Ctx(task, run_dir, logf, started, do_publish=not a.no_publish, dry=a.dry_run)
    ctx.log("任务 %s（%s）开始，阶段：%s" % (task["key"], task.get("section"), " → ".join(stages)))
    if os.path.isdir(SHARED_DIR):
        ctx.log("共享库 → %s" % SHARED_DIR)
    for req in task.get("requires") or []:
        if req == "pylibs":
            # 公共库软降级：设了且目录存在就前置进 sys.path；没设/不存在回退系统
            # site-packages。这里不硬报错——真缺包时 import 会自然抛
            # ModuleNotFoundError，比「请先设置 PYLIBS_DIR」更有指向性。
            d = (os.environ.get("PYLIBS_DIR") or "").strip()
            if not d:
                ctx.log("未设 PYLIBS_DIR，依赖走系统 site-packages")
            elif not os.path.isdir(d):
                ctx.log("PYLIBS_DIR=%s 不存在，回退系统 site-packages" % d)
            elif d not in sys.path:
                sys.path.insert(0, d)
                ctx.log("依赖 pylibs → %s" % d)
            else:
                ctx.log("依赖 pylibs → %s（已在 sys.path）" % d)

    # 依赖检查：上游产物不满足就不进入 fetch，也不发报告
    results: List[Dict] = []
    dep_problems = [] if a.dry_run else _check_depends(ctx, task)
    # 凭据前置校验：推送类任务先确认飞书凭据就绪，别跑完全流程才在推送阶段失败
    if not a.dry_run:
        _cred_problem = _preflight_feishu(task)
        if _cred_problem:
            dep_problems.append(_cred_problem)
        else:
            ctx.log("飞书凭据预检通过" if _task_uses_feishu(task) else "无需飞书凭据")
    if dep_problems:
        results.append({"name": "depends", "ok": False, "rc": 1, "sec": 0.0,
                        "detail": "；".join(dep_problems)[:200], "hard": True})
        ctx.log("依赖不满足，跳过全部阶段")
        stages = []

    mod = load_pipeline(task)
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

    # 防「空转」：声明了 pipeline 却没有任何阶段真正执行（既无 pipeline.py 钩子、
    # 也无 stages[].cmd），旧逻辑是五阶段全「未配置，跳过」并记 ok —— 任务空转还报成功。
    # 这里把它判成硬失败，宁可报错也不要静默通过（dry-run 只打印阶段，不参与）。
    ran_stage = any(r.get("name") in STAGES and not r.get("skipped") for r in results)
    pipeline_idle = bool(pipe) and not ran_stage and not a.dry_run
    if pipeline_idle:
        ctx.warn("流水线空转", ["pipeline 已声明，但没有任何阶段真正执行",
                                "要么补 pipeline.py 钩子 / stages[].cmd，"
                                "要么去掉 pipeline 块走 legacy 直跑"], "error")

    # 没有任何指标产出也算"面板不会有东西"，但不能覆盖其它失败判定
    hard_fail = any((not r.get("ok")) and not r.get("soft") and not r.get("skipped")
                    for r in results) or bool(final_check and not final_check["ok"]) \
        or pipeline_idle
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