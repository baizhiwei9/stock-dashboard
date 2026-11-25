#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_pipeline.py — 给 case 任务批量生成框架适配的 pipeline.py

两种接法：

  A. 原入口已经能用参数分离"出稿"和"发送"（--no-push / --push / --send）
     → fetch 跑不发的那条命令，dispatch 跑发的那条，检查夹在中间

  B. 原入口一体式（没有发送开关）
     → fetch 跑完整命令（取数 + 出报告 + 自带发送），emit 写指标，
       检查放在后面复盘；dispatch 只记录"发送已内嵌"

emit 统一用 case/_lib/extract.py 的 auto_extract：扫 out/*.json 的标量字段，
让任务先有指标上面板；后续哪个任务想精细化，直接改它自己的 pipeline.py。

用法：
    python3 case/_tools/make_pipeline.py --dry-run
    python3 case/_tools/make_pipeline.py
    python3 case/_tools/make_pipeline.py --only rmt_daily
    python3 case/_tools/make_pipeline.py --force      # 覆盖已有 pipeline.py
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CASE_DIR = os.path.dirname(HERE)
QUIET_FLAGS = ("--no-push", "--no-feishu", "--no-email", "--no-send",
               "--no-share", "--no-alert", "--no-stocks")
SEND_FLAGS = ("--push", "--send", "--mail")
HANDMADE = {"tech_consensus", "tda_spd", "realestate_daily"}
SEC_OF = {"stock": "CN.STOCK", "macro": "CN.MACRO", "bond": "CN.BOND",
          "liquidity": "CN.LIQUIDITY", "futures": "CN.FUTURES",
          "realestate": "CN.REALESTATE", "option": "CN.OPTION",
          "risk": "CN.RISK", "modules": "CN.GENERIC"}

TM = '''# -*- coding: utf-8 -*-
"""%(key)s 的框架适配（自动生成，可直接改）

由 case/_tools/make_pipeline.py 按入口参数生成：
  · %(mode_desc)s
  · 指标用 case/_lib/extract.py 扫 out/*.json 自动抽取，需要更细就改下面的 emit()

阶段：fetch → check → emit → dispatch（顺序见 task.json 的 pipeline）
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.normpath(os.path.join(HERE, "..", "_lib"))
for _p in (HERE, LIB):
    if _p not in sys.path:
        sys.path.insert(0, _p)

CMD_FETCH = %(cmd_fetch)r
CMD_SEND = %(cmd_send)r
SEC = %(sec)r
TIMEOUT = %(timeout)d


def fetch(ctx):
    %(fetch_body)s


def emit(ctx):
    from extract import auto_extract
    points = auto_extract(ctx, sec=SEC)
    if not points:
        ctx.warn("没有抽到可展示的指标", [
            "out/ 下没有可解析的 json 产物",
            "任务还在跑的话，面板这一格会是空的"], "warn")
    return points


def dispatch(ctx):
    %(dispatch_body)s
'''


def _args_of(path: str):
    if not os.path.isfile(path):
        return []
    s = open(path, encoding="utf-8", errors="replace").read()
    return re.findall(r"add_argument\(\s*[\'\"]([^\'\"]+)[\'\"]", s)


def _fresh(sched: dict) -> int:
    mode = (sched or {}).get("mode")
    return 40 if mode == "monthly" else 10 if mode == "weekly" else 5


def build(key: str, apply: bool, force: bool) -> dict:
    tdir = os.path.join(CASE_DIR, key)
    tj = os.path.join(tdir, "task.json")
    task = json.load(open(tj, encoding="utf-8"))
    cmd = str(task.get("cmd") or "").strip()
    entry = str(task.get("entry") or "")
    args = _args_of(os.path.join(tdir, entry)) if entry else []

    quiet = [f for f in QUIET_FLAGS if f in args]
    send = [f for f in SEND_FLAGS if f in args and f not in quiet]

    if quiet and cmd:
        mode = "A"
        cmd_fetch = cmd + " " + " ".join(quiet)
        cmd_send = cmd
        mode_desc = "取数阶段不带 %s，发送单独放 dispatch" % "/".join(quiet)
        fetch_body = "ctx.sh(CMD_FETCH, timeout=TIMEOUT, check=True)"
        dispatch_body = 'ctx.sh(CMD_SEND, timeout=TIMEOUT, check=False)'
    elif send and cmd:
        mode = "A"
        cmd_fetch = cmd
        cmd_send = cmd + " " + send[0]
        mode_desc = "取数阶段跑原命令（默认不发），dispatch 加 %s 发送" % send[0]
        fetch_body = "ctx.sh(CMD_FETCH, timeout=TIMEOUT, check=True)"
        dispatch_body = "ctx.sh(CMD_SEND, timeout=TIMEOUT, check=False)"
    else:
        mode = "B"
        cmd_fetch = cmd
        cmd_send = ""
        mode_desc = ("原入口一体式（没有发送开关）：取数阶段一次跑完取数+出报告+自带发送，"
                     "emit 之后只做产物复盘")
        fetch_body = "ctx.sh(CMD_FETCH, timeout=TIMEOUT, check=False)"
        dispatch_body = ('ctx.log("这个任务的发送内嵌在取数阶段（原入口无 --no-push 开关），'
                         'dispatch 不重复发送")')

    pipe = {
        "on_fail": "continue",
        "freshness_days": _fresh(task.get("sched")),
        "min_ok_ratio": 0.3,
        "require_data": False,
        "require_report": True,
        "require_metrics": True,
        "artifacts": {"report": ["out/*.html", "out/*.md", "out/*.png", "out/*.json"]},
        "stages": [
            {"name": "fetch", "hook": True, "timeout": int(task.get("timeout") or 1800)},
            {"name": "check", "builtin": "artifacts"},
            {"name": "emit", "hook": True, "builtin": "emit"},
            {"name": "dispatch", "hook": True, "timeout": int(task.get("timeout") or 1800),
             "optional": True},
        ],
    }

    src = TM % {"key": key, "mode_desc": mode_desc, "cmd_fetch": cmd_fetch,
                "cmd_send": cmd_send, "sec": SEC_OF.get(task.get("section"), "CN.GENERIC"),
                "timeout": int(task.get("timeout") or 1800),
                "fetch_body": fetch_body, "dispatch_body": dispatch_body}

    # 有 pipeline.py 且不是 --force 时不覆盖（手工改过的保留）
    p = os.path.join(tdir, "pipeline.py")
    wrote_src = False
    if force or not os.path.isfile(p):
        wrote_src = True

    if apply:
        if wrote_src:
            with open(p, "w", encoding="utf-8") as f:
                f.write(src)
        task["pipeline"] = pipe
        task.setdefault("legacy_cmd", cmd)
        with open(tj, "w", encoding="utf-8") as f:
            json.dump(task, f, ensure_ascii=False, indent=2)
            f.write("\n")
    return {"key": key, "mode": mode, "wrote_src": wrote_src,
            "quiet": quiet, "send": send, "cmd_fetch": cmd_fetch}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--include-handmade", action="store_true")
    a = ap.parse_args(argv)

    keys = [a.only] if a.only else sorted(
        n for n in os.listdir(CASE_DIR)
        if not n.startswith(("_", ".")) and
        os.path.isfile(os.path.join(CASE_DIR, n, "task.json")))
    rows = []
    for k in keys:
        if k in HANDMADE and not a.include_handmade and not a.only:
            continue
        try:
            rows.append(build(k, not a.dry_run, a.force))
        except Exception as e:                                # noqa: BLE001
            print("%-24s 生成失败：%s" % (k, e))

    for r in rows:
        print("%-24s %s  %s" % (r["key"], r["mode"],
                                "写 pipeline.py" if r["wrote_src"] else "只更新 task.json"))
    print("\n共 %d 个任务；A 类 %d（出稿/发送分离），B 类 %d（一体式）"
          % (len(rows), sum(1 for r in rows if r["mode"] == "A"),
             sum(1 for r in rows if r["mode"] == "B")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))