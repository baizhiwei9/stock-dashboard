#!/bin/bash
# 资金面因子日报 —— case 任务入口（原 strategies/run_daily_factor.sh 的本地化版本）
#
# 只改一处：工作目录不再写死 workspace 绝对路径，改为脚本自己所在目录；
# 其余闸门（周末跳过 / 非交易日跳过 / 日志落 logs/）与原件完全一致。
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
LOGDIR="$HERE/logs"
mkdir -p "$LOGDIR"

TODAY=$(date +%Y-%m-%d)
DOW=$(date +%u)  # 1=Mon ... 7=Sun

if [ "$DOW" -gt 5 ]; then
    echo "$TODAY 周末跳过" >> "$LOGDIR/skip.log"
    exit 0
fi
python3 -c "from trading_day_check import check_trading_day; check_trading_day()" || { echo "$TODAY 非交易日跳过" >> "$LOGDIR/skip.log"; exit 0; }

cd "$HERE"
python3 -u daily_factor_report.py --date "$TODAY" >> "$LOGDIR/$TODAY.log" 2>&1
