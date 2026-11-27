#!/usr/bin/env bash
# 一键启动 / 停止 / 体检 —— 新机器上只有一个入口。
#
# 架构：
#   · TaskManager = 本仓 scheduler.py，一个常驻进程。
#   · 公有任务（产公开数据的）放在 stock-dashboard/case/。
#   · 私有任务放在 precious-task/case/，通过 CASE_EXTRA_DIRS 挂进同一个 TaskManager。
#   · 两边任务各按 task.json 里的 sched（每天/每周、不同时间点）跑，不需要 crontab。
#   · sd_watchdog.sh 以 --loop 常驻，按心跳把挂掉/僵死的 TaskManager 拉起来，
#     同样不需要 crontab。
#
# 新机器迁移（详见 docs/DEPLOY.md §14）：
#   1) 四个仓 clone 到同一父目录：
#        stock-dashboard / precious-task / datahub-multi-tier / options_utility
#   2) ./install.sh               # 一次性录入凭据（缺项拒绝安装）
#   3) ./start_all.sh up          # 一条命令起全部任务
#
# 用法：
#   ./start_all.sh up            启动 TaskManager + 常驻看门狗（幂等）
#   ./start_all.sh up --only k1,k2   本次只跑这几个任务，其余自动当停用（不改配置）
#   ./start_all.sh up --no-api   不监听任何端口，纯后台调度（等价 SD_NO_API=1）
#   ./start_all.sh down          停止看门狗 + TaskManager
#   ./start_all.sh restart [--only k1,k2] [--no-api]
#   ./start_all.sh status        调度状态（公有+私有一起）+ 看门狗/心跳 + 本次过滤
#   ./start_all.sh doctor        只体检不启动；加 --json 输出机器可读结果
#   ./start_all.sh fg [--only k1,k2] [--no-api]   前台跑（调试，Ctrl-C 退出）
#   ./start_all.sh --help
#
# --only 是运行期白名单：只在本次进程内生效，绝不改 modules.json / task.json。
# 已经在跑但过滤条件不同时，up 会先停再起（会打断当时正在跑的任务）；不带 --only
# 再 up 一次即恢复全部。key 写错会直接拒绝启动并列出未知 key。
#
# 可选环境变量：
#   SD_PYTHON=python3           解释器
#   SD_PRECIOUS_DIR=...         私有仓根目录（默认同级 ../precious-task）
#   OPTIONS_UTILITY_ROOT=...    期权算法仓根（默认同级 ../options_utility 或 ../options-utility）
#   DATA_HUB_PATH=...           DataHub 根（默认同级 ../datahub-multi-tier；都没有时 up 直接报错）
#   SD_MODULES_FROM=...         首次启动时从旧机 modules.json 导入本机模块清单（build/macro 等）
#   SD_ONLY=k1,k2               同 up --only（直接 export 也可以）
#   SD_NO_API=1                 同 up --no-api：不监听端口
#   SD_SKIP_LIVE=1              凭据校验跳过联网（离线装机）
#   SD_ALLOW_NO_PRECIOUS=1      私有仓缺失也允许启动（默认拒绝，避免私有任务静默消失）
#   SD_DRY_RUN=1                up 只打印解析结果与计划，不拉起任何进程
#   SD_WATCHDOG_INTERVAL=60     看门狗巡检间隔（秒）
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
LOG_DIR="$HERE/logs"
PY="${SD_PYTHON:-python3}"
WD_PID="$LOG_DIR/watchdog.pid"
WD_INTERVAL="${SD_WATCHDOG_INTERVAL:-60}"

say()  { printf '%s\n' "$*"; }
warn() { printf '[warn] %s\n' "$*" >&2; }
die()  { printf '[error] %s\n' "$*" >&2; exit 1; }

usage() { awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "${BASH_SOURCE[0]}"; }

