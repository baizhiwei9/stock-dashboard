# -*- coding: utf-8 -*-
"""动力学MACD 空头入场严格+持仓宽松 + a_span=5 EMA滤波变体
步骤:
1. 计算原始 a_raw = hist/ATR14
2. a = EMA(a_raw, span=5)  (5天滤波)
3. 多头: C>S50 且 v>0 且 a>0 (原版, 无jerk)
4. 空头入场: C<S50 且 v<0 且 a<0 且 a<v (|a|>|v|) 且 a_t<a_{t-1} (加速度继续向下)
5. 空头持仓: 一旦入场, 只要 v<0 且 C<S50 就继续持有
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


def compute(df, a_span=5):
    """a 先做 EMA(span=a_span) 滤波, 再判断"""
    c = df["close"]
    e12 = _ema(c, DX_EMA_FAST)
    e26 = _ema(c, DX_EMA_SLOW)
    dif = e12 - e26
    dea = _ema(dif, DX_SIG)
    hist = dif - dea
    s50 = _ema(c, DX_TREND)
    a14 = _atr(df, 14)
    v = dif / a14
    a_raw = hist / a14
    if a_span > 1:
        a = a_raw.ewm(span=a_span, adjust=False).mean()
    else:
        a = a_raw
    return pd.DataFrame({"close": c, "s50": s50, "v": v, "a": a, "a_raw": a_raw})


def daily_views(df, a_span=5):
    """逐日观点: a先滤波, 多头=原版, 空头=入场严格+持仓宽松"""
    raw = compute(df, a_span=a_span)
    views = np.full(len(raw), 0)
    v, a, s50, c = raw["v"].values, raw["a"].values, raw["s50"].values, raw["close"].values
    in_short = False

    for i in range(1, len(raw)):
        if np.isnan(v[i]) or np.isnan(a[i]) or np.isnan(s50[i]):
            views[i] = 0
            in_short = False
            continue

        # 多头: 完全原版 (不要求jerk)
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
            # 未持仓: 检查入场条件 (jerk + 势能>动能)
            if a[i] < v[i]:  # |a| > |v|, 势能>动能
                prev_a = a[i - 1]
                if not np.isnan(prev_a) and a[i] < prev_a:  # 加速度继续向下(更负)
                    in_short = True
                    views[i] = -1
                    continue

        views[i] = 0
        in_short = False

    return pd.Series(views, index=raw.index)
