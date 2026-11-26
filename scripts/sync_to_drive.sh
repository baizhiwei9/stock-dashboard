#!/usr/bin/env bash
# sync_to_drive.sh —— 把本机部署目录同步到云盘备份目录（云盘只当备份）。
#
# 背景：stock-dashboard 直接跑在 FUSE 云盘上时 listdir 极慢（case/ 目录 0.6~1.3s，
# _case_modules() 5~10s），tick 实际周期被拖到 29s、/api/state 9~12s。
# 现改为「代码和运行时数据放本机盘跑，云盘只当备份」，本脚本就是那条备份链路。
#
# 用法：
#   bash scripts/sync_to_drive.sh                 # 手动同步一次
#   bash scripts/sync_to_drive.sh --dry-run       # 只打印会传什么，不写盘
#   */30 * * * * SD_LOCAL_DIR=/opt/stock-dashboard \
#     SD_DRIVE_DIR='/Coze/Drive/股市监控画布/backup' \
#     bash /opt/stock-dashboard/scripts/sync_to_drive.sh
#
# 环境变量：
#   SD_LOCAL_DIR      本机部署根（默认：脚本所在仓库根，即 scripts/ 的上一级）
#   SD_DRIVE_DIR      云盘备份目录（默认：/Coze/Drive/股市监控画布/backup）
#   SD_LOG_FILE       日志（默认：$SD_LOCAL_DIR/logs/sync_to_drive.log）
#   SD_MAX_LOG_BYTES  日志轮转阈值（默认 5MB）
#
# 参数：
#   -n, --dry-run   透传 rsync -n -i，只打印不写盘
#   --prune         额外加 --delete（默认不加：备份盘上手工放的东西不该被删）
#   -v, --verbose   打印 rsync -i 逐文件明细
#   -h, --help      本帮助
#
# 退出码：0 成功（含「上一轮未完成，本轮跳过」）；1 路径/目标不可用；3 rsync 失败；
#         4 缺少 rsync/flock；64 参数错误。
#
# 日志：追加写 $SD_LOG_FILE；单文件超 SD_MAX_LOG_BYTES 就 mv 成 .<时间戳> 轮转一次。

set -uo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)
SD_LOCAL_DIR=${SD_LOCAL_DIR:-$(cd -- "$SCRIPT_DIR/.." >/dev/null 2>&1 && pwd -P)}
SD_DRIVE_DIR=${SD_DRIVE_DIR:-/Coze/Drive/股市监控画布/backup}
SD_LOG_FILE=${SD_LOG_FILE:-$SD_LOCAL_DIR/logs/sync_to_drive.log}
SD_MAX_LOG_BYTES=${SD_MAX_LOG_BYTES:-$((5 * 1024 * 1024))}

# ===== 排除列表（要改就改这一段）=====
#  - /logs/ 锚定源根（只排根下的 logs/）；logs/ 再列一次，排掉 case/*/logs/ 这类嵌套日志。
#  - state.json / scheduler.pid 是运行状态，备份过去只会被下一轮覆盖。
#  - out/ 下的大图占了体积绝大部分（报告配图），单独按目录 + 扩展名排掉。
EXCLUDES=(
  --exclude=/logs/
  --exclude=logs/
  --exclude=__pycache__/
  --exclude='*.pyc'
  --exclude=.git/
  --exclude=state.json
  --exclude=scheduler.pid
  --exclude='*.tmp'
  --exclude='*.bak*'
  --exclude=node_modules/
  --exclude='out/*.png'
  --exclude='out/*.jpg'
  --exclude='out/*.jpeg'
  --exclude='out/*.webp'
  --exclude='out/*.gif'
  --exclude='out/*.pdf'
  # 原始抓取缓存：case/<task>/data/db 与 data/md 是逐日 CSV 原件（margin 两个任务就占 230MB），
  # 全是网上抓的、可重抓，不进备份。data/ 下的汇总 csv/json 仍然备份。
  --exclude='*/data/db/'
  --exclude='*/data/md/'
  --exclude=catboost_info/
  --exclude='preview-*.png'
)

DRY_RUN=0
PRUNE=0
VERBOSE=0

usage() {
  sed -n '2,32p' "${BASH_SOURCE[0]}" | sed 's/^#\{1,\} \{0,1\}//'
}

while [ $# -gt 0 ]; do
  case "$1" in
    -n|--dry-run) DRY_RUN=1 ;;
    --prune)      PRUNE=1 ;;
    -v|--verbose) VERBOSE=1 ;;
    -h|--help)    usage; exit 0 ;;
    *) echo "sync_to_drive: 未知参数：$1（--help 看用法）" >&2; exit 64 ;;
  esac
  shift
done

# ---------- 日志 ----------
log_dir=$(dirname -- "$SD_LOG_FILE")
mkdir -p -- "$log_dir" 2>/dev/null || true

rotate_log() {
  [ -f "$SD_LOG_FILE" ] || return 0
  local size
  size=$(wc -c <"$SD_LOG_FILE" 2>/dev/null || echo 0)
  [ "${size:-0}" -gt "$SD_MAX_LOG_BYTES" ] || return 0
  mv -f -- "$SD_LOG_FILE" "$SD_LOG_FILE.$(date +%Y%m%d_%H%M%S)" 2>/dev/null || true
}

log_line() {
  rotate_log
  printf '%s %s\n' "$(date '+%F %T')" "$*" >>"$SD_LOG_FILE" 2>/dev/null || true
}

die() { # die <exit_code> <message>
  log_line "ERROR(exit=$1) $2"
  echo "sync_to_drive: $2" >&2
  exit "$1"
}

