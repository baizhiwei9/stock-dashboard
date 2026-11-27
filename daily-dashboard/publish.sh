#!/bin/bash
# 股市监控画布 · 发布到 Pages 固定网址
# 由 run_daily.sh 在 build 成功后调用；也可单独执行。
# 纯脚本零 token。发布失败只记日志，不影响看板生成。
set -uo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export LANG=C.UTF-8
export TZ=Asia/Shanghai

DASH="${DASH_DIR:-$(cd "$(dirname "$0")" && pwd)}"
SITE="$DASH/site"
CLI=/usr/bin/coze-dev
LOG=$DASH/daily.log
APP_ID="${DASH_APP_ID:-7711624744050933823}"
APP_NAME="${DASH_APP_NAME:-股市监控画布每日看板}"
URL="${DASH_URL:-https://4hs5cr9vd6.page.coze.site}"

ts() { date '+%Y-%m-%d %H:%M:%S'; }

[ -f "$DASH/index.html" ] || { echo "$(ts) 发布跳过：index.html 不存在" >> "$LOG"; exit 1; }

mkdir -p "$SITE"
cp "$DASH/index.html" "$SITE/index.html" || { echo "$(ts) 发布失败：复制 index.html 出错" >> "$LOG"; exit 1; }

cd "$SITE" || exit 1

# 必须显式带 --app-id：不带的话 CLI 会按 name 另建一个新应用，
# 页面就跑到新域名去了（2026-11-27 踩过这个坑）。
OUT=$("$CLI" deploy --format json --source-root "$SITE" --application-type pages \
        --app-id "$APP_ID" --name "$APP_NAME" --yes 2>&1)
# CLI 会在 JSON 前面打 Warning 之类的非 JSON 行，先定位第一个 { 再解析
DID=$(printf '%s' "$OUT" | python3 -c "
import json,sys
raw=sys.stdin.read()
i=raw.find('{')
try:
    print(json.loads(raw[i:]).get('deploy_history_id','') if i>=0 else '')
except Exception:
    print('')
" 2>/dev/null)

if [ -z "$DID" ]; then
    echo "$(ts) 发布失败：未拿到 deploy_history_id" >> "$LOG"
    echo "$OUT" | head -5 >> "$LOG"
    exit 1
fi

ST=""
for i in $(seq 1 30); do
    sleep 5
    ST=$("$CLI" deploy --format json status --app-id "$APP_ID" --deploy-id "$DID" 2>/dev/null \
         | python3 -c "
import json,sys
raw=sys.stdin.read()
i=raw.find('{')
try:
    print(json.loads(raw[i:]).get('status','') if i>=0 else '')
except Exception:
    print('')
" 2>/dev/null)
    case "$ST" in
        Succeeded)
            echo "$(ts) 发布成功 $URL deploy=$DID" >> "$LOG"
            exit 0 ;;
        Failed|Canceled)
            echo "$(ts) 发布失败 status=$ST deploy=$DID" >> "$LOG"
            exit 1 ;;
    esac
done

echo "$(ts) 发布超时 deploy=$DID 最后状态=${ST:-unknown}（可用同 ID 恢复查询）" >> "$LOG"
exit 1
