# -*- coding: utf-8 -*-
"""动力学MACD 双向入场严格 + 2*ATR14 硬止损
入场: 同 dxke_bidirect (动能>势能+加速度不减速/继续向下)
持仓: 一旦入场, 跟踪最高价/最低价; 若反向突破 2*ATR14 则立即平仓
多头: 入场后跟踪最低价, 若 (最低价 - 入场价) / atr_entry < -2 则平仓
空头: 入场后跟踪最高价, 若 (最高价 - 入场价) / atr_entry > +2 则平仓
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
    return pd.DataFrame({"close": c, "high": df["high"], "low": df["low"],
                         "s50": s50, "v": v, "a": a, "atr14": a14})


def daily_views(df):
    """逐日观点: 双向入场严格 + 2*ATR14 硬止损"""
    raw = compute(df)
    views = np.full(len(raw), 0)
    v, a, s50, c = raw["v"].values, raw["a"].values, raw["s50"].values, raw["close"].values
    h, l, atr = raw["high"].values, raw["low"].values, raw["atr14"].values

    in_long = False
    in_short = False
    entry_price = 0.0
    atr_entry = 0.0
    track_high = 0.0   # 空头: 跟踪最高价
    track_low = 0.0    # 多头: 跟踪最低价

    for i in range(1, len(raw)):
        if np.isnan(v[i]) or np.isnan(a[i]) or np.isnan(s50[i]):
            views[i] = 0
            in_long, in_short = False, False
            continue

        # === 多头持仓中: 先检查止损 ===
        if in_long:
            if c[i] < track_low:
                track_low = c[i]
            # 2*ATR 止损: 从入场价回撤超过 2*ATR14
            if (track_low - entry_price) / atr_entry < -2.0:
                views[i] = 0
                in_long = False
                continue
            # 多头持仓最低要求
            if c[i] > s50[i] and v[i] > 0 and a[i] > 0:
                views[i] = 1
                continue
            else:
                views[i] = 0
                in_long = False
                continue

        # === 空头持仓中: 先检查止损 ===
        if in_short:
            if c[i] > track_high:
                track_high = c[i]
            # 2*ATR 止损: 反向涨超 2*ATR14
            if (track_high - entry_price) / atr_entry > 2.0:
                views[i] = 0
                in_short = False
                continue
            # 空头持仓最低要求
            if c[i] < s50[i] and v[i] < 0 and a[i] < 0:
                views[i] = -1
                continue
            else:
                views[i] = 0
                in_short = False
                continue

        # === 未持仓: 检查入场 ===
        # 多头入场
        if c[i] > s50[i] and v[i] > 0 and a[i] > 0:
            if v[i] > a[i]:  # 动能>势能
                prev_a = a[i - 1]
                if not np.isnan(prev_a) and a[i] >= prev_a:  # 加速度不减速
                    in_long = True
                    entry_price = c[i]
                    atr_entry = atr[i] if not np.isnan(atr[i]) else 0.0
                    track_low = c[i]
                    views[i] = 1
                    continue

        # 空头入场
        elif c[i] < s50[i] and v[i] < 0 and a[i] < 0:
            if a[i] < v[i]:  # 势能>动能
                prev_a = a[i - 1]
                if not np.isnan(prev_a) and a[i] < prev_a:  # 加速度继续向下
                    in_short = True
                    entry_price = c[i]
                    atr_entry = atr[i] if not np.isnan(atr[i]) else 0.0
                    track_high = c[i]
                    views[i] = -1
                    continue

        views[i] = 0

    return pd.Series(views, index=raw.index)
