#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
价差回归策略 — 独立运行入口
收盘后或手动运行, 自行获取日线数据, 跑17组配对信号计算

用法:
  python -m commodity_spread_scanner.run_spread_monitor              # 打印报告
  python -m commodity_spread_scanner.run_spread_monitor --send       # 发送到飞书
  python -m commodity_spread_scanner.run_spread_monitor --summary    # 仅持仓汇总
"""

import sys
import os
import argparse
import time
from datetime import datetime

# 确保项目根目录在 sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from data_fetcher import get_futures_daily
from commodity_spread_scanner.scanner.pair_table import load_pair_table, get_unique_symbols
from commodity_spread_scanner.scanner.calc_signal import analyze_pair
from commodity_spread_scanner.scanner.report import (
    build_signal_report, build_summary_report, build_full_scan_report,
    compute_board_momentum_rank,
)
from commodity_spread_scanner.config.config import STATE_FILE, FEISHU_CHAT_ID

import json


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"pair_dirs": {}, "last_calc_date": None}


def save_state(state: dict):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  保存状态失败: {e}")


def fetch_daily_data(symbols: set) -> dict:
    """获取所有品种的日线数据"""
    data = {}
    for symbol in sorted(symbols):
        try:
            print(f"  获取 {symbol} 日线数据...")
            bars = get_futures_daily(symbol)
            if bars:
                data[symbol] = bars
                print(f"    {symbol}: {len(bars)} 条")
            else:
                print(f"    {symbol}: 无数据")
            time.sleep(0.3)
        except Exception as e:
            print(f"    {symbol}: 获取失败 - {e}")
    return data


def run_scan(send: bool = False, summary_only: bool = False):
    """执行全量扫描"""
    print(f"\n{'=' * 60}")
    print(f"价差回归扫描器 — 独立运行")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'=' * 60}")

    # 加载配对表
    pairs = load_pair_table()
    symbols = get_unique_symbols(pairs)
    print(f"\n配对: {len(pairs)} 组, 品种: {len(symbols)} 个")

    # 获取数据
    print(f"\n[数据获取]")
    daily_data = fetch_daily_data(symbols)

    # 加载状态
    state = load_state()
    pair_dirs = state.get("pair_dirs", {})
    pair_alerts = state.get("pair_alerts", {})

    # 分析所有配对
    print(f"\n[信号分析]")
    results = []
    for pair in pairs:
        bars_a = daily_data.get(pair.leg_a, [])
        bars_b = daily_data.get(pair.leg_b, [])

        if not bars_a or not bars_b:
            print(f"  {pair.pair_id}: 数据不足")
            continue

        prev_dir = pair_dirs.get(pair.pair_id, 0)
        prev_alert = pair_alerts.get(pair.pair_id, 0)
        result = analyze_pair(pair, bars_a, bars_b, prev_dir, prev_alert)

        if result:
            results.append(result)
            pair_dirs[pair.pair_id] = result["direction"]
            pair_alerts[pair.pair_id] = result["alert"]
            signal = result["signal"]
            z = result["z"]
            stars = "⭐" if "ALERT" in signal else ("⭐⭐⭐⭐⭐" if signal in ("ENTRY_LONG", "ENTRY_SHORT", "HOLD") else "")
            print(f"  {pair.pair_id}: z={z:+.2f} dir={result['direction']} alert={result['alert']} signal={signal} {stars}")

    # 保存状态
    state["pair_dirs"] = pair_dirs
    state["pair_alerts"] = pair_alerts
    state["last_calc_date"] = datetime.now().strftime("%Y-%m-%d")
    save_state(state)

    # 计算板块动量排名
    rankings = compute_board_momentum_rank(results)

    # 生成报告
    print(f"\n[报告]")

    if summary_only:
        report = build_summary_report(results, state, rankings)
        reports = [report] if report else []
    else:
        # 全量扫描报告
        full_report = build_full_scan_report(results, rankings)
        reports = [full_report] if full_report else []

        # 信号变化报告
        state_changes = [r for r in results if r["signal"] in
                        ("ENTRY_LONG", "ENTRY_SHORT", "CLOSE_LONG", "CLOSE_SHORT")]
        if state_changes:
            signal_reports = build_signal_report(state_changes, state, rankings)
            reports.extend(signal_reports)

    if not reports:
        print("  无报告生成")
        return

    for title, content in reports:
        print(f"\n{'=' * 40}")
        print(f"  {title}")
        print(f"{'=' * 40}")
        print(content)

    # 发送飞书
    if send and reports:
        try:
            from notifier import send_to_chat
            for title, content in reports:
                print(f"\n  发送: {title}")
                send_to_chat(FEISHU_CHAT_ID, title, content)
                time.sleep(2)  # 限流
            print("  发送完成")
        except Exception as e:
            print(f"  发送失败: {e}")


def main():
    parser = argparse.ArgumentParser(description="商品期货价差回归扫描器")
    parser.add_argument("--send", action="store_true", help="发送到飞书群")
    parser.add_argument("--summary", action="store_true", help="仅输出持仓汇总")
    args = parser.parse_args()

    run_scan(send=args.send, summary_only=args.summary)


if __name__ == "__main__":
    main()
