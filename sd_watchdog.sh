#!/bin/bash
# scheduler 看门狗：由 cron 每分钟调用一次。
# 作用只有一个 —— 保证 scheduler 常驻进程活着；业务调度仍全部由 scheduler.py 决定，
# cron 不直接跑任何业务任务（避免和「scheduler 统一管理」冲突）。
# 存在的理由：本环境的 bash 工具调用结束后会清理该调用派生的所有子进程，
# 所以 setsid/nohup 也留不住常驻进程；而 cron 在宿主侧运行，起得来就活得久。
SD="/Coze/Drive/股市监控画布/stock-dashboard"
cd "$SD" || exit 0
mkdir -p logs
/usr/bin/python3 scheduler.py start >> logs/watchdog.log 2>&1
