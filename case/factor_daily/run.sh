#!/bin/bash
# 资金面因子日报 —— case 任务入口（原 strategies/run_daily_factor.sh 的本地化版本）
#
# 只改一处：工作目录不再写死 workspace 绝对路径，改为脚本自己所在目录；
# 其余闸门（周末跳过 / 非交易日跳过 / 日志落 logs/）与原件完全一致。
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
LOGDIR="$HERE/logs"
mkdir -p "$LOGDIR"

TODAY=$(date +%Y-%m-%d)
DOW=$(date +%u)  # 1=Mon ... 7=Sun

if [ "$DOW" -gt 5 ] && [ "${CASE_FORCE_RUN:-0}" != "1" ]; then
    echo "$TODAY 周末跳过" >> "$LOGDIR/skip.log"
    echo "$TODAY 周末跳过"          # 也走 stdout：runner 据此判 skipped，不再报 data 空
    exit 0
fi
# 交易日闸门：只有 check_trading_day() 明确判定「非交易日」才跳过。
# 该模块的契约是非交易日 sys.exit(0)（见 trading_day_check.check_trading_day），
# 所以这里把它包一层，用专门的退出码把「休市」与「真实错误」区分开：
#   0 = 交易日（照常出报告）  3 = 明确非交易日（跳过）
#   2 = 导入失败/其它异常（stderr 原样透出，下面按失败 exit 1）
#
# trading_day_check 在 case/_lib（#173 已补），但只有经过 runner 时才被注入
# PYTHONPATH；直接 `bash run.sh`（手工补跑、crontab 直跑旧入口、排障单跑）
# 仍会 ModuleNotFoundError → rc=2 → exit 1。这里两层兜底：
#   ① 把 case/_lib 挂进 PYTHONPATH，用仓库内实现（akshare → tushare → 工作日降级）；
#   ② 用 hub_root.ensure_hub_on_path 按全仓统一口径（环境变量 → 仓库根
#      datahub_path.py → 同级 checkout）把 DataHub 根插到 sys.path 最前，
#      DataHub 里若有更新的实现就优先用它；找不到不阻断，①仍是兜底。
rc=0
PYTHONPATH="$HERE/../_lib${PYTHONPATH:+:$PYTHONPATH}" \
python3 - "$HERE" <<'PY' || rc=$?
import sys
try:
    from hub_root import ensure_hub_on_path
    ensure_hub_on_path(sys.argv[1] if len(sys.argv) > 1 else ".")
except Exception:                    # DataHub 未部署/未装：退仓库内实现
    pass
try:
    from trading_day_check import check_trading_day
except Exception:
    import traceback
    traceback.print_exc()
    sys.exit(2)
try:
    check_trading_day()
except SystemExit as e:
    if e.code in (0, None):
        sys.exit(3)          # 明确判定：非交易日
    raise
PY
case "$rc" in
    0) : ;;
    3) echo "$TODAY 非交易日跳过" >> "$LOGDIR/skip.log"
       echo "$TODAY 非交易日跳过"          # 也走 stdout：runner 据此判 skipped
       exit 0 ;;
    *) echo "$TODAY 交易日判断失败 rc=$rc（不是休市，按失败处理）" >&2
       echo "$TODAY 交易日判断失败 rc=$rc" >> "$LOGDIR/skip.log"
       exit 1 ;;
esac

cd "$HERE"
python3 -u daily_factor_report.py --date "$TODAY" >> "$LOGDIR/$TODAY.log" 2>&1
