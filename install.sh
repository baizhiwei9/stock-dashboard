#!/bin/bash
# 安装 / 首次部署门禁：敏感配置补齐之前，不允许继续。
#
#   敏感配置默认写 ~/.config/stock-dashboard/secrets.json（仓外，不随仓库外泄）。
#   想放别处就 export COZE_SECRETS=/path/to/secrets.json
#
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

SECRETS_PATH="$("$PY" -c 'import sys; sys.path.insert(0,"case/_lib"); import app_secrets as s; print(s.config_path())')"
echo "== 敏感配置位置：$SECRETS_PATH"

echo "== 补齐敏感配置（必填项不填完不能安装）"
"$PY" case/_tools/check_secrets.py --init || true

echo "== 校验"
if ! "$PY" case/_tools/check_secrets.py; then
  echo >&2
  echo "安装中止：敏感配置缺失或权限不合规。" >&2
  echo "修正后重新运行 ./install.sh；字段说明见 docs/SECRETS.md。" >&2
  exit 1
fi

echo
echo "== 敏感配置就绪。接下来："
echo "   1) 本机模块清单：cp modules.example.json modules.json 后按提示替换占位符"
echo "   2) 全仓自检：   python3 setup.py --audit"
echo "   3) 构建发布：   python3 build.py && ./publish.sh"
echo "   4) 常驻调度：   python3 scheduler.py start"