# ---------------------------------------------------------------- 运行参数
# --only / --no-api 都是「本次进程」的开关：只 export 环境变量，不写任何配置文件。
# 写法：--only a,b   --only=a,b   也接受空格/中文逗号分隔。
parse_run_flags() {
  local raw=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --only)
        [ $# -ge 2 ] || die "--only 后面要跟任务 key（逗号分隔）"
        raw="$2"; shift 2 ;;
      --only=*)
        raw="${1#--only=}"; shift ;;
      --no-api)
        export SD_NO_API=1; shift ;;
      *)
        die "未知参数：$1（./start_all.sh --help）" ;;
    esac
  done
  if [ -n "$raw" ]; then
    SD_ONLY="$(printf '%s' "$raw" \
      | sed 's/，/ /g; s/,/ /g; s/[[:space:]]\{1,\}/\n/g' \
      | awk 'NF && !seen[$0]++ {printf "%s%s", (n++ ? "," : ""), $0}')"
    [ -n "$SD_ONLY" ] || die "--only 没解析出任务 key"
    export SD_ONLY
  fi
}

# 当前进程实际在跑的过滤条件（由 scheduler loop 启动时写入 state.json）。
current_only() {
  # 输出「only 列表|api 是否开启」：--no-api 也是本次运行参数，只比 only 会让
  # 「up 已经在跑 → up --no-api」被当成无变化，旧进程继续监听端口。
  "$PY" - "$HERE/state.json" <<'PYEOF'
import json, sys
try:
    st = json.load(open(sys.argv[1], encoding="utf-8"))
except (OSError, ValueError):
    st = {}
sc = st.get("scheduler") or {}
api = sc.get("api")
print("%s|%s" % (",".join(sc.get("only") or []),
                 "" if api is None else int(bool(api))))
PYEOF
}

sched_running() { "$PY" "$HERE/scheduler.py" health >/dev/null 2>&1; }

# 控制台实际形态：关闭 / host:port。只读 modules.json，不启动任何东西。
api_desc() {
  "$PY" - "$HERE/modules.json" "${SD_NO_API:-0}" <<'PYEOF'
import json, sys
if sys.argv[2] == "1":
    print("关闭"); raise SystemExit
try:
    c = json.load(open(sys.argv[1], encoding="utf-8")).get("api") or {}
except (OSError, ValueError):
    c = {}
if c.get("enabled") is False:
    print("关闭"); raise SystemExit
print("%s:%s" % (c.get("host") or "127.0.0.1", c.get("port") or 8767))
PYEOF
}

# up 的核心语义：启动脚本决定「这一轮跑哪些」。已经在跑且过滤条件不同 → 先停再起，
# 否则 scheduler.py start 只会打印「已在运行」，--only 被静默忽略。
ensure_only_filter() {
  local cur cur_only cur_api want want_api
  cur="$(current_only 2>/dev/null || true)"
  cur_only="${cur%%|*}"
  cur_api="${cur#*|}"
  want="${SD_ONLY:-}"
  want_api=1
  [ "${SD_NO_API:-0}" = "1" ] && want_api=0
  # 老版本 state.json 没有 api 字段（cur_api 为空）：只比 only，不误杀。
  if [ "$want" = "$cur_only" ] && { [ -z "$cur_api" ] || [ "$want_api" = "$cur_api" ]; }; then
    return 0
  fi
  if sched_running; then
    say "过滤条件变化：运行中=${cur_only:-全部}/api=${cur_api:-?} → 本次=${want:-全部}/api=${want_api}，重启 TaskManager 生效"
    if [ "${SD_DRY_RUN:-0}" = 1 ]; then
      say "dry-run：不重启；正式启动会先 down 再 up"
      return 0
    fi
    cmd_down
    sleep 1
  fi
}

# ---------------------------------------------------------------- 路径解析
prepend_path() {  # $1=目录  $2=变量名
  local dir="$1" name="$2" cur
  eval "cur=\${$name:-}"
  case ":$cur:" in
    *":$dir:"*) ;;
    *) eval "export $name=\"$dir\${$name:+:\${$name}}\"" ;;
  esac
}

append_extra_dir() {  # $1=任务目录（不是仓根）
  local dir="$1" real p old_ifs
  [ -n "$dir" ] && [ -d "$dir" ] || return 0
  real="$(cd "$dir" && pwd -P)"
  old_ifs="$IFS"; IFS=':'
  for p in ${CASE_EXTRA_DIRS:-}; do
    [ -n "$p" ] || continue
    if [ "$(cd "$p" 2>/dev/null && pwd -P)" = "$real" ]; then
      IFS="$old_ifs"; return 0
    fi
  done
  IFS="$old_ifs"
  export CASE_EXTRA_DIRS="${CASE_EXTRA_DIRS:+$CASE_EXTRA_DIRS:}$real"
}

