#!/usr/bin/env bash
# test_sync_to_drive.sh —— sync_to_drive.sh 的自测（纯 bash + rsync + flock）。
#
# 覆盖：
#   a) 正常同步：该同步的同步过去了
#   b) 排除项没过去（logs/ __pycache__ *.pyc .git/ state.json scheduler.pid *.tmp *.bak* out/*.png）
#   c) --dry-run 不写盘
#   d) 只有 --prune 才删目标上多出来的东西
#   e) 目标不可用（不可创建）时退出码非 0
#   f) flock 并发：第二个实例跳过（退出码 0 + 日志写 SKIP）
#
# 用法：bash scripts/test_sync_to_drive.sh [-v]
# 退出码：0 全部 PASS；1 有 FAIL。

set -uo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)
SUT="$SCRIPT_DIR/sync_to_drive.sh"
VERBOSE=0
[ "${1:-}" = "-v" ] && VERBOSE=1

command -v rsync >/dev/null 2>&1 || { echo "test: 缺 rsync，无法测试" >&2; exit 1; }
command -v flock >/dev/null 2>&1 || { echo "test: 缺 flock，无法测试" >&2; exit 1; }
[ -f "$SUT" ] || { echo "test: 找不到被测脚本 $SUT" >&2; exit 1; }

PASS=0
FAIL=0
FAILED_NAMES=()

ok()   { PASS=$((PASS + 1)); printf '  PASS  %s\n' "$1"; }
bad()  { FAIL=$((FAIL + 1)); FAILED_NAMES+=("$1"); printf '  FAIL  %s\n' "$1"; [ -n "${2:-}" ] && printf '        %s\n' "$2"; }
check() { # check <desc> <ok:0|1> [detail]
  if [ "$2" -eq 0 ]; then ok "$1"; else bad "$1" "${3:-}"; fi
}

WORK=$(mktemp -d "${TMPDIR:-/tmp}/sd_synctest.XXXXXX") || exit 1
cleanup() { rm -rf -- "$WORK"; }
trap cleanup EXIT

# ---------- 造源目录 ----------
make_src() { # make_src <dir>
  local d=$1
  mkdir -p "$d"/{case/foo/out,case/foo/logs,deep/__pycache__,.git/logs,logs}
  printf 'a\n'        >"$d/README.md"
  printf 'code\n'     >"$d/scheduler.py"
  printf 'r\n'        >"$d/case/foo/report.json"
  printf 'big\n'      >"$d/case/foo/out/chart.png"
  printf 'data\n'     >"$d/case/foo/out/table.csv"
  # 应被排除
  printf 'x\n'        >"$d/logs/tick.log"
  printf 'x\n'        >"$d/case/foo/logs/run.log"
  printf 'x\n'        >"$d/deep/__pycache__/m.cpython-311.pyc"
  printf 'x\n'        >"$d/deep/mod.pyc"
  printf 'x\n'        >"$d/.git/logs/HEAD"
  printf '{"x":1}\n'  >"$d/state.json"
  printf '123\n'      >"$d/scheduler.pid"
  printf 'x\n'        >"$d/tmpfile.tmp"
  printf 'x\n'        >"$d/config.json.bak_20260101"
  printf 'x\n'        >"$d/config.json.bak"
}

run() { # run <src> <dst> [args...] -> sets RC / OUT / ERR
  local src=$1 dst=$2; shift 2
  OUT=$(SD_LOCAL_DIR="$src" SD_DRIVE_DIR="$dst" bash "$SUT" "$@" 2>"$WORK/stderr.txt")
  RC=$?
  ERR=$(cat "$WORK/stderr.txt")
  [ "$VERBOSE" -eq 1 ] && printf '    [cmd] %s %s\n    [rc=%s] %s\n' "$src" "$*" "$RC" "$OUT"
  return 0
}

echo "== 1) 正常同步 + 排除项 =="
SRC1="$WORK/src1"; DST1="$WORK/dst1"; mkdir -p "$SRC1" "$DST1"
make_src "$SRC1"
run "$SRC1" "$DST1"
check "退出码 0" "$([ "$RC" -eq 0 ] && echo 0 || echo 1)" "rc=$RC err=$ERR"
check "README.md 同步过去"        "$([ -f "$DST1/README.md" ] && echo 0 || echo 1)"
check "scheduler.py 同步过去"     "$([ -f "$DST1/scheduler.py" ] && echo 0 || echo 1)"
check "嵌套 report.json 同步过去" "$([ -f "$DST1/case/foo/report.json" ] && echo 0 || echo 1)"
check "out/ 下 csv 同步过去"      "$([ -f "$DST1/case/foo/out/table.csv" ] && echo 0 || echo 1)"

check "logs/ 被排除"              "$([ ! -e "$DST1/logs/tick.log" ] && echo 0 || echo 1)"
check "case/*/logs/ 被排除"       "$([ ! -e "$DST1/case/foo/logs/run.log" ] && echo 0 || echo 1)"
check "__pycache__/ 被排除"       "$([ ! -e "$DST1/deep/__pycache__/m.cpython-311.pyc" ] && echo 0 || echo 1)"
check "*.pyc 被排除"              "$([ ! -e "$DST1/deep/mod.pyc" ] && echo 0 || echo 1)"
check ".git/ 被排除"              "$([ ! -e "$DST1/.git/logs/HEAD" ] && echo 0 || echo 1)"
check "state.json 被排除"         "$([ ! -e "$DST1/state.json" ] && echo 0 || echo 1)"
check "scheduler.pid 被排除"      "$([ ! -e "$DST1/scheduler.pid" ] && echo 0 || echo 1)"
check "*.tmp 被排除"              "$([ ! -e "$DST1/tmpfile.tmp" ] && echo 0 || echo 1)"
check "*.bak* 被排除(带后缀)"     "$([ ! -e "$DST1/config.json.bak_20260101" ] && echo 0 || echo 1)"
check "*.bak* 被排除(纯 .bak)"    "$([ ! -e "$DST1/config.json.bak" ] && echo 0 || echo 1)"
check "out/*.png 被排除"          "$([ ! -e "$DST1/case/foo/out/chart.png" ] && echo 0 || echo 1)"
check "摘要含「已同步」"          "$(printf '%s' "$OUT" | grep -q '已同步' && echo 0 || echo 1)" "out=$OUT"

