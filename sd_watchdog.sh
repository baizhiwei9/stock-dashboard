#!/bin/bash
# scheduler 看门狗。
#
# 职责只有一个 —— 保证 scheduler 常驻进程「真的在调度」；业务任务仍全部由
# scheduler.py 决定，看门狗不直接跑任何业务任务。
#
# 两种跑法（二选一）：
#   1) 常驻循环（推荐，新机器）：./start_all.sh up 会拉起
#        bash sd_watchdog.sh --loop
#      每 SD_WATCHDOG_INTERVAL 秒（默认 60）自检一次，不依赖 crontab；
#      pid 写 logs/watchdog.pid，由 start_all.sh down/status 管理。
#   2) 老部署的 crontab，每分钟调一次（无参数 = 跑一遍就退出）：
#        * * * * * /path/to/stock-dashboard/sd_watchdog.sh >/dev/null 2>&1
#
# 判定依据是 scheduler.py 每 tick 写的心跳文件 logs/scheduler.hb（3 分钟过期），
# 不是「pid 在不在」：进程活着但调度循环卡死（僵死）时，pid 判定会漏掉。
# 退出码语义由 `scheduler.py health` 给出：0=健康 / 1=未运行 / 2=僵死。
#
# 可选环境变量：
#   SD_PYTHON        指定解释器（默认 python3）
#   SD_WATCHDOG_INTERVAL --loop 模式下的巡检间隔秒数（默认 60）
#   SD_ALERT_WEBHOOK 连续拉不起来时的告警 webhook，留空则去凭据文件找 alert.webhook，
#                    都没有就只写日志（不静默失败，也不外发）
#   SD_ALERT_AT      连续失败几次才告警（默认 3）
#   SD_CASE_EXTRA_DIRS 私有任务目录（默认 ../precious-task/case），导出成
#                    CASE_EXTRA_DIRS 交给 scheduler；不存在则跳过
#
# 落盘（都在 stock-dashboard/logs/）：
#   watchdog.log   拉起/重启/失败记录（健康时不写，避免每分钟一行噪音）
#   watchdog.hb    最后一次成功巡检的 epoch 秒 —— 用来确认 cron 这行还在跑
#   watchdog.fail  连续拉起失败计数（成功即清零）
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE" || exit 1
mkdir -p logs

# 常驻模式：--loop 时每 SD_WATCHDOG_INTERVAL 秒调自己一次（--once）。
# 不依赖 crontab；pid 写 logs/watchdog.pid，退出时自动清掉。
if [ "${1:-}" = "--loop" ]; then
  echo $$ > logs/watchdog.pid
  trap 'rm -f logs/watchdog.pid; exit 0' TERM INT EXIT
  while :; do
    "$HERE/sd_watchdog.sh" --once || true
    # 用后台 sleep + wait，收到 TERM 能立刻中断，不必等满一个间隔
    sleep "${SD_WATCHDOG_INTERVAL:-60}" & wait $! || true
  done
fi
[ "${1:-}" = "--once" ] && shift

PY="${SD_PYTHON:-python3}"
LOG="logs/watchdog.log"
FAILF="logs/watchdog.fail"
ALERT_AT="${SD_ALERT_AT:-3}"
TS="$(date '+%Y-%m-%d %H:%M:%S')"

# 环境自举：cron 的 env 极简，PYTHONPATH 一般为空。DataHub 根统一交给
# datahub_path.py 解析（DATA_HUB_PATH → DATAHUB_DIR → EDB_REPO → 同级 checkout
# → /opt 兜底），与 scheduler.py 用的是同一份逻辑；补进 PYTHONPATH 后，
# scheduler 内的 task_health 上报和任务子进程看到的是同一个根，不会出现
# 「看门狗认为 datahub 在、子进程 import 失败」。已有的 PYTHONPATH 只增不覆盖。
DH="$("$PY" "$HERE/datahub_path.py" 2>/dev/null || true)"
if [ -n "$DH" ]; then
  case ":${PYTHONPATH:-}:" in
    *":$DH:"*) ;;
    *) export PYTHONPATH="$DH${PYTHONPATH:+:$PYTHONPATH}" ;;
  esac
  export EDB_REPO="${EDB_REPO:-$DH}"
fi

