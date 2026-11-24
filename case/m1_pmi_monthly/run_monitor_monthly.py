#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""月度宏观监测统一入口：M1 告警 → PMI 全链路（爬取+判定+报告+飞书卡片+邮件）
与十五号定时任务 job_c5a0f1cb（cron 0 9 15 * *）一一对应，一条命令完成两步。
全部取数均为程序化爬虫（M1=东财 API，PMI=统计局发布页），脚本内无 LLM 调用，不消耗 Token。

用法：
  python run_monitor_monthly.py             # 两步串行执行（触发才发信）
  python run_monitor_monthly.py --dry-run   # 只计算打印，不发送
  python run_monitor_monthly.py --force     # 忽略同月幂等强制重发
"""
import argparse
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))


def run(script, args):
    cmd = [sys.executable, os.path.join(BASE, script)] + args
    print(f"\n[monitor] $ python {script} {' '.join(args)}", flush=True)
    proc = subprocess.run(cmd, cwd=BASE)
    if proc.returncode != 0:
        print(f"[monitor] {script} 失败，退出码 {proc.returncode}", flush=True)
        sys.exit(proc.returncode)
    return proc.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="全链路不实际发送")
    ap.add_argument("--force", action="store_true", help="忽略同月幂等强制重发")
    args = ap.parse_args()

    extra = []
    if args.dry_run:
        extra.append("--dry-run")
    if args.force:
        extra.append("--force")

    run("m1_alert.py", list(extra))
    run("run_pmi_monthly.py", list(extra))
    print(f"\n[monitor] M1 + PMI 月度监测全链路完成（{'dry-run' if args.dry_run else '已发送'}）")


if __name__ == "__main__":
    main()