echo "== 2) --dry-run 不写盘 =="
SRC2="$WORK/src2"; DST2="$WORK/dst2"; mkdir -p "$SRC2" "$DST2"
make_src "$SRC2"
run "$SRC2" "$DST2" --dry-run
check "退出码 0" "$([ "$RC" -eq 0 ] && echo 0 || echo 1)" "rc=$RC err=$ERR"
check "目标仍是空的（只允许 logs/ 这种被脚本自己建的）" \
  "$([ -z "$(find "$DST2" -mindepth 1 -maxdepth 1 -not -name '*.lock*' -print -quit)" ] && echo 0 || echo 1)" \
  "$(find "$DST2" -mindepth 1 | head -5)"
check "README.md 未被写入"        "$([ ! -e "$DST2/README.md" ] && echo 0 || echo 1)"
check "摘要含 [dry-run]"          "$(printf '%s' "$OUT" | grep -q '\[dry-run\]' && echo 0 || echo 1)" "out=$OUT"

echo "== 3) 默认不删 / --prune 才删 =="
SRC3="$WORK/src3"; DST3="$WORK/dst3"; mkdir -p "$SRC3" "$DST3"
make_src "$SRC3"
printf 'stale\n' >"$DST3/手工放的东西.md"
run "$SRC3" "$DST3"
check "默认同步退出码 0" "$([ "$RC" -eq 0 ] && echo 0 || echo 1)" "rc=$RC err=$ERR"
check "默认不删目标多余文件" "$([ -f "$DST3/手工放的东西.md" ] && echo 0 || echo 1)"
run "$SRC3" "$DST3" --prune
check "--prune 退出码 0" "$([ "$RC" -eq 0 ] && echo 0 || echo 1)" "rc=$RC err=$ERR"
check "--prune 删掉目标多余文件" "$([ ! -e "$DST3/手工放的东西.md" ] && echo 0 || echo 1)"

echo "== 4) 目标不可用时退出码非 0 =="
SRC4="$WORK/src4"; mkdir -p "$SRC4"; make_src "$SRC4"
printf 'not a dir\n' >"$WORK/blocker"
run "$SRC4" "$WORK/blocker/sub"
check "退出码非 0" "$([ "$RC" -ne 0 ] && echo 0 || echo 1)" "rc=$RC out=$OUT"
check "退出码为 1（路径不可用，不是 3）" "$([ "$RC" -eq 1 ] && echo 0 || echo 1)" "rc=$RC err=$ERR"
check "stderr 有明确一行" "$([ -n "$ERR" ] && echo 0 || echo 1)" "stderr 空"

echo "== 5) flock 并发：第二个跳过 =="
SRC5="$WORK/src5"; DST5="$WORK/dst5"; mkdir -p "$SRC5" "$DST5"
make_src "$SRC5"
mkdir -p "$SRC5/logs"
LOCK="$SRC5/logs/.sync_to_drive.lock"
(
  exec 9>"$LOCK"
  flock -x 9
  sleep 4
) &
HOLDER=$!
sleep 0.6
run "$SRC5" "$DST5"
check "跳过时退出码 0" "$([ "$RC" -eq 0 ] && echo 0 || echo 1)" "rc=$RC err=$ERR"
check "输出提示 skipped" "$(printf '%s' "$OUT" | grep -qi 'skip' && echo 0 || echo 1)" "out=$OUT"
check "日志里有 SKIP 记录" \
  "$(grep -qi 'SKIP' "$SRC5/logs/sync_to_drive.log" 2>/dev/null && echo 0 || echo 1)" \
  "$([ -f "$SRC5/logs/sync_to_drive.log" ] && tail -3 "$SRC5/logs/sync_to_drive.log" || echo '日志文件还没生成')"
wait "$HOLDER" 2>/dev/null
run "$SRC5" "$DST5"
check "锁释放后再跑正常同步" "$([ "$RC" -eq 0 ] && [ -f "$DST5/README.md" ] && echo 0 || echo 1)" "rc=$RC err=$ERR"

echo "== 6) 参数错误 / 帮助 =="
OUT=$(bash "$SUT" --nope 2>&1); RC=$?
check "未知参数退出码 64" "$([ "$RC" -eq 64 ] && echo 0 || echo 1)" "rc=$RC out=$OUT"
OUT=$(bash "$SUT" --help 2>&1); RC=$?
check "--help 退出码 0 且有用法" \
  "$([ "$RC" -eq 0 ] && printf '%s' "$OUT" | grep -q 'SD_DRIVE_DIR' && echo 0 || echo 1)" "rc=$RC"

echo
echo "==================== 汇总 ===================="
printf 'PASS: %d    FAIL: %d\n' "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
  printf '失败项：\n'
  for n in "${FAILED_NAMES[@]}"; do printf '  - %s\n' "$n"; done
  exit 1
fi
echo "ALL PASS"
exit 0
