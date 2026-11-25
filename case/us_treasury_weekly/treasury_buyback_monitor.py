#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
财政部国债回购监控分析脚本（从统一数据层读取，不做联网）
============================================================
数据来源：treasury_data_output.json（由 treasury_data_fetch.py 统一抓取 buyback 板块：
  schedule = Tentative-Buyback-Schedule.xml 解析的未来计划
  results  = api.fiscal.treasury.gov /v1/marketable-securities/buybacks 的 Results 明细）

计算：
  接纳率     = accepted / submitted
  utilization = accepted / max_size
  bid_cover   = submitted / max_size
  单位：对外统一用 亿美元（美元 / 1e8），保留 2 位小数。

报警规则（按利用率四级）：
  utilization >= 95%            -> 买满
  80% <= util <  95%            -> 基本买满
  50% <= util <  80%            -> 明显低于上限，关注（可能是惜售或挑价）
  util <  50%                   -> 严重没买满，信号意义强/需求差

同时观察：
  - 长端（10-30Y 篮子）低于上限 -> 对美债长端利率压制不及预期
  - 连续 2-3 次低于上限（util < 95%）-> 财政部回购效力走弱
  - 口径提示：低于上限 != QE（财政部回购多数用短债筹资，不算美联储扩表）

用法：
  python3 treasury_buyback_monitor.py [--json 输出文件]
    默认输出 treasury_buyback_output.json（不发送）