# 私有任务目录：调度器只认环境变量 CASE_EXTRA_DIRS（见 case/CONTRACT.md §2），
# cron 的 env 里没有这一行，私有仓的 case/ 就永远挂不进来 —— 表现是「任务在私有仓
# 里配好了，但调度台里根本没有它」。默认取公开仓同级的 precious-task/case（标准布局），
# 目录不存在就不设，不制造空路径；已显式设过的（SD_CASE_EXTRA_DIRS / CASE_EXTRA_DIRS）
# 只追加不覆盖，多目录用冒号分隔。
PRIV_CASE="${SD_CASE_EXTRA_DIRS:-$HERE/../precious-task/case}"
if [ -d "$PRIV_CASE" ]; then
  # 归一成物理路径再比：$HERE/../precious-task/case 与 /Coze/.../precious-task/case
  # 是同一个目录，不归一就会重复挂载、同一个任务扫两遍。
  PRIV_CASE="$(cd "$PRIV_CASE" && pwd -P)"
  _dup=0
  _old_ifs="$IFS"; IFS=':'
  for _p in ${CASE_EXTRA_DIRS:-}; do
    [ -n "$_p" ] || continue
    [ "$(cd "$_p" 2>/dev/null && pwd -P)" = "$PRIV_CASE" ] && _dup=1
  done
  IFS="$_old_ifs"
  [ "$_dup" = 1 ] || export CASE_EXTRA_DIRS="${CASE_EXTRA_DIRS:+$CASE_EXTRA_DIRS:}$PRIV_CASE"
fi

say() { echo "$TS $*" >> "$LOG"; }

# 告警通道按顺序找：SD_ALERT_WEBHOOK → 凭据文件 alert.webhook → 只写日志
webhook() {
  if [ -n "${SD_ALERT_WEBHOOK:-}" ]; then
    printf '%s' "$SD_ALERT_WEBHOOK"
    return 0
  fi
  "$PY" - 2>/dev/null <<'PYEOF'
import sys
sys.path.insert(0, "case/_lib")
try:
    import app_secrets
    sys.stdout.write(app_secrets.get("alert.webhook") or "")
except Exception:
    pass
PYEOF
}

alert() {
  hook="$(webhook)"
  if [ -z "$hook" ]; then
    say "未配置 SD_ALERT_WEBHOOK / alert.webhook，告警只记日志"
    return 0
  fi
  "$PY" - "$hook" "$1" >> "$LOG" 2>&1 <<'PYEOF'
import json, socket, sys, urllib.request
hook, text = sys.argv[1], sys.argv[2]
text = "[stock-dashboard] %s\n机器：%s" % (text, socket.gethostname())
# 飞书自定义机器人用 msg_type，钉钉用 msgtype，两者 body 结构也不同。
# 判断同时认域名（feishu / larksuite）和路径（/open-apis/bot），后者能覆盖自建代理和短链。
if any(k in hook for k in ("feishu", "larksuite", "/open-apis/bot")):
    body = {"msg_type": "text", "content": {"text": text}}
else:
    body = {"msgtype": "text", "text": {"content": text}}
req = urllib.request.Request(hook, data=json.dumps(body).encode("utf-8"),
                             headers={"Content-Type": "application/json"})
try:
    print("告警已发：%s" % urllib.request.urlopen(req, timeout=10).read().decode()[:200])
except Exception as e:
    print("告警发送失败：%s" % e)
PYEOF
}

out=$("$PY" scheduler.py health 2>&1); rc=$?
case "$rc" in
  0)  # 健康：只留巡检时间戳，不写日志
      date +%s > logs/watchdog.hb
      [ -s "$FAILF" ] && rm -f "$FAILF"
      exit 0
      ;;
  2)  # 僵死：进程在但心跳过期 —— start 会说「已在运行」，必须 restart
      say "[WARN] 心跳过期，重启调度器：$out"
      "$PY" scheduler.py restart >> "$LOG" 2>&1
      ;;
  *)  # 未运行（或健康检查命令本身出错）→ 直接拉起
      say "未运行，拉起调度器：$out"
      "$PY" scheduler.py start >> "$LOG" 2>&1
      ;;
esac

# 拉起/重启后二次确认：sleep 2 秒足够 start 自带的 1.8s 探活
sleep 2
out2=$("$PY" scheduler.py health 2>&1); rc2=$?
if [ "$rc2" -eq 0 ]; then
  date +%s > logs/watchdog.hb
  rm -f "$FAILF"
  say "已恢复：$out2"
  exit 0
fi

n=$(( $(cat "$FAILF" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$FAILF"
say "[WARN] 拉起失败（连续第 $n 次）：$out2"
if [ "$n" -ge "$ALERT_AT" ]; then
  # health 输出可能为空（进程直接崩了没打印），退回第一次的输出，两个都空就明说
  detail="${out2:-$out}"
  [ -z "$detail" ] && detail="（health 无输出）"
  alert "调度器连续 ${n} 分钟拉不起来，业务任务已停摆：$detail（详见 logs/scheduler.log）"
  rm -f "$FAILF"        # 告警后清零，避免每分钟重复推送；再连败 $ALERT_AT 次会再告一次
fi
exit 1
