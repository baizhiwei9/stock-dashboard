#!/bin/bash
# scheduler 看门狗：由 cron 每分钟调用一次。
#
# 作用只有一个 —— 保证 scheduler 常驻进程**活着且真的在推进**。
# 业务调度仍全部由 scheduler.py 决定，cron 不直接跑任何业务任务
# （避免和「scheduler 统一管理」冲突）。
#
# 存在的理由：本环境的 bash 工具调用结束后会清理该调用派生的所有子进程，
# 所以 setsid/nohup 也留不住常驻进程；而 cron 在宿主侧运行，起得来就活得久。
#
# 判定用 `scheduler.py health` 的**心跳**，不用 pid：
#   0 = 健康（心跳 180 秒内更新过）      → 只刷 logs/watchdog.hb，静默
#   1 = 未运行                          → scheduler.py start
#   2 = 僵死（进程在但心跳停了）          → scheduler.py restart
# 为什么必须区分 1 和 2：僵死时进程还活着、pid 正常、端口甚至还能响应，
# `start` 会因为「已在运行」直接空转 —— 按 pid 判定的看门狗在这个场景下
# 永远救不回来，而那正好是最需要它的时候。
#
# 拉起/重启后会再查一次；连续 SD_ALERT_AT（默认 3）次仍不健康才发告警，
# 告警后计数清零，避免每分钟刷屏。
#
# 环境变量：
#   SD_ALERT_AT        连续失败多少次告警，默认 3
#   SD_ALERT_WEBHOOK   告警 webhook（飞书群机器人）；不设则读凭据文件
#                      的 alert.webhook，都没有就只记日志
# 部署目录：默认本机路径；换机器时 export SD_HOME=/srv/monitor/stock-dashboard
# 覆盖即可，不用改脚本（cron 行里可以直接带 SD_HOME=...）。
SD="${SD_HOME:-/Coze/Drive/股市监控画布/stock-dashboard}"

# 私有任务（precious-task）挂载点：只跑与落库，不上牌面。
# 不设这个变量 = private 任务不进调度（不报错、静默消失），所以必须写死在这里。
export CASE_EXTRA_DIRS="/Coze/Drive/股市监控画布/precious-task/case"

# ── case 任务的运行环境 ────────────────────────────────────────────
# data_hub 仓库位置：优先同级 checkout（新机器按 DEPLOY.md §2 就是这么放的），
# 找不到再退回本机的非标准路径。这样换机器不用改这个脚本。
DH="${EDB_REPO:-}"
if [ -z "$DH" ] || [ ! -d "$DH/data_hub" ]; then
  for _cand in "$SD/../datahub-multi-tier" "/opt/coze/.fuse_drive/dataHub_三级体系"; do
    if [ -d "$_cand/data_hub" ]; then DH="$_cand"; break; fi
  done
fi
if [ -n "$DH" ]; then
  export EDB_REPO="$DH"          # dh.py 靠它找含 data_hub/ 的仓库
  export PYTHONPATH="$DH"        # scheduler 与 runner 子进程都继承
fi

# PYLIBS_DIR：**故意不设**。
# 原设计是「第三方包（akshare/tushare/…）集中放一个公共目录，靠 PYLIBS_DIR 引用」，
# 机制保留在 task.json 的 requires 和 runner 里（设了就自动前置进 sys.path）。
# 但本机第三方包已全局装在 /usr/local/lib/python3.13/dist-packages，
# 原 crontab 里也从未设过这个变量，所以缺省不设、走系统 site-packages。
# 真要隔离时，在这里 export 一行即可，无需改任何任务。
# export PYLIBS_DIR=/path/to/pylibs

cd "$SD" || exit 0
mkdir -p logs

PY=/usr/bin/python3
LOG="logs/watchdog.log"
HB="logs/watchdog.hb"            # 最近一次确认健康的 unix 时间戳
FAIL_FILE="logs/watchdog.fail"   # 连续不健康次数
ALERT_AT="${SD_ALERT_AT:-3}"

log() { echo "[$(date "+%Y-%m-%d %H:%M:%S")] watchdog: $*" >> "$LOG"; }

# ── 1) 先问 health ────────────────────────────────────────────────
out=$("$PY" scheduler.py health 2>&1)
rc=$?

case $rc in
  0)
    date +%s > "$HB"
    if [ -f "$FAIL_FILE" ]; then
      rm -f "$FAIL_FILE"
      log "已恢复健康（health=0），失败计数清零"
    fi
    exit 0
    ;;
  1)
    log "未运行 → start"
    "$PY" scheduler.py start >> "$LOG" 2>&1
    ;;
  2)
    log "僵死 → restart（$out）"
    "$PY" scheduler.py restart >> "$LOG" 2>&1
    ;;
  *)
    log "health 返回意外码 $rc：$out"
    ;;
esac

# ── 2) 拉起/重启后再查一次 ────────────────────────────────────────
# start 是后台起进程，给 3 秒让它把 pid 写出来。第一轮 tick 可能还没落，
# health 对「心跳记录缺失」按健康处理（下一轮 tick 会补上），所以不会误判。
sleep 3
out2=$("$PY" scheduler.py health 2>&1)
rc2=$?
if [ "$rc2" = "0" ]; then
  date +%s > "$HB"
  log "已恢复（health=0）：$out2"
  rm -f "$FAIL_FILE"
  exit 0
fi

# ── 3) 还是不健康 → 累计失败次数，够阈值才告警 ────────────────────
n=$(( $(cat "$FAIL_FILE" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$FAIL_FILE"
log "拉起后仍不健康（health=$rc2，连续失败 $n/$ALERT_AT）：$out2"

if [ "$n" -ge "$ALERT_AT" ]; then
  WEBHOOK="${SD_ALERT_WEBHOOK:-}"
  if [ -z "$WEBHOOK" ]; then
    WEBHOOK=$("$PY" -c 'import sys; sys.path.insert(0,"case/_lib")
try:
    import app_secrets as s
    print(s.get("alert.webhook") or "")
except Exception:
    print("")' 2>/dev/null)
  fi
  if [ -n "$WEBHOOK" ]; then
    # health 输出可能为空（比如进程直接崩了没打印），退回第一次的输出，
    # 两个都空就写「无输出」，别让告警里出现空荡荡的一行。
    detail="${out2:-$out}"
    [ -z "$detail" ] && detail="（health 无输出）"
    "$PY" - "$WEBHOOK" "$n" "$detail" >> "$LOG" 2>&1 <<'PY_ALERT'
import json, socket, sys, urllib.request
hook, n, detail = sys.argv[1], sys.argv[2], sys.argv[3]
text = ("🚨 stock-dashboard 调度器连续 %s 次拉起失败\n"
        "health 输出：%s\n机器：%s\n详见 logs/watchdog.log"
        % (n, detail, socket.gethostname()))
body = {"msg_type": "text", "content": {"text": text}}
req = urllib.request.Request(
    hook, data=json.dumps(body).encode("utf-8"),
    headers={"Content-Type": "application/json"})
try:
    print("告警已发：%s" % urllib.request.urlopen(req, timeout=10).read().decode()[:200])
except Exception as e:
    print("告警发送失败：%s" % e)
PY_ALERT
    log "已尝试发送告警（连续失败 $n 次）"
  else
    log "未配置 SD_ALERT_WEBHOOK / alert.webhook，告警只记日志"
  fi
  rm -f "$FAIL_FILE"     # 告警后清零，避免每分钟刷屏
fi
exit 1
