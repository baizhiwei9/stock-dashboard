#!/bin/bash
# 涨停预测·午盘 —— case 任务入口（与早盘共用 ../_shared/limit_up_predictor）
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
CODE="$(cd "$HERE/../_shared/limit_up_predictor" && pwd)"
cd "$CODE" || exit 1
exec python3 -u daily_runner.py --mode noon "$@"
