#!/bin/bash
# 股市监控画布 · 发布到 Pages 固定网址
# 由 run_daily.sh 在 build 成功后调用；也可单独执行。
# 纯脚本零 token。发布失败只记日志，不影响看板生成。
set -uo pipefail
# 补 PATH 而不是覆盖（pyenv/venv/自建 Python 才不会被 /usr/bin 顶掉）
export PATH="$PATH:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export LANG=C.UTF-8
export TZ=Asia/Shanghai

DASH="${DASH_DIR:-$(cd "$(dirname "$0")" && pwd)}"
SITE="$DASH/site"
LOG=$DASH/daily.log

# 解释器：PYTHON > python3 > python
if [ -n "${PYTHON:-}" ]; then PY="$PYTHON"
elif command -v python3 >/dev/null 2>&1; then PY=python3
elif command -v python >/dev/null 2>&1; then PY=python
else PY=""; fi

ts() { date '+%Y-%m-%d %H:%M:%S'; }

# coze-dev 是 Coze 平台专用 CLI，普通服务器没有：没有就明确跳过，
# 不要每天往 daily.log 里写一条「发布失败」（发布是可选的，看板本体已生成）。
CLI="${COZE_DEV_CLI:-/usr/bin/coze-dev}"
if [ ! -x "$CLI" ]; then
    echo "$(ts) 发布跳过：未找到 $CLI（换机器/非 Coze 平台属正常，设 COZE_DEV_CLI 指向可执行文件）" >> "$LOG"
    exit 0
fi
if [ -z "$PY" ]; then
    echo "$(ts) 发布跳过：没有 python3，无法解析 deploy 结果" >> "$LOG"
    exit 0
fi
APP_ID="${DASH_APP_ID:-7711624744050933823}"
APP_NAME="${DASH_APP_NAME:-股市监控画布每日看板}"
URL="${DASH_URL:-https://4hs5cr9vd6.page.coze.site}"

[ -f "$DASH/index.html" ] || { echo "$(ts) 发布跳过：index.html 不存在" >> "$LOG"; exit 1; }

mkdir -p "$SITE"
cp "$DASH/index.html" "$SITE/index.html" || { echo "$(ts) 发布失败：复制 index.html 出错" >> "$LOG"; exit 1; }

cd "$SITE" || exit 1

# 必须显式带 --app-id：不带的话 CLI 会按 name 另建一个新应用，
# 页面就跑到新域名去了（2026-11-27 踩过这个坑）。
OUT=$("$CLI" deploy --format json --source-root "$SITE" --application-type pages \
        --app-id "$APP_ID" --name "$APP_NAME" --yes 2>&1)
# CLI 会在 JSON 前面打 Warning 之类的非 JSON 行，先定位第一个 { 再解析
DID=$(printf '%s' "$OUT" | "$PY" -c "
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
         | "$PY" -c "
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
