#!/bin/bash
# Hurst 指数午报 crontab 启动脚本
# 工作日 12:30 运行，纯 Python 零 token
# 2026-11-27 去云盘化：以脚本自身所在目录为根（原写死 /Coze/Drive/hurst_指数）

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE" || exit 1

LOG_DIR="$HERE/logs"
mkdir -p "$LOG_DIR"

LOG_FILE="$LOG_DIR/hurst_$(date +\%Y\%m\%d).log"

echo "===== $(date '+%Y-%m-%d %H:%M:%S') 开始运行 Hurst 午报 =====" >> "$LOG_FILE"

python3 "$HERE/hurst_daily.py" >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

echo "===== $(date '+%Y-%m-%d %H:%M:%S') 运行结束，退出码=$EXIT_CODE =====" >> "$LOG_FILE"
echo "" >> "$LOG_FILE"

exit $EXIT_CODE
