#!/bin/bash
# scheduler 看门狗：由 cron 每分钟调用一次。
# 作用只有一个 —— 保证 scheduler 常驻进程活着；业务调度仍全部由 scheduler.py 决定，
# cron 不直接跑任何业务任务（避免和「scheduler 统一管理」冲突）。
# 存在的理由：本环境的 bash 工具调用结束后会清理该调用派生的所有子进程，
# 所以 setsid/nohup 也留不住常驻进程；而 cron 在宿主侧运行，起得来就活得久。
SD="/Coze/Drive/股市监控画布/stock-dashboard"

# 私有任务（precious-task）挂载点：只跑与落库，不上牌面。
# 不设这个变量 = private 任务不进调度（不报错、静默消失），所以必须写死在这里。
export CASE_EXTRA_DIRS="/Coze/Drive/股市监控画布/precious-task/case"

# ── case 任务的运行环境 ────────────────────────────────────────────
# 取值依据：原 crontab 里所有 data_hub 任务统一用 PYTHONPATH 指这个目录，
# 这里保持一致，不再另造路径。
DH="/opt/coze/.fuse_drive/dataHub_三级体系"
export EDB_REPO="$DH"          # dh.py 靠它找含 data_hub/ 的仓库
export PYTHONPATH="$DH"        # scheduler 与 runner 子进程都继承

# PYLIBS_DIR：**故意不设**。
# 原设计是「第三方包（akshare/tushare/…）集中放一个公共目录，靠 PYLIBS_DIR 引用」，
# 机制保留在 task.json 的 requires 和 runner 里（设了就自动前置进 sys.path）。
# 但本机第三方包已全局装在 /usr/local/lib/python3.13/dist-packages，
# 原 crontab 里也从未设过这个变量，所以缺省不设、走系统 site-packages。
# 真要隔离时，在这里 export 一行即可，无需改任何任务。
# export PYLIBS_DIR=/path/to/pylibs

cd "$SD" || exit 0
mkdir -p logs
echo "[$(date "+%Y-%m-%d %H:%M:%S")] watchdog: CASE_EXTRA_DIRS=${CASE_EXTRA_DIRS:-<未设>} EDB_REPO=${EDB_REPO:-<未设>} PYLIBS_DIR=${PYLIBS_DIR:-<未设，走系统包>}" >> logs/watchdog.log
/usr/bin/python3 scheduler.py start >> logs/watchdog.log 2>&1
