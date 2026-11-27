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
# 安全 ①：--apply 先做前置检查——20 个 case 的 task.json 缺一个、双时间点任务的
#          sched.at 没覆盖全、cmd 里的入口文件不存在、task.json 的 weekdays 和 crontab
#          的星期字段对不上，任一命中就 exit 3 中止，一行 crontab 都不动（避免静默停摆）。
# 安全 ②：--apply / --revert 前都会把 crontab 备份到 logs/crontab.<时间戳>.bak
# 安全 ③：crontab 重装失败时自动把 enabled 改回 false，避免 scheduler 与 crontab 双跑。
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
  risk_release_monitor risk_forecast_daily liquidity_risk_monitor merged_analysis
  top200_concept concept_tracker_top20 cycle_monitor oil_monitor
  style_drift_monitor vol_monitor_daily bond_four_factor credit_spread_weekly margin_season_weekly
  hurst_daily tugou_archive weekly_stock_monitor tech_consensus_daily
  wide_t1 wide_t5 wide_ens_weekly liq_credit_weekly cftc_cot_weekly
)

# crontab 里 --name "xxx" 的 xxx（与 KEYS 一一对应，顺序无关）
# 注意：wide_* / liq_credit_weekly / cftc_cot_weekly 的 --name 是迁移时的命名，
# 若旧 crontab 用了不同名字，切换前先核一遍（preflight 会按此对账并报 mismatch）。
NAMES=(
  "中线偏好监控" "环流日报" "多头排列选股" "板块筛选v4" "风险释放监控"
  "风险预测·日终" "风险预测·午间" "流动性风险监控" "合并分析" "涨幅Top200概念" "热点概念Top20"
  "库存信贷周期" "油价月报" "风格漂移监控" "股指波动率监控"
  "国债期货四因子" "信用利差周报" "两融季节同比(周五)" "Hurst指数" "土狗归档" "自选股周报"
  "科技共识日报"
  "宽基 T+1 双轨预测" "宽基 T+5 周报" "宽基集成周报（统一选模）"
  "流动性 · 信用风险周报（中国 / 美国）" "CFTC COT 极值持仓周报"
)

# 一个 key 对应多条 crontab 时，声明每条 cron 的时间点。
# preflight 拿它和 task.json 的 sched.at 对账：少一个时间点，切完那条就静默停摆。
multi_slot_of() {
  case "$1" in
    risk_forecast_daily) echo "12:30 20:50" ;;   # 风险预测·午间 + 风险预测·日终
    *) echo "" ;;
  esac
}

# 每个 key 在 crontab 里的 --name（与 NAMES 顺序无关，用于反查 cron 的星期字段）
cron_names_of() {
  case "$1" in
    risk_forecast_daily) echo "风险预测·午间 风险预测·日终" ;;
    *) python3 - "$1" <<'PYX'
import sys
m = {
 "midline_preference": "中线偏好监控", "circulation_daily": "环流日报",
 "unified_screener": "多头排列选股", "sector_screening_v4": "板块筛选v4",
 "risk_release_monitor": "风险释放监控", "liquidity_risk_monitor": "流动性风险监控",
 "merged_analysis": "合并分析", "top200_concept": "涨幅Top200概念",
 "concept_tracker_top20": "热点概念Top20", "cycle_monitor": "库存信贷周期",
 "oil_monitor": "油价月报", "style_drift_monitor": "风格漂移监控",
 "vol_monitor_daily": "股指波动率监控", "credit_spread_weekly": "信用利差周报",
 "bond_four_factor": "国债期货四因子",
 "margin_season_weekly": "两融季节同比(周五)", "hurst_daily": "Hurst指数",
 "tugou_archive": "土狗归档", "weekly_stock_monitor": "自选股周报",
 "tech_consensus_daily": "科技共识日报",
}
print(m.get(sys.argv[1], ""))
PYX
    ;;
  esac
}