# ---------- 前置检查 ----------
command -v rsync >/dev/null 2>&1 || die 4 "找不到 rsync（apt install rsync / brew install rsync）"
command -v flock >/dev/null 2>&1 || die 4 "找不到 flock（util-linux 提供的文件锁）"
[ -d "$SD_LOCAL_DIR" ] || die 1 "源目录不存在：$SD_LOCAL_DIR"

src_rp=$(cd -- "$SD_LOCAL_DIR" && pwd -P)
case "$SD_DRIVE_DIR" in
  "$src_rp"|"$src_rp"/*) die 1 "目标目录位于源目录内部（$SD_DRIVE_DIR），会递归同步，拒绝执行" ;;
esac

# ---------- 并发锁（上一轮没跑完就跳过）----------
mkdir -p -- "$src_rp/logs" 2>/dev/null || true
LOCK_FILE="$src_rp/logs/.sync_to_drive.lock"
# 注意：这里不能用 `exec 9>"$f" 2>/dev/null`——exec 只带重定向时会把 2>/dev/null
# 永久作用到当前 shell，后面所有 die/echo >&2 都会被吞掉（实测踩过）。
exec 9>"$LOCK_FILE" || die 1 "无法创建锁文件：$LOCK_FILE"
if ! flock -n 9; then
  log_line "SKIP 上一轮同步仍在运行（锁被占用），本轮跳过 local=$src_rp drive=$SD_DRIVE_DIR"
  echo "sync_to_drive: skipped（上一轮还没跑完）"
  exit 0
fi

# ---------- 目标目录 ----------
if [ ! -d "$SD_DRIVE_DIR" ]; then
  if [ "$DRY_RUN" -eq 1 ]; then
    die 1 "目标目录不存在：$SD_DRIVE_DIR（--dry-run 不创建，请先建好再试）"
  fi
  mkdir -p -- "$SD_DRIVE_DIR" 2>/dev/null \
    || die 1 "云盘目标目录不可用：$SD_DRIVE_DIR（不存在且 mkdir 失败）"
fi
if [ "$DRY_RUN" -eq 0 ] && [ ! -w "$SD_DRIVE_DIR" ]; then
  die 1 "云盘目标目录不可写：$SD_DRIVE_DIR"
fi

# ---------- 组装参数 ----------
RSYNC_ARGS=(-a --stats --no-human-readable)
RSYNC_ARGS+=("${EXCLUDES[@]}")
[ "$PRUNE" -eq 1 ] && RSYNC_ARGS+=(--delete)
if [ "$DRY_RUN" -eq 1 ]; then
  RSYNC_ARGS+=(-n -i)
elif [ "$VERBOSE" -eq 1 ]; then
  RSYNC_ARGS+=(-i)
fi

# ---------- 执行 ----------
START=$SECONDS
ERRF=$(mktemp "${TMPDIR:-/tmp}/sync_to_drive.err.XXXXXX") || die 1 "mktemp 失败"
STATS=$(rsync "${RSYNC_ARGS[@]}" "$src_rp/" "$SD_DRIVE_DIR/" 2>"$ERRF")
RC=$?
ELAPSED=$((SECONDS - START))

if [ "$RC" -ne 0 ]; then
  first_err=$(head -c 300 "$ERRF" 2>/dev/null | tr '\n' ' ')
  rm -f -- "$ERRF"
  log_line "ERROR(exit=3) rsync rc=$RC target=$SD_DRIVE_DIR detail=${first_err:-（无 stderr）}"
  echo "sync_to_drive: rsync 失败 rc=$RC：${first_err:-未知错误}" >&2
  exit 3
fi
rm -f -- "$ERRF"

# ---------- 摘要 ----------
# --dry-run 把 rsync -i 的逐条明细打出来（要“透传 -i”的效果），统计块由下面的摘要代替。
if [ "$DRY_RUN" -eq 1 ] && [ -n "$STATS" ]; then
  printf '%s\n' "$STATS" | grep -E '^(\*deleting|\*cdn|\.d\.\.|cL|cS|cd|hf|>f|>d|<f|h\.)' || true
fi

nfiles=$(printf '%s\n' "$STATS" | awk -F': ' '/^Number of regular files transferred:/{gsub(/,/,"",$2); print $2+0; exit}')
nbytes=$(printf '%s\n' "$STATS" | awk -F': ' '/^Total transferred file size:/{gsub(/,/,"",$2); sub(/ bytes.*/,"",$2); print $2+0; exit}')
[ -z "${nfiles:-}" ] && nfiles=0
[ -z "${nbytes:-}" ] && nbytes=0

human_bytes=$(awk -v b="$nbytes" 'BEGIN{
  split("B KB MB GB TB", u, " "); i=1;
  while (b >= 1024 && i < 5) { b /= 1024; i++ }
  printf (i == 1 ? "%.0f%s" : "%.1f%s"), b, u[i];
}')

if [ "$DRY_RUN" -eq 1 ]; then
  printf 'sync_to_drive: [dry-run] 将同步 %s 个文件 / %s / 耗时 %ss / 目标 %s\n' \
    "$nfiles" "$human_bytes" "$ELAPSED" "$SD_DRIVE_DIR"
  log_line "OK(dry-run) files=$nfiles bytes=$nbytes elapsed=${ELAPSED}s target=$SD_DRIVE_DIR prune=$PRUNE"
else
  printf 'sync_to_drive: 已同步 %s 个文件 / %s / 耗时 %ss / 目标 %s\n' \
    "$nfiles" "$human_bytes" "$ELAPSED" "$SD_DRIVE_DIR"
  log_line "OK files=$nfiles bytes=$nbytes elapsed=${ELAPSED}s target=$SD_DRIVE_DIR prune=$PRUNE"
fi

exit 0
