# -*- coding: utf-8 -*-
"""动力学MACD 多头优化版
多头入场: C>S50 且 v>0 且 a>0 且 v>a (动能>势能, 新增)
空头: 完全原版 = C<S50 且 v<0 且 a<0
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


def daily_views(df):
    raw = compute(df)
    views = np.full(len(raw), 0)
    v, a, s50, c = raw["v"].values, raw["a"].values, raw["s50"].values, raw["close"].values
    for i in range(len(raw)):
        if np.isnan(v[i]) or np.isnan(a[i]) or np.isnan(s50[i]):
            views[i] = 0
            continue
        # 多头: 原版 + v>a
        if c[i] > s50[i] and v[i] > 0 and a[i] > 0 and v[i] > a[i]:
            views[i] = 1
            continue
        # 空头: 完全原版
        if c[i] < s50[i] and v[i] < 0 and a[i] < 0:
            views[i] = -1
            continue
        views[i] = 0
    return pd.Series(views, index=raw.index)
