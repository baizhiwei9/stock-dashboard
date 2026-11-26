# -*- coding: utf-8 -*-
"""动力学MACD 方向+权重变体
方向由 v + S50 决定: v>0 且 C>S50 = 多; v<0 且 C<S50 = 空; 否则震荡
权重由 a 决定: a 同向(多a>0 / 空a<0) = 全仓(1.0); a 反向或零 = 1/3仓(0.33)
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


def daily_views(df, flat_weight=0.33):
    """返回连续权重序列: 多=+1.0/+0.33, 空=-1.0/-0.33, 震荡=0"""
    raw = compute(df)
    w = np.zeros(len(raw), dtype=float)
    v, a, s50, c = raw["v"].values, raw["a"].values, raw["s50"].values, raw["close"].values
    for i in range(len(raw)):
        if np.isnan(v[i]) or np.isnan(a[i]) or np.isnan(s50[i]):
            w[i] = 0.0
            continue
        # 方向判定
        if v[i] > 0 and c[i] > s50[i]:
            # 多头
            w[i] = 1.0 if a[i] > 0 else flat_weight
        elif v[i] < 0 and c[i] < s50[i]:
            # 空头
            w[i] = -1.0 if a[i] < 0 else -flat_weight
        else:
            w[i] = 0.0
    return pd.Series(w, index=raw.index)
