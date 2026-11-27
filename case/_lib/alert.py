#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""alert.py — case 任务的统一报警出口

框架在三种情况下报警，全部走这里，保证「报警」这件事只有一种写法：

  · 取数失败 / 数据为空
  · 检查不通过（缺产物、数据过期、指标算不出来）
  · 阶段命令非零退出

落点（都在任务目录的 out/ 下，属于产物，不入库）：

    out/alerts.json                    最近 100 条报警（追加）
    out/alerts/<ts>-<level>.md         单条报警的可读副本

可选外发：环境变量 CASE_ALERT_WEBHOOK 给了 http(s) 地址就以 JSON POST 出去
（飞书/钉钉/企业微信的机器人地址都收这个格式）。没配就只落盘，不影响任务。

等级：info / warn / error。error 会同时让本次运行判为失败（由 runner 决定）。

命令行自检：
    python3 alert.py --task <key> --level warn --title "测试" --line "第一行"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime
from typing import Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
CASE_DIR = os.path.dirname(HERE)
KEEP = 100
LEVELS = ("info", "warn", "error")


def _dirs(key: str, task_dir: Optional[str] = None) -> Dict[str, str]:
    base = task_dir or os.path.join(CASE_DIR, key)
    out = os.path.join(base, "out")
    return {"base": base, "out": out, "log": os.path.join(out, "alerts"),
            "json": os.path.join(out, "alerts.json")}


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


def _post_webhook(rec: Dict) -> Optional[str]:
    url = (os.environ.get("CASE_ALERT_WEBHOOK") or "").strip()
    if not url.startswith(("http://", "https://")):
        try:                       # 按应用绑定（apps.stock-dashboard 的群机器人 webhook）
            import notify_init
            notify_init.init()
            url = (notify_init.webhook("") or "").strip()
        except Exception:
            url = ""
    if not url.startswith(("http://", "https://")):
        return None
    text = "[%s] %s\n%s" % (rec["level"], rec["title"], "\n".join(rec["lines"]))
    body = json.dumps({"msg_type": "text", "content": {"text": text}},
                      ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return "HTTP %s" % r.status
    except Exception as e:                                   # noqa: BLE001
        return "webhook 失败：%s" % repr(e)[:120]


def send(key: str, title: str, lines: List[str], level: str = "warn",
         dry: bool = False, task_dir: Optional[str] = None) -> Dict:
    """发一条报警。返回落盘记录（含 level/title/lines/ts）。"""
    level = level if level in LEVELS else "warn"
    lines = [str(x) for x in (lines or []) if str(x).strip()]
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rec = {"ts": ts, "level": level, "title": str(title), "lines": lines}
    if dry:
        print("[ALERT][dry] %s %s" % (level, title))
        for ln in lines:
            print("        " + ln)
        return rec

    d = _dirs(key, task_dir)
    if not os.path.isdir(d["base"]):
        os.makedirs(d["base"], exist_ok=True)

    hist = _read_json(d["json"], [])
    if not isinstance(hist, list):
        hist = []
    wh = _post_webhook(rec)
    if wh:
        rec["webhook"] = wh
    hist.append(rec)
    _write_json(d["json"], hist[-KEEP:])

    os.makedirs(d["log"], exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    md = os.path.join(d["log"], "%s-%s.md" % (stamp, level))
    with open(md, "w", encoding="utf-8") as f:
        f.write("# [%s] %s\n\n- 时间：%s\n- 任务：%s\n\n" % (level, title, ts, key))
        for ln in lines:
            f.write("- %s\n" % ln)
        if wh:
            f.write("\n外发：%s\n" % wh)

    print("[ALERT][%s] %s" % (level, title))
    for ln in lines[:10]:
        print("        " + ln)
    return rec


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True)
    ap.add_argument("--level", default="warn", choices=list(LEVELS))
    ap.add_argument("--title", required=True)
    ap.add_argument("--line", action="append", default=[])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    send(a.task, a.title, a.line, a.level, a.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))