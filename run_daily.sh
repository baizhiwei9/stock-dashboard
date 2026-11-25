#!/usr/bin/env bash
# Stock Dashboard 每日流水线
#   ① 跑三个生产者，把指标写进 DataHub tech 指标仓
#   ② 从指标仓读数据生成 index.html
#   ③ 发布到 Pages
# 纯 Python + 系统 crontab 调度，零 token。
set -uo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export LANG=C.UTF-8
export TZ=Asia/Shanghai

DH=/opt/coze/.fuse_drive/dataHub_三级体系
HERE="$(cd "$(dirname "$0")" && pwd)"
LOG="$HERE/daily.log"

ts() { date '+%Y-%m-%d %H:%M:%S'; }
run() { "$@" 2>&1 | grep -vE '^\[20[0-9]{2}-' | tail -12; }

{
  echo "════════════════════════════════════════════"
  echo "$(ts) 开始"

  cd "$DH" || exit 1
  echo "--- 缠论 ---";  run python3 -u -m data_hub.tech.producers.chanlun_daily
  echo "--- DFA ---";   run python3 -u -m data_hub.tech.producers.dfa_daily
  echo "--- RMT ---";   run python3 -u -m data_hub.tech.producers.rmt_daily --lookback 300 --max-comps 150

  cd "$HERE" || exit 1
  echo "--- 构建看板 ---"
  python3 build.py || echo "$(ts) 构建失败"

  if [ -x "$HERE/publish.sh" ]; then
    echo "--- 发布 ---"
    "$HERE/publish.sh"
  fi
  echo "$(ts) 完成"
} >> "$LOG" 2>&1
