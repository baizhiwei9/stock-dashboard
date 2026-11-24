# -*- coding: utf-8 -*-
"""MAD 均线指选股 · 周度一键入口：筛选链路 → 出图 → 名单图 → 飞书投递（网格报告 + 交互卡片）。

用法：
    python3 weekly_run.py              # 完整链路（刷新数据，需联网）
    python3 weekly_run.py --no-fetch   # 离线复跑（PE/一致预期取同口径缓存）
"""
import argparse
import os
import subprocess
import sys

WS = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("--no-fetch", action="store_true")
a = ap.parse_args()

STEPS = [["weekly_screen.py"] + (["--no-fetch"] if a.no_fetch else []),
         ["weekly_chart.py"],
         ["weekly_list_image.py"],
         ["weekly_push.py", "--mode", "both"]]
for st in STEPS:
    print("[weekly_run] → %s" % " ".join(st), flush=True)
    r = subprocess.run([sys.executable, "-u"] + st, cwd=WS, env={**os.environ})
    if r.returncode:
        print("[weekly_run] 失败于 %s（退出码 %d）" % (st[0], r.returncode)); sys.exit(r.returncode)
print("[weekly_run] 完成：筛选 + 出图 + 名单图 + 飞书投递")