resolve_env() {
  case ":$PATH:" in *":/usr/local/bin:"*) ;; *) export PATH="/usr/local/bin:$PATH" ;; esac
  export TZ="${TZ:-Asia/Shanghai}"
  export PYTHONUNBUFFERED=1

  command -v "$PY" >/dev/null 2>&1 || die "找不到解释器 $PY；先装 Python 3.11+"
  "$PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
    || die "Python 版本过低，需要 3.11+：$("$PY" -V 2>&1)"

  # DataHub：与 build.py / scheduler.py / 看门狗同一套解析（env → 同级；不猜云盘旧副本）
  DH="$("$PY" "$HERE/datahub_path.py" 2>/dev/null || true)"
  if [ -n "$DH" ] && [ -d "$DH/data_hub" ]; then
    export DATA_HUB_PATH="${DATA_HUB_PATH:-$DH}"
    export EDB_REPO="${EDB_REPO:-$DH}"
    prepend_path "$DH" PYTHONPATH
    DATAHUB_OK=1
  else
    DATAHUB_OK=0
  fi

  # 私有任务仓（代码在 precious，调度在 dashboard）
  PRIV_DIR="${SD_PRECIOUS_DIR:-${PRECIOUS_DIR:-$HERE/../precious-task}}"
  PRIV_DIR="$(cd "$PRIV_DIR" 2>/dev/null && pwd -P || true)"
  if [ -n "$PRIV_DIR" ] && [ -d "$PRIV_DIR/case" ]; then
    append_extra_dir "$PRIV_DIR/case"
    PRECIOUS_OK=1
  else
    PRIV_DIR=""
    PRECIOUS_OK=0
  fi

  # 期权算法仓：只引用，不复制
  OPT=""
  for c in "${OPTIONS_UTILITY_ROOT:-}" "$HERE/../options_utility" \
           "$HERE/../options-utility"; do
    [ -n "$c" ] || continue
    if [ -f "$c/alert/engine.py" ]; then
      OPT="$(cd "$c" && pwd -P)"
      break
    fi
  done
  if [ -n "$OPT" ]; then
    export OPTIONS_UTILITY_ROOT="$OPT"
    prepend_path "$OPT" PYTHONPATH
    OPTIONS_OK=1
  else
    OPTIONS_OK=0
  fi

  prepend_path "$HERE" PYTHONPATH
}

# ---------------------------------------------------------------- 依赖 / 凭据
ensure_modules_json() {
  if [ -f "$HERE/modules.json" ]; then
    [ -n "${SD_MODULES_FROM:-}" ] && \
      say "modules.json 已存在，忽略 SD_MODULES_FROM（要重新导入请先删掉本机 modules.json）"
    return 0
  fi
  # modules.json 已入库（可移植版）。被误删时先从 git 恢复，别生成空清单 ——
  # 空清单会让 build / macro 等 12 个 producer 静默消失，比启动失败更难发现。
  if [ -d "$HERE/.git" ] && git -C "$HERE" cat-file -e "HEAD:modules.json" 2>/dev/null; then
    if git -C "$HERE" show "HEAD:modules.json" > "$HERE/modules.json" 2>/dev/null; then
      say "modules.json 缺失，已从 git 恢复（HEAD:modules.json）"
      return 0
    fi
  fi
  # 旧机模块清单导入：极老版本的 modules.json 里还有本机绝对路径，用 SD_MODULES_FROM
  # 搬过来后靠 doctor 挑出失效路径（新机器默认不需要这一步）。
  if [ -n "${SD_MODULES_FROM:-}" ]; then
    [ -f "$SD_MODULES_FROM" ] || return 1
    "$PY" - "$SD_MODULES_FROM" <<'PYEOF' || return 1
import json, sys
cfg = json.load(open(sys.argv[1], encoding="utf-8"))
raise SystemExit(0 if isinstance(cfg, dict) and isinstance(cfg.get("modules"), list) else 1)
PYEOF
    cp -f "$SD_MODULES_FROM" "$HERE/modules.json" || return 1
    say "已从 SD_MODULES_FROM 导入 modules.json（旧机模块清单；本机路径用 doctor 核对）"
    return 0
  fi
  "$PY" - "$HERE/modules.json" <<'PYEOF' || return 1
import json, sys
cfg = {
    "version": 2,
    "note": "由 start_all.sh 生成：任务全部来自 case/ + CASE_EXTRA_DIRS；"
            "本文件只放本机 api/queue 配置，不入库。",
    "api": {"host": "127.0.0.1", "port": 8767},
    "queue": {"max_parallel": 1, "pull_forward": True,
              "pull_forward_from": "16:00", "pull_ahead_min": 240},
    "modules": [],
}
with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
    f.write("\n")
PYEOF
  say "已生成 modules.json（空 modules；任务全部来自 case/ + CASE_EXTRA_DIRS）"
}

