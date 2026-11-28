# -*- coding: utf-8 -*-
"""fetch 阶段：采集 ETF 规模 / 期货基差 / 情绪数据 + 被动化率历史回溯

实际取数代码在 scripts/ 下，与独立仓库 clearance-cycle-monitor 保持同一份。
产物同时写到 output/data/（脚本原生位置）和 data/（框架 artifacts 默认位置）。
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "scripts")
DATA = os.path.join(HERE, "data")
SRC_DATA = os.path.join(HERE, "output", "data")

STEPS = [
    ("宽基ETF规模", "fetch_etf_scale.py"),
    ("股指期货基差", "fetch_basis.py"),
    ("情绪与资金面", "fetch_sentiment.py"),
    ("被动化率历史回溯", "build_passive_history.py"),
]


def run(name, script):
    print(f"--- [{name}] {script}", flush=True)
    r = subprocess.run([sys.executable, "-u", script], cwd=SCRIPTS)
    if r.returncode != 0:
        raise SystemExit(f"{name} 失败，退出码 {r.returncode}")


def main():
    if not os.path.isdir(SCRIPTS):
        raise SystemExit(f"缺少取数脚本目录：{SCRIPTS}")
    for name, script in STEPS:
        run(name, script)

    os.makedirs(DATA, exist_ok=True)
    n = 0
    if os.path.isdir(SRC_DATA):
        for f in os.listdir(SRC_DATA):
            if f.endswith((".json", ".csv")):
                shutil.copy2(os.path.join(SRC_DATA, f), os.path.join(DATA, f))
                n += 1
    print(f"已同步 {n} 个取数产物到 data/")

    required = ["etf_scale.json", "basis.json", "sentiment.json", "passive_history.csv"]
    missing = [f for f in required if not os.path.exists(os.path.join(DATA, f))]
    if missing:
        raise SystemExit(f"取数产物缺失：{missing}")


if __name__ == "__main__":
    main()
