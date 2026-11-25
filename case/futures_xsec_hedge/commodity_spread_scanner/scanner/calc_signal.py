#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
核心信号引擎: 价差/比价 → z-score → 信号状态机
"""

import numpy as np
from typing import List, Tuple, Optional


def bars_to_close_array(bars: list) -> np.ndarray:
    """将 bar list[dict] 转为收盘价序列 (np.ndarray, float64)"""
    if not bars:
        return np.array([], dtype=np.float64)
    closes = []
    for b in bars:
        try:
            closes.append(float(b["close"]))
        except (KeyError, TypeError, ValueError):
            closes.append(np.nan)
    return np.array(closes, dtype=np.float64)


def bars_to_close_list(bars: list) -> List[float]:
    """将 bar list[dict] 转为收盘价列表"""
    if not bars:
        return []
    result = []
    for b in bars:
        try:
            result.append(float(b["close"]))
        except (KeyError, TypeError, ValueError):
            result.append(None)
    return result


def calc_spread_ratio(close_a: np.ndarray, close_b: np.ndarray,
                      method: str) -> np.ndarray:
    """
    计算价差或比价序列
    method="spread": value = close_a - close_b
    method="ratio":  value = close_a / close_b
    返回: 与输入等长的序列, 无效位置为 NaN
    """
    if len(close_a) != len(close_b):
        min_len = min(len(close_a), len(close_b))
        close_a = close_a[-min_len:]
        close_b = close_b[-min_len:]

    if method == "spread":
        return close_a - close_b
    elif method == "ratio":
        with np.errstate(divide="ignore", invalid="ignore"):
            result = np.where(close_b != 0, close_a / close_b, np.nan)
        return result.astype(np.float64)
    else:
        raise ValueError(f"未知calc_method: {method}")


def rolling_zscore(values: np.ndarray, window: int = 756) -> np.ndarray:
    """
    滚动z-score: z = (x - mean) / std
    窗口不足时返回NaN, std=0时返回0
    """
    n = len(values)
    z = np.full(n, np.nan, dtype=np.float64)

    for i in range(n):
        start = max(0, i - window + 1)
        seg = values[start:i + 1]
        # 去掉NaN
        seg = seg[~np.isnan(seg)]
        if len(seg) < min(60, window):
            continue
        mean = np.mean(seg)
        std = np.std(seg)
        if std < 1e-10:
            z[i] = 0.0
        else:
            z[i] = (values[i] - mean) / std

    return z


def calc_pct_rank(values: np.ndarray, window: int = 756) -> np.ndarray:
    """
    滚动历史分位数: 当前值在过去window天中的百分位排名
    返回 [0, 1] 区间, 0.5 = 中位数
    """
    n = len(values)
    pct = np.full(n, np.nan, dtype=np.float64)

    for i in range(n):
        start = max(0, i - window + 1)
        seg = values[start:i + 1]
        seg = seg[~np.isnan(seg)]
        if len(seg) < 20:
            continue
        current = values[i]
        if np.isnan(current):
            continue
        count_below = np.sum(seg < current)
        pct[i] = count_below / len(seg)

    return pct


def signal_state_machine(z_current: float, prev_dir: int,
                         prev_alert: int = 0,
                         entry_z: float = 2.0,
                         exit_z: float = 0.5) -> Tuple[int, int, str]:
    """
    信号状态机 (两步入场)

    第一步: |z| >= entry_z → ALERT (提示, 不操作)
    第二步: z 开始回归 → ENTRY (真正开仓)
    退出: |z| <= exit_z → CLOSE (平仓)

    参数:
      z_current: 当前z值
      prev_dir: 上次方向 0=空仓, 1=持多价差, -1=持空价差
      prev_alert: 上次预警方向 0=无, 1=z偏低预警(将做多), -1=z偏高预警(将做空)

    返回: (new_dir, new_alert, signal)
      signal: "ALERT_LONG" / "ALERT_SHORT" / "ENTRY_LONG" / "ENTRY_SHORT"
              / "CLOSE_LONG" / "CLOSE_SHORT" / "HOLD" / "FLAT"
    """
    if np.isnan(z_current):
        return 0, 0, "FLAT"

    # ===== 空仓状态 =====
    if prev_dir == 0:
        if prev_alert == 0:
            # 无预警: 检查是否超出阈值
            if z_current >= entry_z:
                return 0, -1, "ALERT_SHORT"  # z偏高, 预警做空价差
            elif z_current <= -entry_z:
                return 0, 1, "ALERT_LONG"    # z偏低, 预警做多价差
            else:
                return 0, 0, "FLAT"

        elif prev_alert == 1:
            # 已预警做多价差 (z偏低), 等z开始回归
            if z_current > -entry_z:
                # z回升了 = 回归开始 → 开仓做多价差
                return 1, 0, "ENTRY_LONG"
            elif abs(z_current) < entry_z:
                # z回到中性区, 预警取消
                return 0, 0, "FLAT"
            else:
                # z仍在低位, 继续等
                return 0, 1, "FLAT"

        elif prev_alert == -1:
            # 已预警做空价差 (z偏高), 等z开始回归
            if z_current < entry_z:
                # z回落了 = 回归开始 → 开仓做空价差
                return -1, 0, "ENTRY_SHORT"
            elif abs(z_current) < entry_z:
                # z回到中性区, 预警取消
                return 0, 0, "FLAT"
            else:
                # z仍在高位, 继续等
                return 0, -1, "FLAT"

    # ===== 持有多头价差 (买A卖B, 预期z回升) =====
    elif prev_dir == 1:
        if z_current <= -exit_z:
            return 1, 0, "HOLD"           # z仍偏负, 回归进行中
        else:
            return 0, 0, "CLOSE_LONG"     # z回到中性, 平仓

    # ===== 持有空头价差 (卖A买B, 预期z下降) =====
    elif prev_dir == -1:
        if z_current >= exit_z:
            return -1, 0, "HOLD"          # z仍偏高, 回归进行中
        else:
            return 0, 0, "CLOSE_SHORT"    # z回到中性, 平仓

    return 0, 0, "FLAT"


def calc_momentum(bars: list, window: int = 5) -> Tuple[int, float]:
    """
    计算近N日动量
    返回: (direction, pct_change)
      direction: 1=上涨, -1=下跌, 0=持平
      pct_change: 实际涨跌幅 (小数, 如 0.02 = +2%)
    """
    closes = bars_to_close_array(bars)
    closes = closes[~np.isnan(closes)]
    if len(closes) < window + 1:
        return 0, 0.0
    prev_close = closes[-1 - window]
    curr_close = closes[-1]
    if prev_close == 0:
        return 0, 0.0
    pct = (curr_close - prev_close) / prev_close
    direction = 1 if pct > 0.001 else (-1 if pct < -0.001 else 0)
    return direction, round(pct, 4)


def calc_momentum_direction(bars: list, window: int = 5) -> int:
    """
    计算近N日动量方向 (兼容旧接口)
    返回: 1=上涨, -1=下跌, 0=持平
    """
    d, _ = calc_momentum(bars, window)
    return d


def analyze_pair(pair, bars_a: list, bars_b: list,
                 prev_dir: int = 0,
                 prev_alert: int = 0) -> dict:
    """
    对单组配对进行完整分析 (两步入场逻辑)
    返回: {
        pair_id, value, z, pct_rank, direction, alert, signal,
        momentum_a, momentum_b, momentum_star, ...
    }
    """
    close_a = bars_to_close_array(bars_a)
    close_b = bars_to_close_array(bars_b)

    if len(close_a) < 60 or len(close_b) < 60:
        return None

    # 对齐长度
    min_len = min(len(close_a), len(close_b))
    close_a = close_a[-min_len:]
    close_b = close_b[-min_len:]

    # 计算价差/比价
    values = calc_spread_ratio(close_a, close_b, pair.calc_method)

    # 滚动z-score
    z_series = rolling_zscore(values, window=pair.history_window)

    # 当前z
    z_current = z_series[-1] if len(z_series) > 0 else np.nan
    if np.isnan(z_current):
        return None

    # 历史分位
    pct_series = calc_pct_rank(values, window=pair.history_window)
    pct_current = pct_series[-1] if len(pct_series) > 0 else np.nan

    # 信号状态机 (两步入场: alert → entry)
    new_dir, new_alert, signal = signal_state_machine(
        z_current, prev_dir, prev_alert,
        pair.entry_z, pair.exit_z
    )

    # 动量 (方向 + 涨跌幅百分比)
    mom_dir_a, mom_pct_a = calc_momentum(bars_a)
    mom_dir_b, mom_pct_b = calc_momentum(bars_b)
    # 动量星标: 标记动量更强的一侧
    if new_dir == 1:  # 做多价差 → 买A卖B
        momentum_star = "A*" if mom_dir_a >= mom_dir_b else "B*"
    elif new_dir == -1:  # 做空价差 → 卖A买B
        momentum_star = "B*" if mom_dir_b >= mom_dir_a else "A*"
    else:
        momentum_star = ""

    # 当前价差/比价值
    current_value = values[-1] if len(values) > 0 and not np.isnan(values[-1]) else 0

    return {
        "pair_id": pair.pair_id,
        "leg_a": pair.leg_a,
        "leg_b": pair.leg_b,
        "board": pair.board,
        "relation_type": pair.relation_type,
        "calc_method": pair.calc_method,
        "value": round(float(current_value), 4),
        "z": round(float(z_current), 3),
        "pct_rank": round(float(pct_current), 3) if not np.isnan(pct_current) else None,
        "direction": new_dir,
        "alert": new_alert,
        "signal": signal,
        "momentum_a": mom_dir_a,
        "momentum_b": mom_dir_b,
        "momentum_pct_a": mom_pct_a,
        "momentum_pct_b": mom_pct_b,
        "momentum_star": momentum_star,
        "close_a": round(float(close_a[-1]), 2),
        "close_b": round(float(close_b[-1]), 2),
    }