check_secrets() {
  local out rc
  out="$("$PY" "$HERE/setup.py" --check --file-only ${SD_SKIP_LIVE:+--skip-live} 2>&1)"
  rc=$?
  [ "$rc" -eq 0 ] && return 0
  printf '%s\n' "$out" >&2
  return "$rc"
}

# ---------------------------------------------------------------- 状态采集
doctor_json() {
  "$PY" - "$HERE" "${PRIV_DIR:-}" "${DATA_HUB_PATH:-}" "${OPTIONS_UTILITY_ROOT:-}" \
           "${DATAHUB_OK:-0}" "${PRECIOUS_OK:-0}" "${OPTIONS_OK:-0}" <<'PYEOF'
import json, os, re, sys
root, priv_dir, hub, opt, hub_ok, priv_ok, opt_ok = sys.argv[1:8]
sys.path.insert(0, os.path.join(root, "case", "_lib"))
out = {
    "repo": root,
    "precious": priv_dir or "",
    "datahub": hub or "",
    "options_utility": opt or "",
    "python": "%d.%d.%d" % sys.version_info[:3],
    "paths_ok": {"datahub": hub_ok == "1", "precious": priv_ok == "1",
                 "options_utility": opt_ok == "1"},
    "tasks": {"public": 0, "private": 0, "enabled_public": 0, "enabled_private": 0},
    "modules_cfg": {"count": 0, "enabled": 0},
    "enabled_private": [], "split_errors": [], "errors": [],
}
try:
    with open(os.path.join(root, "modules.json"), encoding="utf-8") as f:
        legacy = (json.load(f) or {}).get("modules") or []
    # 旧机 modules.json 里的路径/占位符在本机很可能失效：启动前把「绝对 cwd 不存在」和
    # 「scheduler 不会展开的占位符」挑出来，避免任务静默跑空。
    # 展开口径必须和 datahub_path.expand_root() 一致：那边加了占位这里不加，
    # doctor 会把合法占位符误报成 unresolved（反之会漏报）。
    try:
        sys.path.insert(0, root)
        import datahub_path as _dp
        _fcli = _dp.feishu_cli_dir(root)
    except Exception:                                         # noqa: BLE001
        _fcli = os.path.expanduser("~/.feishu_cli")
    KNOWN_PH = ("{{DATAHUB_ROOT}}", "{{REPO_ROOT}}", "{{FEISHU_CLI_DIR}}")
    def _expand(v):
        return ((v or "").replace("{{DATAHUB_ROOT}}", hub)
                        .replace("{{REPO_ROOT}}", root)
                        .replace("{{FEISHU_CLI_DIR}}", _fcli))
    bad_cwd, unresolved = [], []
    for m in legacy:
        if not m.get("enabled"):
            continue
        cwd = _expand(m.get("cwd"))
        if cwd and os.path.isabs(cwd) and not os.path.isdir(cwd):
            bad_cwd.append(m.get("key"))
        for ph in sorted(set(re.findall(r"\{\{[A-Z_]+\}\}", _expand(m.get("cmd"))))):
            if ph not in KNOWN_PH:
                unresolved.append("%s:%s" % (m.get("key"), ph))
    out["modules_cfg"] = {"count": len(legacy),
                          "enabled": sum(1 for m in legacy if m.get("enabled")),
                          "bad_cwd": bad_cwd, "unresolved": unresolved}
except (OSError, ValueError):
    pass
try:
    import case_registry
    tasks = case_registry.load_tasks(os.path.join(root, "case"))
except Exception as e:  # noqa: BLE001
    out["errors"].append("扫描任务失败：%s" % e)
    tasks = []
priv_real = os.path.realpath(priv_dir) if priv_dir else ""
for t in tasks:
    vis = case_registry.visibility_of(t)
    d = os.path.realpath(t.get("_dir") or "")
    in_priv = bool(priv_real) and (d == priv_real or d.startswith(priv_real + os.sep))
    if vis == "private":
        out["tasks"]["private"] += 1
        out["tasks"]["enabled_private"] += 1 if t.get("enabled") else 0
        if t.get("enabled"):
            out["enabled_private"].append(t.get("key"))
        if not in_priv:
            out["split_errors"].append(
                "私有任务 %s 在公开仓（应放 precious-task）" % t.get("key"))
    elif vis == "public":
        out["tasks"]["public"] += 1
        out["tasks"]["enabled_public"] += 1 if t.get("enabled") else 0
        if in_priv:
            out["split_errors"].append(
                "公开任务 %s 在私有仓（应放 stock-dashboard）" % t.get("key"))
    else:
        out["errors"].append("任务 %s 的 visibility=%r 非法" % (t.get("key"), vis))
print(json.dumps(out, ensure_ascii=False, indent=2))
PYEOF
}

