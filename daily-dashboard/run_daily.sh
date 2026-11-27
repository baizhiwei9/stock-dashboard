#!/bin/bash
# 股市监控画布 · 每日看板自动更新
# 流程：报告 md + 期权库 → facts.db → index.html
# 纯 Python，零 token。由系统 crontab 在工作日 22:10 触发。
set -uo pipefail

export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export LANG=C.UTF-8
export TZ=Asia/Shanghai

DASH="${DASH_DIR:-$(cd "$(dirname "$0")" && pwd)}"
LOG=$DASH/daily.log
PY=/usr/bin/python3

mkdir -p "$DASH"
echo "===== $(date '+%Y-%m-%d %H:%M:%S') 开始 =====" >> "$LOG"

cd "$DASH" || { echo "cd 失败" >> "$LOG"; exit 1; }

# 1. 增量入库（新报告才解析；解析器升版会自动全量重解析）
"$PY" ingest.py >> "$LOG" 2>&1
RC1=$?

# 2. 重新渲染看板
"$PY" build_html.py >> "$LOG" 2>&1
RC2=$?

if [ $RC1 -eq 0 ] && [ $RC2 -eq 0 ]; then
    SIZE=$(stat -c %s index.html 2>/dev/null || echo 0)
    echo "$(date '+%Y-%m-%d %H:%M:%S') 成功 index.html=${SIZE}B" >> "$LOG"
else
    echo "$(date '+%Y-%m-%d %H:%M:%S') 失败 ingest=$RC1 build=$RC2" >> "$LOG"
fi

# 日志超过 2000 行就裁剪
# 发布到 Pages 固定网址（失败只记日志，不影响看板）
if [ $RC1 -eq 0 ] && [ $RC2 -eq 0 ]; then
    "$DASH/publish.sh"
fi
if [ -f "$LOG" ] && [ "$(wc -l < "$LOG")" -gt 2000 ]; then
    tail -n 1000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi

exit $(( RC1 != 0 || RC2 != 0 ))
