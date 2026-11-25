#!/bin/bash
# scheduler 看门狗：由 cron 每分钟调用一次。
#
# 职责只有一个 —— 保证 scheduler 常驻进程「真的在调度」；业务任务仍全部由
# scheduler.py 决定，cron 不直接跑任何业务任务（避免和「scheduler 统一管理」冲突）。
#
# 判定依据是 scheduler.py 每 tick 写的心跳文件 logs/scheduler.hb（3 分钟过期），
# 不是「pid 在不在」：进程活着但调度循环卡死（僵死）时，pid 判定会漏掉。
# 退出码语义由 `scheduler.py health` 给出：0=健康 / 1=未运行 / 2=僵死。
#
# 部署（宿主 crontab，一行）：
#   * * * * * /path/to/stock-dashboard/sd_watchdog.sh >/dev/null 2>&1
#
# 可选环境变量：
#   SD_PYTHON        指定解释器（默认 python3）
#   SD_ALERT_WEBHOOK 连续拉不起来时的告警 webhook，留空则去凭据文件找 alert.webhook，
#                    都没有就只写日志（不静默失败，也不外发）
#   SD_ALERT_AT      连续失败几次才告警（默认 3）
#
# 落盘（都在 stock-dashboard/logs/）：
#   watchdog.log   拉起/重启/失败记录（健康时不写，避免每分钟一行噪音）
#   watchdog.hb    最后一次成功巡检的 epoch 秒 —— 用来确认 cron 这行还在跑
#   watchdog.fail  连续拉起失败计数（成功即清零）
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE" || exit 1
mkdir -p logs

PY="${SD_PYTHON:-python3}"
LOG="logs/watchdog.log"
FAILF="logs/watchdog.fail"
ALERT_AT="${SD_ALERT_AT:-3}"
TS="$(date '+%Y-%m-%d %H:%M:%S')"

# 环境自举：cron 的 env 极简，PYTHONPATH 一般为空。任务命令自己在 cmd 里带了
# PYTHONPATH，但 scheduler 进程内的 task_health 上报（record）要 import
# data_hub.tech.health，那条路只认 DATAHUB_DIR / DATA_HUB_PATH / 代码里的兜底路径。
# 这里按部署约定（datahub 与 stock-dashboard 同级 checkout）补一次，换机器就不用
# 去改 scheduler.py 里的兜底常量。已经配过（systemd / DEPLOY.md §4）就完全不动。
if [ -z "${PYTHONPATH:-}" ] && [ -z "${DATAHUB_DIR:-}" ] && [ -z "${DATA_HUB_PATH:-}" ]; then
  for _c in "$HERE/../datahub-multi-tier" "$HERE/datahub-multi-tier" "${EDB_REPO:-}"; do
    if [ -n "$_c" ] && [ -d "$_c/data_hub" ]; then
      export PYTHONPATH="$_c"
      export EDB_REPO="$_c"
      break
    fi
  done
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