# 前置检查。soft = 只警告（dry-run 用）；hard = 有问题直接中止（apply 用）。
preflight() {
  local mode="${1:-hard}" miss=0 bad=0 k f slots
  echo "-- 前置检查（$mode）：${#KEYS[@]} 个任务 --"
  for k in "${KEYS[@]}"; do
    f="$CASE_DIR/$k/task.json"
    if [ ! -f "$f" ]; then
      printf '  ❌ 缺 task.json: %s\n' "$f"
      miss=$((miss + 1))
      continue
    fi
    # ① 多槽时间点：task.json 的 sched.at 必须覆盖每条 cron 的时间
    slots="$(multi_slot_of "$k")"
    if [ -n "$slots" ]; then
      if ! python3 - "$f" $slots <<'PYX'
import json, sys
p, want = sys.argv[1], sys.argv[2:]
d = json.load(open(p, encoding="utf-8"))
at = (d.get("sched") or {}).get("at")
have = [at] if isinstance(at, str) else [str(x) for x in (at or [])]
miss = [w for w in want if w not in have]
if miss:
    sys.stderr.write("    sched.at=%s 缺 %s\n" % (have, ",".join(miss)))
    sys.exit(1)
PYX
      then
        printf '  ❌ %s 的 sched.at 没覆盖全部 cron 时间点（%s）\n' "$k" "$slots"
        bad=$((bad + 1))
      fi
    fi
    # ② 入口文件：task.json 存在但 cmd 里的脚本被删/改路径，切完就是天天失败
    if ! python3 - "$CASE_DIR/$k" "$f" <<'PYX'
import json, os, shlex, sys
tdir, p = sys.argv[1], sys.argv[2]
d = json.load(open(p, encoding="utf-8"))
cmd = (d.get("cmd") or "").strip()
if not cmd:
    sys.stderr.write("    cmd 为空\n"); sys.exit(1)
try:
    parts = shlex.split(cmd)
except ValueError:
    sys.stderr.write("    cmd 解析失败: %s\n" % cmd); sys.exit(1)
# 跳过解释器与前置参数（python3 -u xxx / bash xxx / PYTHONPATH=.. python3 ..）
skip = {"python", "python3", "bash", "sh", "env", "/usr/bin/python3", "/usr/bin/env"}
ent = ""
for tok in parts:
    if tok in skip or tok.startswith("-") or "=" in tok:
        continue
    ent = tok
    break
if not ent:
    sys.stderr.write("    cmd 里找不到入口脚本: %s\n" % cmd); sys.exit(1)
path = ent if os.path.isabs(ent) else os.path.join(tdir, ent)
if not os.path.exists(path):
    sys.stderr.write("    入口文件不存在: %s\n" % path); sys.exit(1)
PYX
    then
      printf '  ❌ %s 的 cmd 入口文件不存在\n' "$k"
      bad=$((bad + 1))
    fi
    # ③ 星期口径：task.json 的 weekdays 必须和 crontab 的星期字段一致
    #    不一致的后果是「切完周末不跑了」或「该跑的没跑」，且不会有任何告警。
    # 注意：这里不能写 `crontab -l | python3 - <<'PYX'` —— heredoc 会顶掉管道，
    # python 的 stdin 变成脚本本体，crontab 内容永远读不到（第一版就这么踩了）。
    # 落一个临时文件，把路径当参数传进去。
    crontab -l > "/tmp/_cron_dow.$$" 2>/dev/null || : > "/tmp/_cron_dow.$$"
    if ! python3 - "$f" "$(cron_names_of "$k")" "/tmp/_cron_dow.$$" <<'PYX'
import json, re, sys
p, names, cron_path = sys.argv[1], [n for n in sys.argv[2].split() if n], sys.argv[3]
d = json.load(open(p, encoding="utf-8"))
wds = sorted(set(int(x) for x in ((d.get("sched") or {}).get("weekdays") or [])))
if not wds:
    sys.exit(0)
cron = open(cron_path, encoding="utf-8", errors="replace").read()
pat = re.compile(r'--name\s+"([^"]+)"')
bad = []
found = 0
for line in cron.splitlines():
    if line.lstrip().startswith("#"):
        continue
    m = pat.search(line)
    if not m or m.group(1).strip() not in names:
        continue
    found += 1
    fields = line.split()
    if len(fields) < 6:
        continue
    dow = fields[4]
    if dow == "*":
        want = list(range(1, 8))
    elif re.fullmatch(r"[0-7](,[0-7])*", dow):
        want = sorted({7 if x == "0" else int(x) for x in dow.split(",")})
    elif re.fullmatch(r"[0-7]-[0-7]", dow):
        a, b = (7 if x == "0" else int(x) for x in dow.split("-"))
        want = list(range(a, b + 1))
    else:
        continue          # 形如 */2 的写法不猜，跳过（不误报）
    if want != wds:
        bad.append("%s: cron dow=%s → %s，task.json weekdays=%s"
                   % (m.group(1).strip(), dow, want, wds))
if found == 0:
    # crontab 里根本没有 --name 行（新机器 / 已经切过了）：不是错误，静默跳过。
    sys.exit(0)
if bad:
    for b in bad:
        sys.stderr.write("    " + b + "\n")
    sys.exit(1)
PYX
    then
      printf '  ❌ %s 的 sched.weekdays 与 crontab 星期字段不一致\n' "$k"
      bad=$((bad + 1))
    fi
    rm -f "/tmp/_cron_dow.$$"
  done
  if [ "$miss" -gt 0 ] || [ "$bad" -gt 0 ]; then
    echo
    echo "  ⚠ $miss 个缺 task.json，$bad 处不一致（时间点 / 入口文件 / 星期）"
    echo "  现在切会让这些任务从 crontab 消失、scheduler 又不跑 → 静默停摆。"
    if [ "$mode" = "hard" ]; then
      echo "  中止。先把 case/ 同步到本目录并修掉上面几项，再重试。"
      exit 3
    fi
  else
    echo "  ✅ ${#KEYS[@]} 个 task.json 齐全，时间点 / 入口文件 / 星期均对齐"
  fi
}

