#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""alert 级结论邮件的「至少有一个发送方」静态守卫（不连网、不读库）。

2026-11-27 的事故模式：`task_tags_notify` 里删掉统一邮件后，PR 声称
「各适配器会用 data_hub 的 notify_now 逐任务发」，但 `notify_now`
在本仓和 DataHub 的所有分支/提交里都不存在，`data_hub.tech.tasks.*`
也不发通知 —— 结果 alert 级结论的邮件通道静默归零，飞书照样「成功」。

本测试锁住三件事：

1. modules.json 里 `task_tags_notify` 必须挂着 alert 级邮件兜底入口
   （`scripts/task_tags_alert_fallback.py`，或等价地 cmd 里同时出现
   `--channel email` 与 `--level alert`），否则判失败；
2. 兜底脚本真实存在，带 `--dry-run` / `--level`，且说明里讲明
   「先查 notify_log 再决定」的语义（不是空壳）；
3. 兜底脚本不写死本机绝对路径（`/home/`、`/opt/coze`、`/root/`）。

    python3 case/_lib/test_alert_email_channel.py
"""
from __future__ import annotations

import json
import os
import re

LIB = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(LIB))
MODULES = os.path.join(REPO, "modules.json")
FALLBACK = os.path.join(REPO, "scripts", "task_tags_alert_fallback.py")


def _modules():
    with open(MODULES, encoding="utf-8") as f:
        data = json.load(f)
    mods = data["modules"] if isinstance(data, dict) else data
    return {m["key"]: m for m in mods}


def test_task_tags_notify_has_alert_email_sender():
    """alert 级结论邮件必须有落点（兜底脚本或 cmd 里的 --channel email --level alert）。"""
    m = _modules().get("task_tags_notify")
    assert m, "modules.json 里没有 task_tags_notify"
    cmd = m.get("cmd") or ""
    via_script = "task_tags_alert_fallback.py" in cmd
    via_inline = "--channel email" in cmd and "--level alert" in cmd
    assert via_script or via_inline, (
        "task_tags_notify 既没有兜底脚本也没有内联 alert 邮件：\n"
        "alert 级结论将没有任何邮件发送方（逐任务发送器 data_hub.tech.notify.notify_now "
        "在本仓/DataHub 任何分支都不存在）\ncmd=%s" % cmd)


def test_fallback_script_exists_and_has_flags():
    assert os.path.isfile(FALLBACK), "缺少 %s" % FALLBACK
    src = open(FALLBACK, encoding="utf-8").read()
    for needle in ("--dry-run", "--level", "alert", "notify_log"):
        assert needle in src, "兜底脚本缺少 %r（要么是空壳，要么语义丢了）" % needle


def test_fallback_script_has_no_absolute_host_path():
    src = open(FALLBACK, encoding="utf-8").read()
    for bad in ("/home/", "/opt/coze", "/root/"):
        assert bad not in src, "兜底脚本写死了本机绝对路径 %r" % bad
    # 也不许直连 akshare / 明文 webhook
    assert not re.search(r"^\s*import\s+akshare|^\s*from\s+akshare", src, re.M), \
        "兜底脚本不该直连 akshare"


def test_task_tags_desc_states_fallback_semantics():
    """desc 必须说明「逐任务优先 + 兜底只补未发的」，否则运维看不懂 22:35 为何还发邮件。"""
    desc = _modules()["task_tags_notify"].get("desc") or ""
    assert "兜底" in desc, "task_tags_notify 的 desc 没写兜底语义：%s" % desc
    assert "notify_log" in desc or "幂等" in desc, "desc 没讲清幂等/不重复发"


def main():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    ok = 0
    for fn in fns:
        fn()
        print("  ✅ %s" % fn.__name__)
        ok += 1
    print("\n%d/%d 通过" % (ok, len(fns)))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())