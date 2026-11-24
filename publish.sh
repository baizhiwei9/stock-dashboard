#!/bin/bash
# Stock Dashboard · 发布到 Pages 固定网址
# 由 run_daily.sh 在 build 成功后调用；也可单独执行。纯脚本零 token。
set -uo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export LANG=C.UTF-8
export TZ=Asia/Shanghai

HERE="$(cd "$(dirname "$0")" && pwd)"
CLI=/usr/bin/coze-dev
LOG="$HERE/daily.log"
APP_ID=7711640577216921642
APP_NAME="Stock Dashboard"

ts() { date '+%Y-%m-%d %H:%M:%S'; }

[ -f "$HERE/index.html" ] || { echo "$(ts) 发布跳过：index.html 不存在" >> "$LOG"; exit 1; }

cd "$HERE" || exit 1

# 只把 index.html 放进干净的发布目录，避免运行时文件（state.json.tmp 等）
# 与打包进程竞态导致 SOURCE_PACKAGE_FAILED
PUB="$HERE/pub"
rm -rf "$PUB" 2>/dev/null
mkdir -p "$PUB"
cp -f "$HERE/index.html" "$PUB/index.html"

OUT=$("$CLI" deploy --format json --source-root "$PUB" --application-type pages \
        --app-id "$APP_ID" --name "$APP_NAME" --yes 2>&1)
DID=$(printf '%s' "$OUT" | python3 -c "
import json,sys
try: print(json.load(sys.stdin).get('deploy_history_id',''))
except Exception: print('')
" 2>/dev/null)

if [ -z "$DID" ]; then
    echo "$(ts) 发布失败：未拿到 deploy_history_id" >> "$LOG"
    echo "$OUT" | head -5 >> "$LOG"
    exit 1
fi

for i in $(seq 1 40); do
    sleep 5
    ST=$("$CLI" deploy --format json status --app-id "$APP_ID" --deploy-id "$DID" 2>/dev/null \
         | python3 -c "
import json,sys
try: print(json.load(sys.stdin).get('status',''))
except Exception: print('')
" 2>/dev/null)
    case "$ST" in
        Succeeded)
            URL=$("$CLI" deploy --format json status --app-id "$APP_ID" --deploy-id "$DID" 2>/dev/null \
                  | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin).get('domains') or []
    print(d[0] if d else '')
except Exception: print('')
" 2>/dev/null)
            echo "$(ts) 发布成功 $URL deploy=$DID" >> "$LOG"
            exit 0 ;;
        Failed|Canceled)
            echo "$(ts) 发布失败 status=$ST deploy=$DID" >> "$LOG"
            exit 1 ;;
    esac
done

echo "$(ts) 发布超时 deploy=$DID 最后状态=${ST:-unknown}" >> "$LOG"
exit 1
