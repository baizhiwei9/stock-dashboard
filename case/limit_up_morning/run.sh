#!/bin/bash
# 涨停预测·早盘 —— case 任务入口
# 代码与模型放在 ../_shared/limit_up_predictor（下划线开头，case_registry 不扫），
# 早盘/午盘共用同一份代码与 output/，与原 workspace 布局一致。
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
CODE="$(cd "$HERE/../_shared/limit_up_predictor" && pwd)"
cd "$CODE" || exit 1
exec python3 -u daily_runner.py --mode morning "$@"
