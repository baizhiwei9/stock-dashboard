#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""任务标签 alert 级邮件兜底通道。

背景（2026-11-27，#149 之后）
──────────────────────────
`task_tags_notify` 原先在 22:35 把当天所有 alert 级结论凑成一封
「任务标签·严重」邮件。该统一邮件已改成「每个任务跑完当场发自己那封」，
前提是各适配器调用 data_hub 侧的逐任务发送器（`notify_now`）。

问题：`notify_now` 在本仓与 DataHub 的所有分支/提交里都不存在，
`data_hub.tech.tasks.*` / `producers.*` 也不发任何通知。也就是说
统一邮件删掉之后，alert 级结论的邮件通道会**静默归零** —— 报告照常
「成功」推飞书，邮件一封不发，没人发现。

本脚本只做一件事：**先查库，再决定要不要兜底发**
──────────────────────────────────────────────
读 notify_log 判断近 N 天的 alert 级记录里还有没有「没发过 email」的行：

- 全部已发（逐任务邮件确实在工作）→ 直接跳过，不重复发（保持 #149 的语义）；
- 还有没发的（逐任务发送器缺失 → 静默丢失）→ 走 `data_hub.tech.notify`
  发一封汇总兜底；该模块自身还有 notify_log 幂等，已发的行不会重发；
- 探测失败（DataHub 不可导入 / 库读不到）→ 保守发送，宁可多一封也不静默丢。

用法
────
    python3 scripts/task_tags_alert_fallback.py --to 410119229@qq.com          # 判定 + 兜底发送
    python3 scripts/task_tags_alert_fallback.py --to 410119229@qq.com --dry-run # 只打印判定，不发

退出码：0=跳过或已发送成功；非 0=发送失败（交给 health_run 判失败）。
"""
from __future__ import annotations

import argparse
import importlib
import subprocess
import sys

CHANNEL = "email"


def _notify_mod():
    """按模块名取 data_hub.tech.notify（不能 `import ... as n`，tech/__init__ 里有同名函数）。"""
    return importlib.import_module("data_hub.tech.notify")


def pending_alert_rows(days: int):
    """返回 (alert 行数, 其中未发 email 的行数)。探测不了就抛异常，由调用方保守处理。"""
    n = _notify_mod()
    rows = n.fetch_tagged(level="alert", days=days, latest_only=True)
    conn = n._connect()
    try:
        n._ensure_log(conn)
        miss = [r for r in rows
                if not n._already_sent(conn, r["metric_key"], r["security_id"],
                                       r["obs_date"], CHANNEL)]
    finally:
        conn.close()
    return len(rows), len(miss)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="alert 级结论邮件兜底发送")
    ap.add_argument("--to", required=True, help="收件人（逗号分隔）")
    ap.add_argument("--days", type=int, default=7, help="回看天数，默认 7")
    ap.add_argument("--title", default="任务结论标签汇总")
    ap.add_argument("--subject-prefix", default="【任务标签·严重】")
    ap.add_argument("--dry-run", action="store_true", help="只打印判定，不发送")
    a = ap.parse_args(argv)

    try:
        n_rows, n_miss = pending_alert_rows(a.days)
    except Exception as e:                                    # noqa: BLE001
        print("[alert-email] 探测 notify_log 失败（%s: %s）→ 保守发送汇总邮件"
              % (type(e).__name__, e), flush=True)
    else:
        print("[alert-email] 近 %d 天 alert 记录 %d 条，其中未发过 email 的 %d 条"
              % (a.days, n_rows, n_miss), flush=True)
        if n_miss == 0:
            print("[alert-email] 已由逐任务邮件覆盖，跳过兜底（不重复发）", flush=True)
            return 0

    cmd = [sys.executable, "-u", "-m", "data_hub.tech.notify",
           "--channel", CHANNEL, "--to", a.to, "--level", "alert",
           "--days", str(a.days), "--title", a.title,
           "--subject-prefix", a.subject_prefix]
    print("[alert-email] 兜底发送：%s" % " ".join(cmd[3:]), flush=True)
    if a.dry_run:
        print("[alert-email] dry-run，未真发", flush=True)
        return 0
    return subprocess.call(cmd)


if __name__ == "__main__":
    sys.exit(main())