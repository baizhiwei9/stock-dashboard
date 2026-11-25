# -*- coding: utf-8 -*-
"""动力学MACD 空头入场条件对比变体
- 变体A (v4_le): 入场条件 a_t <= a_{t-1} (允许持平)
- 变体B (v4_lt0): 入场条件 a_t < 0 (不要
    (jerk，只要求加速度为负)
持仓: 一旦入场, 只要 v<0 且 C<S50 就继续持有
多头: 完全原版不变
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
    return pd.DataFrame({"close": c, "s50": s50, "v": v, "a": a})


def daily_views(df, mode="le"):
    """
    mode:
      "le"  -> 入场条件: a_t <= a_{t-1} (允许持平)
      "lt0" -> 入场条件: a_t < 0 (只要求加速度为负，不比较前一天)
    """
    raw = compute(df)
    views = np.full(len(raw), 0)
    v, a, s50, c = raw["v"].values, raw["a"].values, raw["s50"].values, raw["close"].values
    in_short = False

    for i in range(1, len(raw)):
        if np.isnan(v[i]) or np.isnan(a[i]) or np.isnan(s50[i]):
            views[i] = 0
            in_short = False
            continue

        # 多头: 完全原版
        if c[i] > s50[i] and v[i] > 0 and a[i] > 0:
            views[i] = 1
            in_short = False
            continue

        # 空头逻辑
        if c[i] < s50[i] and v[i] < 0 and a[i] < 0:
            # 持仓中: 只要还在跌就继续持有
            if in_short:
                views[i] = -1
                continue
            # 未持仓: 检查入场条件
            if a[i] < v[i]:  # |a| > |v|, 势能>动能
                if mode == "le":
                    # 变体A: a_t <= a_{t-1} (允许持平)
                    prev_a = a[i - 1]
                    if not np.isnan(prev_a) and a[i] <= prev_a:
                        in_short = True
                        views[i] = -1
                        continue
                elif mode == "lt0":
                    # 变体B: 只要求 a < 0 (不再比较前一天)
                    in_short = True
                    views[i] = -1
                    continue

        views[i] = 0
        in_short = False

    return pd.Series(views, index=raw.index)
