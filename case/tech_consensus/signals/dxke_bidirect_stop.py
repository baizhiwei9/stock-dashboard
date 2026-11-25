# -*- coding: utf-8 -*-
"""动力学MACD 双向入场严格+持仓宽松 + 2倍ATR止损变体
多头止损 = 入场价 - 2*ATR14, 收盘跌破即触发平仓信号
空头止损 = 入场价 + 2*ATR14, 收盘涨破即触发平仓信号
T+1执行: 止损信号当日收盘触发 -> 次日平仓
"""
import numpy as np
import pandas as pd
from config import DX_EMA_FAST, DX_EMA_SLOW, DX_SIG, DX_TREND


def _ema(s, span):
    return s.ewm(span=span, adjust=False).mean()


def _atr(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(span=n, adjust=False).mean()


def compute(df):
    c = df["close"]
    e12 = _ema(c, DX_EMA_FAST)
    e26 = _ema(c, DX_EMA_SLOW)
    dif = e12 - e26
    dea = _ema(dif, DX_SIG)
    hist = dif - dea
    s50 = _ema(c, DX_TREND)
    a14 = _atr(df, 14)
    v = dif / a14
    a = hist / a14
    return pd.DataFrame({"close": c, "s50": s50, "v": v, "a": a, "atr14": a14})


def daily_views(df):
    """逐日观点: 双向入场严格+持仓宽松 + 2倍ATR止损"""
    raw = compute(df)
    views = np.full(len(raw), 0)
    v, a, s50, c, atr = (
        raw["v"].values, raw["a"].values, raw["s50"].values,
        raw["close"].values, raw["atr14"].values,
    )
    in_long = False
    in_short = False
    entry_price = 0.0

    for i in range(1, len(raw)):
        if np.isnan(v[i]) or np.isnan(a[i]) or np.isnan(s50[i]) or np.isnan(atr[i]):
            views[i] = 0
            in_long, in_short = False, False
            continue

        # === 止损检查 (收盘触发) ===
        if in_long:
            stop_loss = entry_price - 2 * atr[i]
            if c[i] < stop_loss:
                views[i] = 0  # 止损平仓
                in_long = False
                continue
        if in_short:
            stop_loss = entry_price + 2 * atr[i]
            if c[i] > stop_loss:
                views[i] = 0  # 止损平仓
                in_short = False
                continue

        # === 多头入场 + 持仓 ===
        if c[i] > s50[i] and v[i] > 0 and a[i] > 0:
            if in_long:
                views[i] = 1
                continue
            # 未持仓: 检查严格入场条件
            if v[i] > a[i]:  # 动能>势能
                prev_a = a[i - 1]
                if not np.isnan(prev_a) and a[i] >= prev_a:  # 加速度不减速
                    in_long = True
                    entry_price = c[i]
                    views[i] = 1
                    continue

        # === 空头入场 + 持仓 ===
        elif c[i] < s50[i] and v[i] < 0 and a[i] < 0:
            if in_short:
                views[i] = -1
                continue
            # 未持仓: 检查严格入场条件
            if a[i] < v[i]:  # 势能>动能 (|a|>|v|)
                prev_a = a[i - 1]
                if not np.isnan(prev_a) and a[i] < prev_a:  # 加速度继续向下
                    in_short = True
                    entry_price = c[i]
                    views[i] = -1
                    continue

        # 不满足任何条件 -> 震荡, 重置
        views[i] = 0
        in_long, in_short = False, False

    return pd.Series(views, index=raw.index)
