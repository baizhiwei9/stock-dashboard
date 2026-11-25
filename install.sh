#!/bin/bash
# 安装 / 首次部署门禁：敏感配置补齐且可用之前，不允许继续。
#
#   敏感配置默认写 ~/.config/stock-dashboard/secrets.json（仓外，不随仓库外泄）。
#   想放别处就 export COZE_SECRETS=/path/to/secrets.json
#
# 用法：
#   ./install.sh                 交互式（有 TTY 时按提示录入）
#   ./install.sh                 无 TTY：先 export COZE_SEC_* 喂值，会从环境变量落盘
#   SD_SKIP_LIVE=1 ./install.sh  跳过飞书联网校验（离线 / 内网机器）
#
# 门禁口径（与根 DEPLOY.md §3.0 一致，缺一不装）：
#   feishu.app_id / app_secret / webhook   必填，默认联网校验
#   api.password                          必填，>=10 位且非弱口令
#   api.registry_key                      必填，>=16 位且不等于管理密码
#   treasury.* / tushare.* / email.* / fred.api_key / alert.webhook
#                                         选填，缺了对应通道降级，不影响启动
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "找不到 $PY，请先安装 Python 3.11+" >&2
  exit 1
fi
if ! "$PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo "Python 版本过低，需要 3.11+（运行机基线）：$("$PY" -V 2>&1)" >&2
  echo "注意：f-string 内嵌同类型引号在 3.12+ 才合法，代码按 3.11 基线写，别降级跑。" >&2
  exit 1
fi

SECRETS_PATH="$("$PY" -c 'import sys; sys.path.insert(0,"case/_lib"); import app_secrets as s; print(s.config_path())')"
echo "== 敏感配置位置：$SECRETS_PATH"

# 1) 非交互落盘：环境变量里给了值就写进配置文件（没给就跳过，不报错）。
#    有 TTY 且缺项时，人工补齐请直接跑 python3 setup.py（交互引导）。
echo "== 补齐敏感配置（环境变量 -> 配置文件，已有的不覆盖）"
"$PY" setup.py --init ${SD_SKIP_LIVE:+--skip-live} || true

# 2) 完整门禁校验：飞书三项 + 管理密码强度 + 注册密钥，缺一即中止。
#    这里必须用 setup.py --check 而不是 check_secrets.py：
#    check_secrets 只判 REQUIRED_KEYS（api.password / api.registry_key），
#    飞书在 OPTIONAL_KEYS 里，只过 check_secrets 会出现「安装通过但推送必失败」。
echo "== 校验（完整门禁）"
if ! "$PY" setup.py --check ${SD_SKIP_LIVE:+--skip-live}; then
  echo >&2
  echo "安装中止：敏感配置缺失、弱口令或飞书凭据不可用。" >&2
  echo "  有终端：python3 setup.py            # 交互式录入" >&2
  echo "  无终端：export COZE_SEC_<KEY>=... 后重跑 ./install.sh" >&2
  echo "  字段说明见 docs/SECRETS.md" >&2
  exit 1
fi

echo
echo "== 敏感配置就绪。接下来："
echo "   1) 本机模块清单：cp modules.example.json modules.json 后按提示替换占位符"
echo "   2) 全仓自检：   python3 setup.py --audit"
echo "   3) 构建发布：   python3 build.py && ./publish.sh"
echo "   4) 常驻调度：   python3 scheduler.py start"