MODE="dry"
case "${1:-}" in
  --apply)  MODE="apply"  ;;
  --revert) MODE="revert" ;;
  "")       MODE="dry"    ;;
  *) echo "用法: $0 [--apply|--revert]"; exit 2 ;;
esac

echo "== 模式: $MODE  仓库: $ROOT"

# 读现有 crontab，删掉 --name 命中 NAMES 的行，写到 $1；成功返回 0。
#
# 2026-11-26 加固：原版只判 `crontab <file>` 的返回码。实测 python 段失败时
# 临时文件是空的，而 `crontab <空文件>` 返回 0 —— 于是脚本打印「✅ crontab 已重装
# （现有 0 行）」，把整张 crontab 清空。现在三道闸：
#   ① 读现有表失败 → return 1；② python 段失败 → return 1；
#   ③ 安装前校验：新表非空，且行数 == 原行数 − 命中行数。
build_crontab() {  # $1 = 输出文件；其余 = 要移除的 --name
  local out="$1"; shift
  local src="/tmp/_cron_src.$$" msg="/tmp/_cron_msg.$$" cnt="/tmp/_cron_cnt.$$" rc before drop
  if ! crontab -l > "$src" 2>/dev/null; then
    : > "$src"        # 新机器上 crontab 为空时 `crontab -l` 非 0 属正常
  fi
  # 行数口径与安装前自检（grep -c ''）一致：crontab 文件末尾没有换行时 wc -l 会少算 1 行，
  # 第③道闸会误判「行数对不上」而拒绝安装（安全但会挡住合法的 --apply）。
  before="$(awk 'END{print NR}' "$src")"
  if [ "$before" -eq 0 ]; then
    echo "  ❌ 读到的 crontab 是空的，拒绝继续（防止把生产 crontab 清空）" >&2
    rm -f "$src" "$msg"
    return 1
  fi
  python3 - "$src" "$cnt" "$@" > "$out" 2> "$msg" <<'PYX'
import sys, re
src, cnt_path, names = sys.argv[1], sys.argv[2], set(sys.argv[3:])
pat = re.compile(r'--name\s+"([^"]+)"')
keep, drop = [], []
for line in open(src, encoding='utf-8').read().splitlines():
    m = pat.search(line)
    if m and m.group(1).strip() in names:
        drop.append(m.group(1).strip())
    else:
        keep.append(line)
sys.stderr.write("  命中 %d 行待移除: %s\n" % (len(drop), ", ".join(drop) if drop else "(无)"))
open(cnt_path, "w").write(str(len(drop)))
sys.stdout.write("\n".join(keep).rstrip() + "\n")
PYX
  rc=$?
  cat "$msg" >&2
  rm -f "$src"
  if [ "$rc" -ne 0 ]; then
    echo "  ❌ 生成新 crontab 失败（python 退出码 $rc），未安装" >&2
    rm -f "$msg"
    return 1
  fi
  drop="$(cat "$cnt" 2>/dev/null || echo 0)"
  rm -f "$msg" "$cnt"
  # 安装前自检：非空 + 行数对得上
  if [ ! -s "$out" ]; then
    echo "  ❌ 新 crontab 为空，拒绝安装" >&2
    return 1
  fi
  local after expect
  after="$(grep -c '' "$out" 2>/dev/null || echo 0)"
  expect=$((before - ${drop:-0}))
  if [ "$after" -ne "$expect" ]; then
    echo "  ❌ 新 crontab 行数 ${after} != 预期 ${expect}（原 ${before} − 命中 ${drop:-0}），拒绝安装" >&2
    return 1
  fi
  return 0
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
    preflight soft
    echo "-- crontab 侧：将被移除的行 --"
    if ! build_crontab /tmp/_new_cron.$$ "${NAMES[@]}"; then
      echo "   预演中止：生成新 crontab 失败（详见上面的错误）"
    else
      echo "   移除后 crontab 行数: $(grep -c '' /tmp/_new_cron.$$)"
    fi
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
    preflight hard
    crontab -l > "$BAK_DIR/crontab.$STAMP.bak" 2>/dev/null && echo "  备份: $BAK_DIR/crontab.$STAMP.bak"
    # 顺序：先生成并校验新表，成功了再开 scheduler。
    # 反过来的话，生成失败时任务已经 enabled=true 而 crontab 还在 → 双跑。
    if ! build_crontab /tmp/_new_cron.$$ "${NAMES[@]}"; then
      echo "  ❌ 中止：新 crontab 没生成出来，scheduler 侧未做任何改动（crontab 原样）"
      rm -f /tmp/_new_cron.$$
      exit 4
    fi
    set_enabled true
    if crontab /tmp/_new_cron.$$; then
      echo "  ✅ crontab 已重装（现有 $(crontab -l | grep -c '') 行）"
    else
      # 关键：crontab 没换成功，但 scheduler 任务已经开了 → 会双跑。立刻关回去。
      echo "  ❌ crontab 重装失败，回滚 scheduler 侧 enabled=false（避免双跑）"
      set_enabled false
      echo "     请手工确认 crontab 未变: crontab -l | grep -c '\-\-name'"
    fi
    rm -f /tmp/_new_cron.$$
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
