#!/bin/bash
# Stock Dashboard · 发布到 Pages 固定网址
# 由 run_daily.sh 在 build 成功后调用；也可单独执行。纯脚本零 token。
set -uo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export LANG=C.UTF-8
export TZ=Asia/Shanghai
# scheduler 起的子进程偶尔拿不到登录态（AUTH_REQUIRED），显式固定 HOME
export HOME="${HOME:-/root}"

HERE="$(cd "$(dirname "$0")" && pwd)"
CLI=/usr/bin/coze-dev
LOG="$HERE/daily.log"
APP_ID=7711640577216921642
APP_NAME="Stock Dashboard"

ts() { date '+%Y-%m-%d %H:%M:%S'; }

# 失败诊断：daily.log 记全量，stderr 打一行摘要
# 为什么要有这个：2026-11-25 22:43 build 整链 exit=1，health 只留了「退出码 1」，
# daily.log 里虽有 AUTH_REQUIRED 原文，但没人会去翻；scheduler 的 logs/build.log
# 只有 health_run 那句「退出码 1」。失败原因必须落在当次日志里。
diag() {
    local reason="$1"
    {
        echo "$(ts) 发布失败：$reason"
        echo "--- 诊断 ---"
        echo "HOME=$HOME  CLI=$CLI"
        if [ -r "$HOME/.coze/cli/config.json" ]; then
            echo "config.json: 存在 $(stat -c '%a %s字节' "$HOME/.coze/cli/config.json" 2>/dev/null)"
        else
            echo "config.json: 不存在（$HOME/.coze/cli/config.json）"
        fi
        if [ -n "${COZE_API_TOKEN:-}" ]; then echo "COZE_API_TOKEN: 已设置"; else echo "COZE_API_TOKEN: 未设置"; fi
        echo "auth status: $("$CLI" deploy auth status --format json 2>&1 | tr -d '\n' | head -c 300)"
        echo "--- deploy 输出（末 15 行）---"
        printf '%s\n' "$OUT" | tail -15
    } >> "$LOG"
    # stderr 一行摘要：scheduler 的 logs/build.log 能看到，crontab 邮件也能看到
    local code
    code=$(printf '%s' "$OUT" | grep -oE '"code": *"[A-Z_]+"' | head -1 | grep -oE '[A-Z_]+')
    echo "[publish] 发布失败：$reason${code:+（$code）}" >&2
}

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
    # 一次自愈重试：偶发 AUTH_REQUIRED / 网络抖动
    sleep 5
    OUT=$("$CLI" deploy --format json --source-root "$PUB" --application-type pages \
            --app-id "$APP_ID" --name "$APP_NAME" --yes 2>&1)
    DID=$(printf '%s' "$OUT" | python3 -c "
import json,sys
try: print(json.load(sys.stdin).get('deploy_history_id',''))
except Exception: print('')
" 2>/dev/null)
fi

if [ -z "$DID" ]; then
    diag "未拿到 deploy_history_id（已重试1次）"
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
            diag "deploy 状态 $ST（deploy=$DID）"
            exit 1 ;;
    esac
done

diag "轮询 200s 超时，最后状态=${ST:-unknown}（deploy=$DID）"
exit 1