cmd_doctor() {
  local json=0
  [ "${1:-}" = "--json" ] && json=1
  resolve_env
  local secrets_ok=1
  check_secrets >/dev/null 2>&1 || secrets_ok=0

  if [ "$json" = 1 ]; then
    doctor_json | "$PY" -c '
import json, sys
d = json.load(sys.stdin)
d["secrets_ok"] = sys.argv[1] == "1"
print(json.dumps(d, ensure_ascii=False, indent=2))
' "$secrets_ok"
    return 0
  fi

  say "== 任务管理器部署体检"
  say "  stock-dashboard : $HERE"
  say "  precious-task   : ${PRIV_DIR:-（未找到，私有任务不会挂载）}"
  say "  datahub         : ${DATA_HUB_PATH:-（未找到，任务 import 会失败）}"
  say "  options_utility : ${OPTIONS_UTILITY_ROOT:-（未找到，7 条期权任务会失败）}"
  say "  python          : $("$PY" -V 2>&1)"
  [ "$secrets_ok" = 1 ] && say "  凭据            : 就绪" \
                        || warn "凭据缺失/不合格：先跑 ./install.sh 或 python3 setup.py"
  [ "$PRECIOUS_OK" = 1 ] || warn "私有仓缺失：默认 up 会拒绝启动（SD_ALLOW_NO_PRECIOUS=1 可放行）"
  [ "$DATAHUB_OK" = 1 ] || warn "DataHub 缺失：任务 import data_hub 会失败"
  [ "$OPTIONS_OK" = 1 ] || warn "options_utility 缺失：options_* 7 条任务会报错"

  doctor_json | "$PY" -c '
import json, sys
d = json.load(sys.stdin)
t = d["tasks"]
print()
print("== 任务分布")
print("  公有（dashboard）: %d 条，启用 %d" % (t["public"], t["enabled_public"]))
print("  私有（precious） : %d 条，启用 %d" % (t["private"], t["enabled_private"]))
mc = d.get("modules_cfg") or {}
if mc.get("count"):
    print("  modules.json 旧模块: %d 条，启用 %d（不在 case/，见 DEPLOY §14.2）"
          % (mc["count"], mc.get("enabled", 0)))
    if mc.get("bad_cwd"):
        print("  ! 启用模块的 cwd 在本机不存在: " + ", ".join(mc["bad_cwd"]))
    if mc.get("unresolved"):
        print("  ! scheduler 不会展开的占位符: " + ", ".join(mc["unresolved"]))
else:
    print("  modules.json 旧模块: 无 —— build/macro 等 producer 不会跑，公开看板不会自动刷新")
    print("    换机迁移：SD_MODULES_FROM=/旧机/modules.json ./start_all.sh up（见 DEPLOY §14.2）")
if d["enabled_private"]:
    print("  启用的私有任务   : " + ", ".join(d["enabled_private"]))
if d["split_errors"]:
    print()
    print("== 公私归属失配（违反「公开进 dashboard / 私有进 precious」）")
    for e in d["split_errors"]:
        print("  - " + e)
if d["errors"]:
    print()
    print("== 配置错误")
    for e in d["errors"]:
        print("  - " + e)
'
}

