#!/usr/bin/env bash
# switch_datahub_path.sh - 把 crontab 里的 DataHub 路径一次性切到本机 checkout。
#
# 为什么单独一个脚本
#   60 条 cron 任务的 PYTHONPATH 写的是云盘路径。迁到本机盘后这些任务仍然 import
#   云盘那份 data_hub、写云盘那份 output/hub_store.db，和调度器用的本机库不是同一个，
#   表现为「任务在跑，看板数字不动」。手工 sed 60 条太容易漏，固化成脚本：
#   默认 dry-run 看 diff，--apply 才动，动之前自动备份，动完逐条核对残留。
#
# 用法
#   bash scripts/switch_datahub_path.sh              # dry-run，只打印将要改什么
#   bash scripts/switch_datahub_path.sh --apply      # 真改（自动备份到 /tmp）
#   bash scripts/switch_datahub_path.sh --revert     # 回滚到最近一次备份
#   bash scripts/switch_datahub_path.sh --to <路径>  # 指定目标（默认本机同级 checkout）
#
# 退出码：0 成功；1 目标不可用/装载失败；2 无改动；3 回滚失败；64 参数错误。

set -uo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)
REPO=$(cd -- "$SCRIPT_DIR/.." >/dev/null 2>&1 && pwd -P)

# 源：云盘那份老 DataHub。换部署形态时只改这一行。
OLD_PATH=${OLD_PATH:-/opt/coze/.fuse_drive/dataHub_三级体系}
# 目标：默认取仓库根 datahub_path.py 的解析结果（与 build.py / scheduler.py 同口径）
NEW_PATH=${NEW_PATH:-$(cd "$REPO" && python3 datahub_path.py 2>/dev/null || true)}

MODE=dry-run
while [ $# -gt 0 ]; do
  case "$1" in
    --apply)   MODE=apply ;;
    --revert)  MODE=revert ;;
    --to)      NEW_PATH=${2:-}; shift ;;
    -h|--help) sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^#\{1,\} \{0,1\}//'; exit 0 ;;
    *) echo "switch_datahub_path: 未知参数 $1（--help 看用法）" >&2; exit 64 ;;
  esac
  shift
done

BACKUP_DIR=${BACKUP_DIR:-/tmp}
LATEST="$BACKUP_DIR/crontab.datahub_switch.latest"

if [ "$MODE" = revert ]; then
  [ -f "$LATEST" ] || { echo "回滚失败：找不到 $LATEST" >&2; exit 3; }
  crontab "$LATEST" || { echo "回滚失败：crontab 装载失败" >&2; exit 3; }
  echo "已回滚到 $LATEST"
  exit 0
fi

[ -n "$NEW_PATH" ] || { echo "目标路径为空：先设 NEW_PATH 或用 --to 指定" >&2; exit 1; }
[ -d "$NEW_PATH/data_hub" ] || { echo "目标不可用：$NEW_PATH 下没有 data_hub/" >&2; exit 1; }
[ "$NEW_PATH" != "$OLD_PATH" ] || { echo "新旧路径相同，无需切换" >&2; exit 2; }

CUR=$(mktemp "$BACKUP_DIR/crontab.cur.XXXXXX") || exit 1
crontab -l >"$CUR" 2>/dev/null || { echo "读不到当前 crontab" >&2; exit 1; }

hits=$(grep -vE '^[[:space:]]*#' "$CUR" | grep -c -- "$OLD_PATH")
echo "旧路径 $OLD_PATH"
echo "新路径 $NEW_PATH"
echo "非注释条目里引用旧路径的行数：$hits"

if [ "$hits" -eq 0 ]; then
  echo "没有需要改的行（可能已经切过了）"
  exit 2
fi

NEWF=$(mktemp "$BACKUP_DIR/crontab.new.XXXXXX") || exit 1
python3 - "$CUR" "$NEWF" "$OLD_PATH" "$NEW_PATH" <<'PYEOF'
import sys
src, dst, old, new = sys.argv[1:5]
n = 0
out = []
for line in open(src, encoding='utf-8').read().split('\n'):
    if not line.strip().startswith('#') and old in line:
        n += line.count(old)
        line = line.replace(old, new)
    out.append(line)
open(dst, 'w', encoding='utf-8').write('\n'.join(out))
print('替换次数：%d' % n)
PYEOF

echo "--- 改动预览（前 6 行）---"
diff <(grep -vE '^[[:space:]]*#' "$CUR" | grep -v '^$') \
     <(grep -vE '^[[:space:]]*#' "$NEWF" | grep -v '^$') | head -12

if [ "$MODE" != apply ]; then
  echo
  echo "dry-run：没有改动。确认后加 --apply。"
  exit 0
fi

TS=$(date +%Y%m%d_%H%M%S)
BAK="$BACKUP_DIR/crontab.before_datahub_switch.$TS"
cp "$CUR" "$BAK" && cp "$CUR" "$LATEST" || { echo "备份失败，已中止" >&2; exit 1; }
crontab "$NEWF" || { echo "装载新 crontab 失败，原表未动" >&2; exit 1; }

left=$(crontab -l | grep -vE '^[[:space:]]*#' | grep -c -- "$OLD_PATH")
echo "已切换。备份：$BAK"
echo "回滚：bash $0 --revert"
echo "残留旧路径行数：$left"
[ "$left" -eq 0 ] || { echo "警告：仍有残留，请人工检查" >&2; exit 1; }
