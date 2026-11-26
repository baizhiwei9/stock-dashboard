#!/bin/bash
# Hurst 指数午报 crontab 启动脚本
# 工作日 12:30 运行，纯 Python 零 token
#
# 2026-11-26 去云盘化：不再 cd 到 /Coze/Drive/hurst_指数 跑云端旧码，
# 一律跑本脚本所在目录（= case/hurst_daily）的代码，日志落 ./logs。

CASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$CASE_DIR" || exit 1

LOG_DIR="$CASE_DIR/logs"
mkdir -p "$LOG_DIR"

LOG_FILE="$LOG_DIR/hurst_$(date +\%Y\%m\%d).log"

echo "===== $(date '+%Y-%m-%d %H:%M:%S') 开始运行 Hurst 午报 =====" >> "$LOG_FILE"

python3 "$CASE_DIR/hurst_daily.py" >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

echo "===== $(date '+%Y-%m-%d %H:%M:%S') 运行结束，退出码=$EXIT_CODE =====" >> "$LOG_FILE"
echo "" >> "$LOG_FILE"

exit $EXIT_CODE
