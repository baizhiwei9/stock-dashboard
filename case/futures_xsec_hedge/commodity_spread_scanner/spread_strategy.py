#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
价差回归策略 — 统一流水线接口
接口: init_spread() / process_cycle() / get_views() / get_cycle_signals()

与缠论分型版同一逻辑:
- process_cycle() 在 main.py 的每个5分钟周期被调用
- 信号存入 _latest_signals, 只在状态变化时产生信号
- 日线数据在盘中不变, 实际只在首次运行时计算一次
- 收盘后(15:05+)用当日最新数据重新计算, 发持仓汇总
- 开盘时(9:00/21:00)发全量持仓报告 (不管增量变化, 全量展示)
"""

import os
import json
import time
from datetime import datetime
from typing import List, Tuple, Optional

from .scanner.pair_table import load_pair_table, get_unique_symbols, Pair
from .scanner.calc_signal import analyze_pair
from .scanner.report import (
    build_signal_report, build_summary_report, build_opening_report,
    build_alert_report, compute_board_momentum_rank,
)
from .config.config import STATE_FILE, SYMBOL_NAME


# ===== 模块级状态 =====
_latest_signals: List[tuple] = []
_pair_results: List[dict] = []  # 最新一轮完整结果(供 get_views 使用)


def get_cycle_signals() -> List[tuple]:
    """获取本轮周期产生的信号 [(title, content), ...]"""
    return list(_latest_signals)


def load_state() -> dict:
    """加载持久化状态"""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                state = json.load(f)
                # 兼容旧格式: 确保有 pair_alerts
                if "pair_alerts" not in state:
                    state["pair_alerts"] = {}
                return state
        except Exception as e:
            print(f"  价差回归加载状态失败: {e}")
    return {"pair_dirs": {}, "pair_alerts": {},
            "last_calc_date": None, "opening_sent_date": None}


def save_state(state: dict):
    """保存持久化状态"""
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  价差回归保存状态失败: {e}")


def init_spread() -> Tuple[dict, dict]:
    """
    初始化价差回归策略
    返回: (pair_configs, state)
      pair_configs: {"pairs": [Pair, ...], "symbols": set}
      state: 持久化状态
    """
    pairs = load_pair_table()
    symbols = get_unique_symbols(pairs)
    state = load_state()

    pair_configs = {
        "pairs": pairs,
        "symbols": symbols,
    }

    print(f"  价差回归: 加载 {len(pairs)} 组配对, {len(symbols)} 个品种")
    return (pair_configs, state)


def _is_near_open(now: datetime) -> bool:
    """判断当前是否接近开盘时间 (日盘9:00-9:05, 夜盘21:00-21:05)"""
    hm = now.hour * 60 + now.minute
    # 日盘 9:00-9:05
    if 540 <= hm <= 545:
        return True
    # 夜盘 21:00-21:05
    if 1260 <= hm <= 1265:
        return True
    return False


def _is_trading_hours(now: datetime) -> bool:
    """
    判断是否在交易时段
    日盘: 9:00-15:00, 夜盘: 21:00-02:30 (跨午夜)
    """
    hm = now.hour * 60 + now.minute
    if 540 <= hm <= 900:      # 9:00 - 15:00
        return True
    if hm >= 1260:            # 21:00 - 23:59
        return True
    if hm <= 150:             # 00:00 - 02:30
        return True
    return False


def process_cycle(data_cache, pair_configs, state) -> list:
    """
    执行一个周期的价差回归分析
    复用 DataCache 的日线数据

    计算 vs 消息 分离:
    - 计算始终执行 (供 get_views/consensus 使用)
    - 消息只在状态变化/特殊时段产生 (增量推送, 不重复)

    特殊报告:
    - 开盘时(9:00/21:00): 全量持仓报告
    - 收盘后(15:05-15:30): 持仓汇总
    - ALERT(|z|≥2σ): 每对每天只提示一次
    """
    global _latest_signals, _pair_results

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    ts = now.strftime("%H:%M:%S")

    # 非交易时段完全跳过 (15:05-15:30 日盘收盘窗口也算)
    is_in_close_window = (now.hour == 15 and 5 <= now.minute <= 30)
    if not _is_trading_hours(now) and not is_in_close_window:
        _latest_signals = []
        return []

    # 判断是否需要生成消息 (vs 仅计算供 views)
    last_date = state.get("last_calc_date")
    is_after_close = is_in_close_window  # 仅日盘收盘后 15:05-15:30
    is_near_open = _is_near_open(now)
    opening_sent_date = state.get("opening_sent_date")
    # 是否需要生成消息: 首次计算 / 收盘后 / 开盘时
    should_send = (last_date != today) or is_after_close or is_near_open

    pairs = pair_configs["pairs"]
    pair_dirs = state.get("pair_dirs", {})
    pair_alerts = state.get("pair_alerts", {})
    # ALERT去重: 记录今天已发送过alert的配对, 避免重复推送
    alerts_sent_today = set(state.get("alerts_sent_today", []))
    alert_date = state.get("alert_date", "")
    if alert_date != today:
        alerts_sent_today = set()
        state["alert_date"] = today

    results = []
    state_changes = []   # ENTRY/CLOSE 信号 (实际操作)
    alert_signals = []    # ALERT 信号 (仅提示, 去重)

    for pair in pairs:
        try:
            bars_a = data_cache.get("daily", pair.leg_a)
            bars_b = data_cache.get("daily", pair.leg_b)

            if not bars_a or not bars_b:
                continue

            # 获取上一期方向和预警状态
            prev_dir = pair_dirs.get(pair.pair_id, 0)
            prev_alert = pair_alerts.get(pair.pair_id, 0)

            # 分析
            result = analyze_pair(pair, bars_a, bars_b, prev_dir, prev_alert)
            if result is None:
                continue

            results.append(result)

            # 检测状态变化 (仅当需要发消息时才收集)
            if should_send:
                new_dir = result["direction"]
                new_alert = result["alert"]
                signal = result["signal"]

                if signal in ("ENTRY_LONG", "ENTRY_SHORT"):
                    state_changes.append(result)
                    print(f"  {pair.pair_id}: {signal} (z={result['z']:+.2f})")
                elif signal in ("CLOSE_LONG", "CLOSE_SHORT"):
                    state_changes.append(result)
                    print(f"  {pair.pair_id}: {signal} (z={result['z']:+.2f})")
                elif signal in ("ALERT_LONG", "ALERT_SHORT"):
                    if pair.pair_id not in alerts_sent_today:
                        alert_signals.append(result)
                        alerts_sent_today.add(pair.pair_id)
                        print(f"  {pair.pair_id}: {signal} (z={result['z']:+.2f}, 提示关注)")

            # 更新方向和预警
            pair_dirs[pair.pair_id] = result["direction"]
            pair_alerts[pair.pair_id] = result["alert"]

        except Exception as e:
            continue

    # 更新状态 — 无论什么时段, 算过就更新日期
    state["pair_dirs"] = pair_dirs
    state["pair_alerts"] = pair_alerts
    state["last_calc_date"] = today
    state["alerts_sent_today"] = list(alerts_sent_today)
    state["alert_date"] = today
    save_state(state)

    # 始终更新 _pair_results (供 get_views 使用)
    _pair_results = results

    # 计算板块动量排名
    rankings = compute_board_momentum_rank(results)

    # 构建信号消息 (仅在 should_send 时)
    _latest_signals = []
    if should_send:
        messages = []

        # 1. 开盘全量报告
        if is_near_open and opening_sent_date != today:
            opening = build_opening_report(results, state, rankings)
            if opening:
                messages.append(opening)
            state["opening_sent_date"] = today
            save_state(state)
            print(f"  价差回归: 开盘全量报告已生成")

        # 2. ALERT 提示信号 (已去重)
        if alert_signals:
            alert_msgs = build_alert_report(alert_signals, state, rankings)
            messages.extend(alert_msgs)

        # 3. 增量操作信号 (开仓/平仓)
        if state_changes:
            sig_msgs = build_signal_report(state_changes, state, rankings)
            messages.extend(sig_msgs)

        # 4. 收盘持仓汇总 (仅日盘15:05-15:30)
        if is_after_close and results:
            summary = build_summary_report(results, state, rankings)
            if summary:
                messages.append(summary)

        _latest_signals = messages
        total = len(_latest_signals)
        if total > 0:
            print(f"  价差回归: {total} 条消息 (操作{len(state_changes)} 提示{len(alert_signals)})")
        else:
            print(f"  价差回归: 无状态变化")

    return []


def get_views(data_cache, pair_configs, state) -> dict:
    """
    获取当前观点 (供 consensus 使用)
    返回: {pair_id: {"name", "direction", "reason", "timeframe"}}
    """
    views = {}

    for r in _pair_results:
        if r is None:
            continue
        pair_id = r["pair_id"]
        direction = r.get("direction", 0)
        z = r.get("z", 0)
        signal = r.get("signal", "FLAT")

        leg_a = r["leg_a"]
        leg_b = r["leg_b"]
        name_a = SYMBOL_NAME.get(leg_a, leg_a)
        name_b = SYMBOL_NAME.get(leg_b, leg_b)

        name = f"{name_a}-{name_b}"
        reason = f"z={z:+.2f} {signal}"

        views[pair_id] = {
            "name": name,
            "direction": direction,  # 1=做多价差, -1=做空价差, 0=空仓
            "reason": reason,
            "timeframe": "日线",
        }

    return views
