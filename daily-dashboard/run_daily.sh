#!/bin/bash
# 股市监控画布 · 每日看板自动更新
# 流程：报告 md + 期权库 → facts.db → index.html
# 纯 Python，零 token。由系统 crontab 在工作日 22:10 触发。
set -uo pipefail

# cron 环境只有精简 PATH；这里「补充」而不是「覆盖」，否则 pyenv/venv/自建
# Python 会被 /usr/bin 顶掉 —— 迁到另一台机器后 /usr/bin/python3 未必存在。
export PATH="$PATH:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export LANG=C.UTF-8
export TZ=Asia/Shanghai

DASH="${DASH_DIR:-$(cd "$(dirname "$0")" && pwd)}"
LOG=$DASH/daily.log

# 解释器：PYTHON 环境变量 > python3 > python（不再写死 /usr/bin/python3）
if [ -n "${PYTHON:-}" ]; then
    PY="$PYTHON"
elif command -v python3 >/dev/null 2>&1; then
    PY=python3
elif command -v python >/dev/null 2>&1; then
    PY=python
else
    echo "===== $(date '+%Y-%m-%d %H:%M:%S') 开始 =====" >> "$LOG"
    echo "找不到 python3（请装 Python 3.11+ 或设 PYTHON=/path/to/python3）" >> "$LOG"
    exit 1
fi

mkdir -p "$DASH"
echo "===== $(date '+%Y-%m-%d %H:%M:%S') 开始 =====" >> "$LOG"

cd "$DASH" || { echo "cd 失败" >> "$LOG"; exit 1; }

# 1. 增量入库（新报告才解析；解析器升版会自动全量重解析）
"$PY" ingest.py >> "$LOG" 2>&1
RC1=$?

# 1.5 事实库为空时明确告警（换机后 DASH_BASE 没指向报告目录时，页面会「成功生成但全空」）
FACTS=$("$PY" - <<'PYEOF' 2>/dev/null || echo 0
import sqlite3
try:
    print(sqlite3.connect("facts.db").execute("select count(*) from facts").fetchone()[0])
except Exception:
    print(0)
PYEOF
)
if [ "${FACTS:-0}" -eq 0 ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') [warn] facts 为 0：DASH_BASE 未指向报告目录或报告还没同步，本次生成的是空看板" >> "$LOG"
fi

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
