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
#   SD_ALERT_WEBHOOK 连续拉不起来时的告警 webhook（钉钉/飞书自定义机器人），留空只写日志
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

say()      { echo "$TS $*" >> "$LOG"; }
alert()    {
  [ -n "${SD_ALERT_WEBHOOK:-}" ] || return 0
  curl -s -m 5 -X POST -H 'Content-Type: application/json' \
       -d "{\"msgtype\":\"text\",\"text\":{\"content\":\"[stock-dashboard] $1\"}}" \
       "$SD_ALERT_WEBHOOK" >/dev/null 2>&1 || true
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
  alert "调度器连续 ${n} 分钟拉不起来，业务任务已停摆：$out2（详见 logs/scheduler.log）"
  rm -f "$FAILF"        # 告警后清零，避免每分钟重复推送；再连败 $ALERT_AT 次会再告一次
fi
exit 1