# ---------------------------------------------------------------- 进程管理
watchdog_pid() {
  local pid
  [ -f "$WD_PID" ] || return 1
  pid="$(cat "$WD_PID" 2>/dev/null || true)"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null || return 1
  # 防止 pid 复用后误杀别的进程
  tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -q 'sd_watchdog.sh' || return 1
  printf '%s' "$pid"
}

#: 本仓库常驻看门狗的 pid 列表：pid 文件优先；文件被清掉时按「cwd 在本仓库 +
#: 命令行含 sd_watchdog.sh --loop」兜底，否则 down 会漏杀 —— 漏杀后看门狗会把
#: 刚停掉的调度器又拉回来，表现为「down 了但任务还在跑」。
watchdog_pids() {
  local pid found=0
  if pid="$(watchdog_pid)"; then
    printf '%s\n' "$pid"
    return 0
  fi
  command -v pgrep >/dev/null 2>&1 || return 1
  for pid in $(pgrep -f -- 'sd_watchdog\.sh --loop' 2>/dev/null); do
    [ "$(readlink -f "/proc/$pid/cwd" 2>/dev/null)" = "$HERE" ] || continue
    tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -q 'sd_watchdog.sh' || continue
    printf '%s\n' "$pid"
    found=1
  done
  [ "$found" = 1 ]
}

start_watchdog() {
  local pid
  if pid="$(watchdog_pid)"; then
    say "看门狗已在运行 pid=$pid"
    return 0
  fi
  mkdir -p "$LOG_DIR"
  if command -v setsid >/dev/null 2>&1; then
    setsid nohup env SD_WATCHDOG_INTERVAL="$WD_INTERVAL" \
      bash "$HERE/sd_watchdog.sh" --loop >>"$LOG_DIR/watchdog.log" 2>&1 &
  else
    nohup env SD_WATCHDOG_INTERVAL="$WD_INTERVAL" \
      bash "$HERE/sd_watchdog.sh" --loop >>"$LOG_DIR/watchdog.log" 2>&1 &
  fi
  echo $! > "$WD_PID"
  sleep 1
  if pid="$(watchdog_pid)"; then
    say "看门狗已启动 pid=$pid（每 ${WD_INTERVAL}s 巡检一次）"
    return 0
  fi
  warn "看门狗启动失败，看 $LOG_DIR/watchdog.log"
  return 1
}

stop_watchdog() {
  local pid i pids stopped=""
  if ! pids="$(watchdog_pids)"; then
    rm -f "$WD_PID"
    say "看门狗未运行"
    return 0
  fi
  for pid in $pids; do
    kill -TERM "$pid" 2>/dev/null || true
    for i in $(seq 1 20); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.5
    done
    if kill -0 "$pid" 2>/dev/null; then
      warn "看门狗 pid=$pid 未响应 TERM，强制结束"
      kill -KILL "$pid" 2>/dev/null || true
    fi
    stopped="${stopped:+$stopped,}$pid"
  done
  rm -f "$WD_PID"
  say "看门狗已停止（pid=$stopped）"
}

