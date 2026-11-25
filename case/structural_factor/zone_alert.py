#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""将军四区间日报 · 区间状态与切换判定

职责：
  1. 维护区间状态文件 zone_state.json，记录"最近一次已判定的区间"及其日期；
  2. 对比本次判定的区间与状态文件中的区间，输出是否发生切换、由哪个区间切到哪个区间。

区间定义（与日报口径一致）：
  1 区间一 = 贴水期（缩量降波、贴水扩大）
  2 区间二 = 散点化运行（次级缓慢扩散）
  3 区间三 = 升水加速（钱比货多）
  4 区间四 = 升水失败

CLI:
  python zone_alert.py --date 2026-10-21 --zone 1
  python zone_alert.py --date 2026-10-21 --zone 2 --zone-label "区间二（散点化运行）" --note "贴水收窄、次级扩散" --dry-run
"""
import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "zone_state.json")

ZONE_LABELS = {
    1: "区间一（贴水期）",
    2: "区间二（散点化运行）",
    3: "区间三（升水加速）",
    4: "区间四（升水失败）",
}

MAX_HISTORY = 60


def normalize_zone(zone):
    """接受 1-4 或 '1'/'区间一' 等形式，归一为整数区间号；无法识别返回 None。"""
    if zone is None:
        return None
    if isinstance(zone, int):
        return zone if zone in ZONE_LABELS else None
    s = str(zone).strip()
    if s.isdigit():
        n = int(s)
        return n if n in ZONE_LABELS else None
    for n, label in ZONE_LABELS.items():
        if s in (label, label.split("（")[0]):
            return n
    return None


def zone_label(zone, override=None):
    if override:
        return override
    return ZONE_LABELS.get(zone, f"区间{zone}")


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def check_transition(current_zone, first_run_as_change=False):
    """对比 current_zone 与状态文件中的区间。

    返回 dict：
      changed        是否发生区间切换
      initialized    是否为首次记录（此前无区间）
      prev_zone      上一次判定的区间号（无则 None）
      prev_label     上一次判定的区间名称（无则 None）
      prev_date      上一次判定的日期（无则 None）
      zone / label   本次判定的区间号与名称
    """
    state = load_state()
    prev_zone = normalize_zone(state.get("last_zone"))
    prev_label = state.get("last_zone_label")
    prev_date = state.get("last_date")

    if prev_zone is None:
        return {
            "changed": bool(first_run_as_change),
            "initialized": True,
            "prev_zone": None,
            "prev_label": None,
            "prev_date": None,
            "zone": current_zone,
            "label": zone_label(current_zone),
        }

    return {
        "changed": prev_zone != current_zone,
        "initialized": False,
        "prev_zone": prev_zone,
        "prev_label": prev_label,
        "prev_date": prev_date,
        "zone": current_zone,
        "label": zone_label(current_zone),
    }


def record(zone, date, label=None, note=""):
    """把本次判定的区间写回状态文件（含历史）。"""
    state = load_state()
    label = zone_label(zone, label)
    state["last_zone"] = zone
    state["last_zone_label"] = label
    state["last_date"] = date
    state["last_note"] = note
    state["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    hist = state.get("history") or []
    entry = {"date": date, "zone": zone, "zone_label": label, "note": note}
    # 同一交易日重复运行（如定时任务重试）就地更新，避免同日重复追加
    if hist and hist[-1].get("date") == date:
        hist[-1] = entry
    else:
        hist.append(entry)
    state["history"] = hist[-MAX_HISTORY:]
    save_state(state)
    return state


def describe(result):
    """把判定结果转成一行可读文本。"""
    if result["initialized"]:
        return f"首次记录：本次区间 {result['label']}（无上一区间，不触发切换提醒）"
    if result["changed"]:
        return (f"区间切换：{result['prev_label']}（{result['prev_date']}）→ "
                f"{result['label']}（{result['zone']}）")
    return f"区间未切换：仍为 {result['label']}（上次 {result['prev_date']}）"


def main():
    ap = argparse.ArgumentParser(description="将军四区间 · 区间状态与切换判定")
    ap.add_argument("--date", required=True, help="本次判定的数据日期（最近交易日）")
    ap.add_argument("--zone", required=True, help="本次判定区间：1/2/3/4 或区间名")
    ap.add_argument("--zone-label", default="", help="区间名称（可选，缺省用内置标签）")
    ap.add_argument("--note", default="", help="判定依据摘要（可选）")
    ap.add_argument("--dry-run", action="store_true", help="不写回状态文件，仅打印判定结果")
    args = ap.parse_args()

    zone = normalize_zone(args.zone)
    if zone is None:
        print(f"[区间] 无法识别的区间：{args.zone}（应为 1/2/3/4 或区间名）")
        sys.exit(2)

    result = check_transition(zone)
    result["label"] = zone_label(zone, args.zone_label or None)
    print("[区间] " + describe(result))

    if args.dry_run:
        print("[区间] dry-run：未写回状态文件")
        sys.exit(0)

    record(zone, args.date, label=args.zone_label or None, note=args.note)
    print(f"[区间] 状态已写回 zone_state.json（{args.date} -> {result['label']}）")
    sys.exit(0)


if __name__ == "__main__":
    main()