"""

import json
import sys
from datetime import date, timedelta

# 长端篮子关键词（10-30Y）
LONG_END_KEYS = ("10Y to 20Y", "20Y to 30Y", "10Y to 30Y")
# 连续低于上限阈值（util < 95% 视为低于上限）
FULL_THRESHOLD = 95.0
WEAK_STREAK = 2  # 连续 2-3 次低于上限判定走弱


def _to_100m(usd):
    """美元 -> 亿美元。"""
    return round(usd / 1e8, 2)


def _util_level(util):
    if util is None:
        return "无数据", "gray"
    if util >= 95.0:
        return "买满", "green"
    if util >= 80.0:
        return "基本买满", "yellow"
    if util >= 50.0:
        return "明显低于上限，关注（可能是惜售或挑价）", "orange"
    return "严重没买满，信号意义强/需求差", "red"


def compute(op):
    """为单笔操作计算接纳率 / utilization / bid_cover / 报警级别。"""
    max_u, sub, acc = op["max_size_usd"], op["submitted_usd"], op["accepted_usd"]
    op = dict(op)
    op["max_100m"] = _to_100m(max_u)
    op["submitted_100m"] = _to_100m(sub)
    op["accepted_100m"] = _to_100m(acc)
    op["acceptance_rate_pct"] = round(acc / sub * 100, 2) if sub else None
    op["utilization_pct"] = round(acc / max_u * 100, 2) if max_u else None
    op["bid_cover"] = round(sub / max_u, 2) if max_u else None
    level, color = _util_level(op["utilization_pct"])
    op["level"] = level
    op["color"] = color
    op["is_long_end"] = any(k in op["maturity_bucket"] for k in LONG_END_KEYS)
    op["below_cap"] = op["utilization_pct"] is not None and op["utilization_pct"] < FULL_THRESHOLD
    return op


def monitor(data):
    """data 来自统一数据层 treasury_data_output.json，只做计算与报警判断。"""
    today = date.today().isoformat()
    upcoming = data["buyback"]["schedule"]
    results = [compute(op) for op in data["buyback"]["results"]]

    ops = results  # 已完成操作（升序：旧的在前，新的在后）
    latest = ops[-1] if ops else None

    # 连续低于上限统计（从最新往前数）
    streak = 0
    for op in reversed(ops):
        if op["below_cap"]:
            streak += 1
        else:
            break
    weakness = streak >= WEAK_STREAK

    # 长端提示：最近一次长端操作是否低于上限
    long_ops = [op for op in ops if op["is_long_end"]]
    latest_long = long_ops[-1] if long_ops else None
    long_end_note = None
    if latest_long is not None:
        if latest_long["below_cap"]:
            long_end_note = ("长端(%s) utilization=%.1f%% 低于上限 -> "
                             "对美债长端利率压制不及预期" % (latest_long["maturity_bucket"],
                                                   latest_long["utilization_pct"]))
        else:
            long_end_note = ("长端(%s) utilization=%.1f%% 接近/买满，"
                             "压长端预期延续" % (latest_long["maturity_bucket"],
                                              latest_long["utilization_pct"]))

    # 最近 2 周内的操作（供卡片展示）
    recent = [op for op in ops if op["operation_date"] >= (date.today() - timedelta(days=14)).isoformat()]

    qe_note = ("口径提示：回购低于上限 != QE——财政部回购多数用短债筹资（Cash Management 篮子 1Mo-2Y），"
               "不算美联储扩表，勿直接等同量化宽松")

    out = {
        "run_date": data["run_date"],
        "data_as_of": latest["operation_date"] if latest else None,
        "n_operations": len(ops),
        "streak_below_cap": streak,
        "weakness_signal": weakness,
        "weakness_note": ("财政部回购效力走弱（连续 %d 次低于上限）" % streak) if weakness else None,
        "long_end_note": long_end_note,
        "latest": latest,
        "recent": recent[-12:],
        "upcoming": [u for u in upcoming if u["operation_date"] >= today][:8],
        "qe_note": qe_note,
    }
    return out


def render(ind):
    lines = []
    lines.append("=" * 60)
    lines.append("美债监控 - 财政部回购   运行日: %s" % ind["run_date"])
    if ind["latest"]:
        lines.append("最近操作: %s | %s | %s" % (ind["latest"]["operation_date"],
                                                   ind["latest"]["operation_type"],
                                                   ind["latest"]["maturity_bucket"]))
        lines.append("  上限 %s 亿美元 | 报价 %s 亿 | 买入 %s 亿 | utilization %.1f%% (%s)"
                     % (ind["latest"]["max_100m"], ind["latest"]["submitted_100m"],
                        ind["latest"]["accepted_100m"], ind["latest"]["utilization_pct"],
                        ind["latest"]["level"]))
    lines.append("连续低于上限: %d 次 | 走弱信号: %s" % (ind["streak_below_cap"],
                                                   "有" if ind["weakness_signal"] else "无"))
    if ind["weakness_note"]:
        lines.append("  ★ %s" % ind["weakness_note"])
    if ind["long_end_note"]:
        lines.append("  ★ %s" % ind["long_end_note"])
    lines.append("-" * 60)
    lines.append("近期操作明细 (最近 %d 笔):" % len(ind["recent"]))
    for op in reversed(ind["recent"]):
        lines.append("  %s %-10s %-18s util=%6.1f%% cover=%4.2f acc=%6.2f亿/%6.2f亿 [%s]%s"
                     % (op["operation_date"], op["operation_type"].split()[0],
                        op["maturity_bucket"], op["utilization_pct"], op["bid_cover"],
                        op["accepted_100m"], op["submitted_100m"], op["level"],
                        " <长端>" if op["is_long_end"] else ""))
    lines.append("-" * 60)
    if ind["upcoming"]:
        lines.append("未来公告计划（%d 场）:" % len(ind["upcoming"]))
        for u in ind["upcoming"]:
            lines.append("  %s %-14s %-16s max=%s 亿" % (
                u["operation_date"], u["operation_type"], u["bucket"],
                round(u["max_size_usd"] / 1e8, 2)))
    lines.append(ind["qe_note"])
    lines.append("=" * 60)
    return "\n".join(lines)


def main():
    out_json = None
    if len(sys.argv) > 1 and sys.argv[1] == "--json":
        out_json = sys.argv[2] if len(sys.argv) > 2 else "treasury_buyback_output.json"

    with open("treasury_data_output.json", encoding="utf-8") as fp:
        data = json.load(fp)
    ind = monitor(data)
    print(render(ind))
    if out_json:
        with open(out_json, "w", encoding="utf-8") as fp:
            json.dump(ind, fp, ensure_ascii=False, indent=2)
        print("\nJSON 已落盘: %s" % out_json)


if __name__ == "__main__":
    main()