#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PMI 月度分析全链路入口：爬取 → 判定 → 报告 → 飞书卡片 + 邮件提醒

整合：
  1. pmi_fetch.py  从国家统计局官方发布页自动取数（最新发布页自动定位 + 历史补齐）
  2. pmi_report.py 7 项阈值判定 + 5 类触发动作 + 绿/黄/红仓位分级，生成分析报告
  3. pmi_push.py   飞书交互式卡片推送分析报告；黄色及以上仓位邮件提醒
                    （410119229@qq.com，dws 主通道 + 163 SMTP 回退），同月幂等

用法：
  python run_pmi_monthly.py             # 全链路（推送/邮件）
  python run_pmi_monthly.py --dry-run   # 全链路但仅打印，不发送
  python run_pmi_monthly.py --force     # 忽略同月幂等
"""
import argparse
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))


def run(script, args):
    cmd = [sys.executable, os.path.join(BASE, script)] + args
    print(f"\n[run_pmi] $ python {script} {' '.join(args)}")
    proc = subprocess.run(cmd, cwd=BASE)
    if proc.returncode != 0:
        print(f"[run_pmi] {script} 失败，退出码 {proc.returncode}")
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

    run("pmi_fetch.py", [])
    run("pmi_report.py", [])
    push_args = list(extra)
    if args.force:
        push_args.append("--force")
    run("pmi_push.py", push_args)
    print("\n[run_pmi] PMI 月度分析全链路完成")


if __name__ == "__main__":
    main()