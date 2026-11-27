#!/usr/bin/env bash
# switch_crontab.sh — 把「日更 / 周更」任务从 crontab 一次性切给 scheduler 接管
#
# 为什么要有这个脚本：crontab 里这些任务走的是
#   python3 -m data_hub.tech.health_run --name "xxx" --timeout N -- <真命令>
# 和 scheduler 的 case 任务跑的是同一件事。两边都开就是双跑，两边都关就是停摆。
# 所以切换必须是一个原子动作：开 scheduler 任务 + 删 crontab 对应行，一步完成。
#
# 用法：
#   bash scripts/switch_crontab.sh              # dry-run，只打印将要做什么
#   bash scripts/switch_crontab.sh --apply      # 真改：开 task.json + 重装 crontab
#   bash scripts/switch_crontab.sh --revert     # 回滚：关 task.json + 恢复最近备份
#
# 安全：--apply / --revert 前都会把 crontab 备份到 logs/crontab.<时间戳>.bak
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
ROOT="$PWD"
CASE_DIR="$ROOT/case"
STAMP="$(date +%Y%m%d_%H%M%S)"
BAK_DIR="$ROOT/logs"
mkdir -p "$BAK_DIR"

# 由 scheduler 接管的日更/周更任务 key（= case/<key> 目录名）
KEYS=(
  midline_preference circulation_daily unified_screener sector_screening_v4
  risk_forecast_daily liquidity_risk_monitor merged_analysis top200_concept
  concept_tracker_top20 bond_four_factor cycle_monitor oil_monitor
  style_drift_monitor vol_monitor_daily credit_spread_weekly margin_season_weekly
  hurst_daily tugou_archive weekly_stock_monitor tech_consensus_daily
)

# crontab 里 --name "xxx" 的 xxx（与 KEYS 一一对应，顺序无关）
NAMES=(
  "中线偏好监控" "环流日报" "多头排列选股" "板块筛选v4" "风险预测·日终"
  "流动性风险监控" "合并分析" "涨幅Top200概念" "热点概念Top20" "国债期货四因子"
  "库存信贷周期" "油价月报" "风格漂移监控" "股指波动率监控" "信用利差周报"
  "两融季节同比(周五)" "Hurst指数" "土狗归档" "自选股周报" "科技共识日报"
)

MODE="dry"
case "${1:-}" in
  --apply)  MODE="apply"  ;;
  --revert) MODE="revert" ;;
  "")       MODE="dry"    ;;
  *) echo "用法: $0 [--apply|--revert]"; exit 2 ;;
esac

echo "== 模式: $MODE  仓库: $ROOT"

# 读现有 crontab 到临时文件，删掉 --name 命中 NAMES 的行，stdout 输出新 crontab
build_crontab() {
  local cur src
  cur="$(crontab -l 2>/dev/null)"
  src="/tmp/_cron_src.$$"
  printf '%s\n' "$cur" > "$src"
  python3 - "$src" "$@" <<'PYX'
import sys, re
src, names = sys.argv[1], set(sys.argv[2:])
pat = re.compile(r'--name\s+"([^"]+)"')
keep, drop = [], []
for line in open(src, encoding='utf-8').read().splitlines():
    m = pat.search(line)
    if m and m.group(1).strip() in names:
        drop.append(m.group(1).strip())
    else:
        keep.append(line)
sys.stderr.write("  命中 %d 行待移除: %s\n" % (len(drop), ", ".join(drop) if drop else "(无)"))
sys.stdout.write("\n".join(keep).rstrip() + "\n")
PYX
  rm -f "$src"
}

set_enabled() {  # $1 = true|false
  local v="$1" n=0 k f
  for k in "${KEYS[@]}"; do
    f="$CASE_DIR/$k/task.json"
    [ -f "$f" ] || { echo "  ⚠ 缺 $f"; continue; }
    python3 - "$f" "$v" <<'PYX'
import json, sys
p, v = sys.argv[1], (sys.argv[2] == "true")
d = json.load(open(p))
if d.get("enabled") is not v:
    d["enabled"] = v
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
PYX
    n=$((n+1))
  done
  echo "  已设 enabled=$v 的任务: $n"
}

case "$MODE" in
  dry)
    echo "-- crontab 侧：将被移除的行 --"
    build_crontab "${NAMES[@]}" > /tmp/_new_cron.$$ 2>/tmp/_cron_msg.$$
    cat /tmp/_cron_msg.$$
    echo "   移除后 crontab 行数: $(wc -l < /tmp/_new_cron.$$)"
    echo "-- scheduler 侧：将被设为 enabled=true 的任务 --"
    for k in "${KEYS[@]}"; do
      f="$CASE_DIR/$k/task.json"
      if [ -f "$f" ]; then
        printf '  %-24s enabled=%s\n' "$k" "$(python3 -c "import json;print(json.load(open('$f')).get('enabled'))" 2>/dev/null)"
      else
        printf '  %-24s ⚠ 无 task.json\n' "$k"
      fi
    done
    echo "-- dry-run 结束，未改动任何文件或 crontab --"
    echo "   真执行: bash scripts/switch_crontab.sh --apply"
    rm -f /tmp/_new_cron.$$ /tmp/_cron_msg.$$
    ;;
  apply)
    crontab -l > "$BAK_DIR/crontab.$STAMP.bak" 2>/dev/null && echo "  备份: $BAK_DIR/crontab.$STAMP.bak"
    set_enabled true
    build_crontab "${NAMES[@]}" > /tmp/_new_cron.$$ 2>/tmp/_cron_msg.$$
    cat /tmp/_cron_msg.$$
    if crontab /tmp/_new_cron.$$; then
      echo "  ✅ crontab 已重装（现有 $(crontab -l | wc -l) 行）"
    else
      echo "  ❌ crontab 重装失败，请从 $BAK_DIR/crontab.$STAMP.bak 恢复"
    fi
    rm -f /tmp/_new_cron.$$ /tmp/_cron_msg.$$
    echo "  下一步：python3 scheduler.py health 确认 rc=0"
    ;;
  revert)
    crontab -l > "$BAK_DIR/crontab.$STAMP.bak" 2>/dev/null && echo "  当前已备份: $BAK_DIR/crontab.$STAMP.bak"
    set_enabled false
    prev="$(ls -t "$BAK_DIR"/crontab.*.bak 2>/dev/null | sed -n '2p')"
    if [ -n "$prev" ]; then
      crontab "$prev" && echo "  ✅ 已恢复 crontab: $prev"
    else
      echo "  ⚠ 找不到更早的备份，请手工恢复 crontab"
    fi
    ;;
esac
