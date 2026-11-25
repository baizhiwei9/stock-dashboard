#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""月度宏观报告 - 一键执行入口
用法: python run_monthly.py [--no-feishu] [--no-email] [--dry-run]
流程: 取数(主数据+PMI分项) -> 剔除法分析 -> 生成报告(md+html) -> 飞书推送 + 邮件发送

追加模块（2026-10 固化）：PMI 分项剔除法·看真实底色——自动取统计局 PMI 分项（fetch_pmi.py → data/pmi.json）、
执行剔除法分析（pmi_analysis.py → data/pmi_analysis.json），report_builder 将其合并为报告第四部分；
PMI 取数/分析失败不阻塞主报告，重跑时自动剔除旧分析结果并按【原文缺失，无法剔除】降级。
"""
import argparse
import datetime
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from config import ensure_dirs, DATA_DIR, OUT_DIR


def run(cmd):
    print(">>>", " ".join(cmd))
    return subprocess.run(cmd, cwd=HERE, capture_output=True, text=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-feishu", action="store_true", help="跳过飞书推送")
    ap.add_argument("--no-email", action="store_true", help="跳过邮件发送")
    ap.add_argument("--dry-run", action="store_true", help="仅取数+生成，不推送")
    args = ap.parse_args()

    ensure_dirs()

    # 0. EDB 统一刷新（唯一联网入口）：一次刷新报告所需全部序列，后续脚本只读库
    if os.environ.get("SKIP_EDB_REFRESH") != "1":
        env = dict(os.environ, EDB_UPDATE="1")
        # edb_refresh 联网逐指标补数，源站慢时会挂很久（实测能挂 20 分钟以上）。
        # 这里加超时兜底：超时就放弃本次刷新，用库里已有数据继续出报告，不拖垮整条链路。
        timeout = int(os.environ.get("EDB_REFRESH_TIMEOUT") or 600)
        try:
            rr = subprocess.run([sys.executable, "edb_refresh.py"], cwd=HERE,
                                capture_output=True, text=True, env=env, timeout=timeout)
            if rr.returncode != 0:
                print("[run_monthly] EDB 刷新失败（将用库内已有数据继续）：",
                      rr.stdout[-400:], rr.stderr[-400:])
        except subprocess.TimeoutExpired:
            print("[run_monthly] EDB 刷新超时（%ss），跳过刷新，用库内已有数据继续" % timeout)

    # 1. 取数（主数据，读 EDB）
    r1 = run([sys.executable, "fetch_data.py"])
    if r1.returncode != 0:
        print("[run_monthly] 取数失败：", r1.stdout[-800:], r1.stderr[-800:])
        sys.exit(1)

    # 1.5 PMI 取数 + 剔除法分析（追加模块：取数 -> data/pmi.json -> 分析 -> data/pmi_analysis.json）
    r1p = run([sys.executable, "fetch_pmi.py"])
    pmi_ok = r1p.returncode == 0
    if not pmi_ok:
        print("[run_monthly] PMI 取数失败（报告将按缺失降级）：", r1p.stdout[-400:], r1p.stderr[-400:])
    else:
        r1a = run([sys.executable, "pmi_analysis.py"])
        pmi_ok = r1a.returncode == 0
        if not pmi_ok:
            print("[run_monthly] PMI 剔除法分析失败（报告将按缺失降级）：", r1a.stdout[-400:], r1a.stderr[-400:])
    if not pmi_ok:
        # 失败时移除旧分析结果，防止过期分项混入本期报告；report_builder 按【原文缺失，无法剔除】降级
        try:
            os.remove(os.path.join(DATA_DIR, "pmi_analysis.json"))
            print("[run_monthly] 已移除过期 pmi_analysis.json，避免旧数据混入")
        except OSError:
            pass

    # 1.6 PPI 深度取数（九步分析数据底座；失败不阻塞主报告，章节按「数据未取到」降级）
    r1q = run([sys.executable, "fetch_ppi.py"])
    if r1q.returncode != 0:
        print("[run_monthly] PPI 取数失败（PPI 九步章节将降级）：", r1q.stdout[-400:], r1q.stderr[-400:])
        try:
            os.remove(os.path.join(DATA_DIR, "ppi.json"))
            print("[run_monthly] 已移除过期 ppi.json，避免旧数据混入")
        except OSError:
            pass

    # 2. 生成报告
    r2 = run([sys.executable, "report_builder.py"])
    if r2.returncode != 0:
        print("[run_monthly] 报告生成失败：", r2.stdout[-800:], r2.stderr[-800:])
        sys.exit(1)

    month = datetime.date.today().strftime("%Y%m")
    md_path = os.path.join(OUT_DIR, f"report_{month}.md")
    html_path = os.path.join(OUT_DIR, f"report_{month}.html")
    if not os.path.exists(md_path):
        print("[run_monthly] 报告文件未生成：", md_path)
        sys.exit(1)

    if args.dry_run:
        print("[run_monthly] dry-run 完成，未推送。报告：", md_path)
        return

    # 3. 推送
    feishu_path = os.path.join(OUT_DIR, f"report_{month}_feishu.md")
    cmd = [sys.executable, "pusher.py", "--md", md_path, "--html", html_path]
    if os.path.exists(feishu_path):
        cmd += ["--feishu-md", feishu_path]
    if args.no_feishu:
        cmd.append("--email-only")
    if args.no_email:
        cmd.append("--feishu-only")
    r3 = run(cmd)
    print(r3.stdout[-1500:] or r3.stderr[-1500:])
    if r3.returncode != 0:
        print("[run_monthly] 推送返回非零退出码")
        sys.exit(1)
    print("[run_monthly] 全链路完成")


if __name__ == "__main__":
    main()