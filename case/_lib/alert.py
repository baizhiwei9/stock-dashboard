#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""alert.py — case 任务的统一报警出口

框架在三种情况下报警，全部走这里，保证「报警」这件事只有一种写法：

  · 取数失败 / 数据为空
  · 检查不通过（缺产物、数据过期、指标算不出来）
  · 阶段命令非零退出

落盘（都在任务目录的 out/ 下，属于产物，不入库，**永远全量记录**）：

    out/alerts.json                    最近 100 条报警（追加）
    out/alerts/<ts>-<level>.md         单条报警的可读副本

外发（可选）：只有 CASE_ALERT_WEBHOOK 给了 http(s) 地址才发，并且**有频控**，
不会把一轮里的几十条失败全变成几十封消息：

  · 同一个（任务, 标题）**每天最多外发 1 次**          → 状态记 out/.alert_state.json
  · 同一进程内同一个标题只外发 1 次
  · 全机每小时外发总量上限 CASE_ALERT_MAX_PER_HOUR（默认 6）
  · 配置 / 加载类错误（key 与目录名、task.json 非法等）**默认不외发**，
    需要发请显式设 CASE_ALERT_ON_CONFIG=1
  · CASE_ALERT_MUTE=1 一键静默（只落盘）

被抑制的条目仍会落盘，并在记录里带 "suppressed" 字段说明原因，便于事后核对。

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

# ---- 外发频控开关（全部可用环境变量覆盖）----
MUTE = (os.environ.get("CASE_ALERT_MUTE") or "").strip().lower() in ("1", "true", "yes", "on")
ON_CONFIG = (os.environ.get("CASE_ALERT_ON_CONFIG") or "").strip().lower() in (
    "1", "true", "yes", "on")
try:
    MAX_PER_HOUR = max(0, int(os.environ.get("CASE_ALERT_MAX_PER_HOUR") or 6))
except ValueError:
    MAX_PER_HOUR = 6

QUOTA_PATH = os.path.join(HERE, ".alert_quota.json")
_SEEN_TITLES = set()          # 进程内去重


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


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _is_config_err(title: str) -> bool:
    """配置 / 加载类错误：不该当运行失败刷出去。"""
    t = str(title or "")
    return ("配置" in t) or ("key 与目录名" in t) or ("task.json" in t)


def _state_path(d: Dict[str, str]) -> str:
    return os.path.join(d["out"], ".alert_state.json")


def _already_sent_today(d: Dict[str, str], title: str) -> bool:
    st = _read_json(_state_path(d), {})
    if not isinstance(st, dict) or st.get("date") != _today():
        return False
    sent = st.get("sent")
    return isinstance(sent, dict) and str(title) in sent


def _mark_sent(d: Dict[str, str], title: str) -> None:
    st = _read_json(_state_path(d), {})
    if not isinstance(st, dict) or st.get("date") != _today():
        st = {"date": _today(), "sent": {}}
    sent = st.get("sent")
    if not isinstance(sent, dict):
        sent = {}
    sent[str(title)] = datetime.now().strftime("%H:%M:%S")
    st["sent"] = sent
    _write_json(_state_path(d), st)


def _quota_take() -> bool:
    """全机每小时外发配额。返回 True 表示还允许发。"""
    hour = datetime.now().strftime("%Y-%m-%d %H")
    q = _read_json(QUOTA_PATH, {})
    if not isinstance(q, dict) or q.get("hour") != hour:
        q = {"hour": hour, "n": 0, "dropped": 0}
    if MAX_PER_HOUR and int(q.get("n") or 0) >= MAX_PER_HOUR:
        q["dropped"] = int(q.get("dropped") or 0) + 1
        _write_json(QUOTA_PATH, q)
        return False
    q["n"] = int(q.get("n") or 0) + 1
    _write_json(QUOTA_PATH, q)
    return True


def _post_target() -> str:
    url = (os.environ.get("CASE_ALERT_WEBHOOK") or "").strip()
    return url if url.startswith(("http://", "https://")) else ""


def _external_gate(key: str, title: str, level: str, d: Dict[str, str]):
    """决定这条要不要外发。返回 (ok, reason)。reason 为空表示外发。"""
    if MUTE:
        return False, "mute"
    if not _post_target():
        return False, ""
    if level == "info":
        return False, "info-no-push"          # info 只落盘
    if _is_config_err(title) and not ON_CONFIG:
        return False, "config-error"
    if str(title) in _SEEN_TITLES:
        return False, "dup-in-run"
    if _already_sent_today(d, title):
        return False, "dup-today"
    if not _quota_take():
        return False, "quota-exceeded"
    return True, ""


def _post_webhook(rec: Dict) -> Optional[str]:
    url = _post_target()
    if not url:
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
    """发一条报警。返回落盘记录（含 level/title/lines/ts；外发被抑制时带 suppressed）。"""
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
    os.makedirs(d["out"], exist_ok=True)

    ok, why = _external_gate(key, title, level, d)
    wh = None
    if ok:
        wh = _post_webhook(rec)
        _SEEN_TITLES.add(str(title))
        if wh and not str(wh).startswith("webhook 失败"):
            _mark_sent(d, title)
        rec["webhook"] = wh
    elif why:
        rec["suppressed"] = why

    hist = _read_json(d["json"], [])
    if not isinstance(hist, list):
        hist = []
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
        if rec.get("suppressed"):
            f.write("\n外发已抑制：%s\n" % rec["suppressed"])

    tail = "（外发已抑制：%s）" % rec["suppressed"] if rec.get("suppressed") else ""
    print("[ALERT][%s] %s%s" % (level, title, tail))
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