cmd_up() {
  local API_DESC
  resolve_env
  say "== 任务管理器（公有 dashboard + 私有 precious 同一调度进程）"
  [ "$DATAHUB_OK" = 1 ] || die "找不到 DataHub（需要含 data_hub/ 的仓库）；设 DATA_HUB_PATH 或同级 clone datahub-multi-tier"
  if [ "$PRECIOUS_OK" != 1 ] && [ "${SD_ALLOW_NO_PRECIOUS:-0}" != 1 ]; then
    die "找不到私有仓 $HERE/../precious-task；设 SD_PRECIOUS_DIR，或 SD_ALLOW_NO_PRECIOUS=1 只跑公有任务"
  fi
  [ "$OPTIONS_OK" = 1 ] || warn "找不到 options_utility：options_* 7 条任务会失败（可设 OPTIONS_UTILITY_ROOT）"

  ensure_modules_json || die "生成 modules.json 失败（检查 SD_MODULES_FROM 是否指向合法文件）"
  MOD_N="$("$PY" -c 'import json,sys;print(len((json.load(open(sys.argv[1])).get("modules") or [])))' \
           "$HERE/modules.json" 2>/dev/null || echo 0)"
  if [ "${MOD_N:-0}" -eq 0 ]; then
    warn "modules.json 里没有旧生产模块：build/macro 等 producer 不会跑，公开看板不会刷新"
    warn "换机迁移请设 SD_MODULES_FROM=/旧机/modules.json 后重跑（详见 DEPLOY §14.2）"
  fi
  if ! check_secrets; then
    if [ "${SD_DRY_RUN:-0}" = 1 ]; then
      warn "凭据检查未通过（dry-run 继续展示计划）"
    else
      die "凭据缺失或不合格：先跑 ./install.sh（无终端时 export COZE_SEC_* 后重跑）"
    fi
  fi

  say "  CASE_EXTRA_DIRS = ${CASE_EXTRA_DIRS:-（空）}"
  say "  DATA_HUB_PATH   = ${DATA_HUB_PATH:-（空）}"
  say "  OPTIONS_ROOT    = ${OPTIONS_UTILITY_ROOT:-（空）}"
  say "  本次任务        = ${SD_ONLY:-全部（按各自 sched）}"
  API_DESC="$(api_desc)"
  say "  HTTP 控制台     = $API_DESC"
  case "$API_DESC" in
    0.0.0.0:*)
      warn "控制台监听 0.0.0.0，局域网可访问管理 API；只需本机用请把 modules.json 的 api.host 改成 127.0.0.1" ;;
  esac

  ensure_only_filter

  if [ "${SD_DRY_RUN:-0}" = 1 ]; then
    say ""
    say "dry-run：不拉起进程。正式启动会执行："
    say "  SD_ONLY=${SD_ONLY:-<空>} $PY scheduler.py start"
    say "  setsid nohup bash sd_watchdog.sh --loop"
    cmd_doctor
    return 0
  fi

  mkdir -p "$LOG_DIR"
  "$PY" "$HERE/scheduler.py" start || die "TaskManager 启动失败，看 $LOG_DIR/scheduler.log"
  start_watchdog || warn "TaskManager 已起，但看门狗没起来；挂掉后不会自动恢复"

  say ""
  if [ -n "${SD_ONLY:-}" ]; then
    say "已启动：本次只调度 ${SD_ONLY}，其余任务一律当停用（运行期过滤，未改配置文件）。"
    say "恢复全部：./start_all.sh up（不带 --only）"
  else
    say "已启动。公有 + 私有任务按各自 task.json 的 sched 时间跑，不需要 crontab。"
  fi
  if [ "$API_DESC" = "关闭" ]; then
    say "  控制台  已关闭（纯后台调度，不监听任何端口）"
  else
    say "  控制台  http://127.0.0.1:${API_DESC##*:}（监听 $API_DESC）"
  fi
  say "  状态    ./start_all.sh status"
  say "  日志    $LOG_DIR/scheduler.log"
}

cmd_down() {
  resolve_env
  stop_watchdog
  "$PY" "$HERE/scheduler.py" stop || warn "TaskManager 停止返回非 0，看 $LOG_DIR/scheduler.log"
}

cmd_restart() {
  cmd_down
  sleep 1
  cmd_up
}

cmd_status() {
  resolve_env
  "$PY" "$HERE/scheduler.py" status || true
  say ""
  if pid="$(watchdog_pid)"; then
    say "看门狗：运行中 pid=$pid（间隔 ${WD_INTERVAL}s）"
  else
    say "看门狗：未运行（./start_all.sh up 会拉起）"
  fi
  local hb="$LOG_DIR/scheduler.hb"
  if [ -f "$hb" ]; then
    say "调度心跳：$("$PY" -c 'import os,sys,time;print("%ds 前" % (time.time()-os.path.getmtime(sys.argv[1])))' "$hb")"
  else
    say "调度心跳：无（TaskManager 可能未启动）"
  fi
  cmd_doctor
}

case "${1:-}" in
  up)      shift; parse_run_flags "$@"; cmd_up ;;
  down)    cmd_down ;;
  restart) shift; parse_run_flags "$@"; cmd_restart ;;
  status)  cmd_status ;;
  doctor)  shift; cmd_doctor "${1:-}" ;;
  fg)      shift; parse_run_flags "$@"; resolve_env; exec "$PY" "$HERE/scheduler.py" fg ;;
  -h|--help|help) usage ;;
  *) usage; exit 1 ;;
esac
