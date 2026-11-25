#!/bin/bash
# 安装/首次部署门禁：必须先把敏感配置补齐，才允许继续。
# 用法：./install.sh
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "找不到 $PY，请先安装 Python 3.9+" >&2
  exit 1
fi
if ! "$PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)'; then
  echo "Python 版本过低，需要 3.9+：$("$PY" -V 2>&1)" >&2
  exit 1
fi

SECRETS="${COZE_SECRETS:-$PWD/config/secrets.json}"
export COZE_SECRETS="$SECRETS"
mkdir -p "$(dirname "$SECRETS")"
if [ ! -f "$SECRETS" ]; then
  cp config/secrets.example.json "$SECRETS"
  chmod 600 "$SECRETS"
  echo "== 已创建 $SECRETS（权限 600，已加入 .gitignore）"
fi

echo "== 补齐敏感配置（必填项不填完不能安装）"
"$PY" case/_tools/check_secrets.py --init

echo "== 校验"
if ! "$PY" case/_tools/check_secrets.py; then
  echo >&2
  echo "安装中止：敏感配置缺失或权限不合规。" >&2
  echo "修正后重新运行 ./install.sh；字段说明见 config/README.md。" >&2
  exit 1
fi

echo
echo "== 敏感配置就绪。接下来："
echo "   1) 检查 modules.json 的任务清单"
echo "   2) python3 build.py && ./publish.sh"
echo "   3) python3 scheduler.py start    # 常驻调度"
echo "   4) 按 docs/DEPLOY.md 第 7 节配 sd_watchdog.sh 的 cron"
