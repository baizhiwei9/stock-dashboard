#!/usr/bin/env bash
# ============================================================
# start_all.sh — 换机器后，一条命令把全部定时任务拉起来
# ============================================================
# 主人 2026-11-26 21:55：
#   「换个机器，一个 Task Manager 来管理它。也就是说我一个启动脚本把它们都启动
#     起来……你要只要能瞬动的迁移，就一个脚本启动起来就行了。所有的任务都放在这
#     两个里边，公有的数据产生放在 Dashboard，私有的放在 Precious。」
#
# 所以这个脚本是**换机器后的唯一入口**。不需要 crontab，不需要手工逐个起。
#
# 起两个调度器（各管各的，但对外只有这一条命令）：
#   1. datahub-multi-tier 的 data_hub.task_manager → 数据采集与维护
#      （EDB / persist / nowcast / 缓存与库维护，任务清单在 data_hub/task_manager.py）
#   2. stock-dashboard 的 scheduler.py            → case 日报
#      （21 个日更/周更任务，任务清单在 case/<key>/task.json，enabled=true 才跑）
#
# 用法：
#   bash scripts/start_all.sh            # 启动（幂等，已在跑就跳过）
#   bash scripts/start_all.sh status     # 两个调度器的状态
#   bash scripts/start_all.sh check      # 健康检查，非 0 = 有问题（可接监控）
#   bash scripts/start_all.sh stop       # 全停
#   bash scripts/start_all.sh restart    # 全重启
#
# 环境变量：
#   DATA_HUB_PATH    datahub-multi-tier 仓库路径；不设时自动在 SD 同级 / 上级找
#   SKIP_DATAHUB=1   只起日报调度器，跳过 data_hub（调试用）
# ============================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE" || exit 1
SD_ROOT="$HERE"

C_OK="\033[32m"; C_WARN="\033[33m"; C_ERR="\033[31m"; C_DIM="\033[2m"; C_END="\033[0m"
ok()   { printf "  ${C_OK}✅${C_END} %s\n" "$*"; }
warn() { printf "  ${C_WARN}⚠${C_END}  %s\n" "$*"; }
err()  { printf "  ${C_ERR}❌${C_END} %s\n" "$*"; }
dim()  { printf "  ${C_DIM}%s${C_END}\n" "$*"; }
hr()   { printf "\n${C_DIM}%s${C_END}\n" "────────────────────────────────────────────────────────"; }

# ---------- 找 datahub-multi-tier ----------
find_dh() {
  local cand
  for cand in \
      "${DATA_HUB_PATH:-}" \
      "$SD_ROOT/../datahub-multi-tier" \
      "$SD_ROOT/../../datahub-multi-tier" \
      "/opt/data/datahub-multi-tier" \
      "/opt/coze/.fuse_drive/dataHub_三级体系"; do
    if [ -n "$cand" ] && [ -d "$cand" ] && [ -f "$cand/data_hub/task_manager.py" ]; then
      (cd "$cand" && pwd); return 0
    fi
  done
  return 1
}

# ---------- 环境自检 ----------
preflight() {
  hr; echo "== 环境自检 =="
  local bad=0

  if [ -f "$SD_ROOT/scheduler.py" ] && [ -d "$SD_ROOT/case" ]; then
    ok "stock-dashboard   $SD_ROOT"
  else
    err "stock-dashboard 结构不完整（缺 scheduler.py 或 case/）：$SD_ROOT"; bad=$((bad+1))
  fi

  DH_ROOT="$(find_dh || true)"
  if [ -n "${DH_ROOT:-}" ]; then
    ok "datahub-multi-tier $DH_ROOT"
  else
    warn "没找到 datahub-multi-tier —— 数据采集类任务不会跑"
    warn "  设 DATA_HUB_PATH=<路径> 或把两仓放成同级目录"
  fi

  if [ -n "${DH_ROOT:-}" ]; then
    if [ -f "$DH_ROOT/options/config/private.py" ]; then
      ok "凭据 options/config/private.py"
    else
      warn "缺 $DH_ROOT/options/config/private.py（照 private.example.py 填）"
    fi
  fi

  # 最容易踩的坑：crontab 里还留着老行 → 和新调度器双跑
  local dup
  dup="$(crontab -l 2>/dev/null | grep -c 'health_run --name' || true)"
  if [ "${dup:-0}" -gt 0 ]; then
    warn "crontab 里还有 $dup 条 health_run --name 老任务 → 会和调度器双跑"
    warn "  跑一次：bash scripts/switch_crontab.sh          # 先 dry-run 看清单"
    warn "          bash scripts/switch_crontab.sh --apply  # 确认后真切"
  else
    ok "crontab 无重复任务"
  fi

  return $bad
}

# ---------- 日报调度器 ----------
sd_start()   { python3 scheduler.py start; }
sd_stop()    { python3 scheduler.py stop; }
sd_status()  { python3 scheduler.py status; }
sd_health()  { python3 scheduler.py health; }

# ---------- 数据采集调度器 ----------
dh_start() {
  [ -n "${DH_ROOT:-}" ] || { warn "跳过 data_hub（仓库未找到）"; return 0; }
  bash "$DH_ROOT/scripts/taskmanager_boot.sh" start
}
dh_stop() {
  [ -n "${DH_ROOT:-}" ] || return 0
  bash "$DH_ROOT/scripts/taskmanager_boot.sh" stop
}
dh_status() {
  [ -n "${DH_ROOT:-}" ] || { warn "跳过 data_hub（仓库未找到）"; return 0; }
  bash "$DH_ROOT/scripts/taskmanager_boot.sh" status
}
dh_check() {
  [ -n "${DH_ROOT:-}" ] || return 0
  bash "$DH_ROOT/scripts/taskmanager_boot.sh" check
}

# ---------- 主流程 ----------
CMD="${1:-start}"
DH_ROOT="$(find_dh || true)"

case "$CMD" in
  start)
    preflight || true
    hr; echo "== 启动 =="
    if [ "${SKIP_DATAHUB:-0}" != "1" ]; then
      echo "-- 数据采集调度器 --"; dh_start
    fi
    echo "-- 日报调度器 --"; sd_start
    hr; echo "== 结果 =="
    sd_health; rc=$?
    case $rc in
      0) ok "日报调度器健康" ;;
      1) err "日报调度器未运行" ;;
      2) err "日报调度器僵死（跑 restart）" ;;
    esac
    echo
    dim "看任务清单：python3 scheduler.py status"
    dim "手动跑一个：python3 scheduler.py run <key>"
    dim "任务开关：  python3 scheduler.py on|off <key>"
    exit $rc
    ;;
  status)
    hr; echo "== 状态 =="
    echo "-- 日报调度器 --"; sd_status
    echo; echo "-- 数据采集调度器 --"; dh_status
    ;;
  check)
    sd_health; rc=$?
    dh_check || rc=$(( rc ? rc : 1 ))
    if [ $rc -eq 0 ]; then ok "全部健康"; else err "有不健康项（rc=$rc）"; fi
    exit $rc
    ;;
  stop)
    hr; echo "== 停止 =="
    echo "-- 日报调度器 --"; sd_stop
    echo "-- 数据采集调度器 --"; dh_stop
    ;;
  restart)
    "$0" stop; sleep 1; exec "$0" start
    ;;
  *)
    echo "用法: $0 [start|stop|restart|status|check]"; exit 2 ;;
esac
