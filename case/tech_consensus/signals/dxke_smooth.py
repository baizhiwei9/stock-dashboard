# -*- coding: utf-8 -*-
"""动力学MACD a-滤波变体: 对 a 做 EMA 平滑后再判断正负
原版: a = hist/ATR (当日MACD柱, 极敏感)
变体: a_smooth = EMA(a, span=3) 或 EMA(a, span=5), 平滑后再判断 a>0 / a<0
其余逻辑与 dxke.py 完全一致
"""
import numpy as np
import pandas as pd
from config import DX_EMA_FAST, DX_EMA_SLOW, DX_SIG, DX_TREND

VIEW_MULTI = 1
VIEW_SHORT = -1
VIEW_FLAT = 0


def _ema(s, span):
    return s.ewm(span=span, adjust=False).mean()


def _atr(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(span=n, adjust=False).mean()


def compute(df, a_span=1):
    """a_span=1: 原版(无滤波); a_span=3/5: EMA平滑"""
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


def daily_views(df, a_span=1):
    """逐日观点, a 经 EMA 平滑后再判断"""
    raw = compute(df, a_span=a_span)
    views = np.full(len(raw), VIEW_FLAT)
    v, a, s50, c = raw["v"].values, raw["a"].values, raw["s50"].values, raw["close"].values
    for i in range(len(raw)):
        if np.isnan(v[i]) or np.isnan(a[i]) or np.isnan(s50[i]):
            views[i] = VIEW_FLAT
        elif c[i] > s50[i] and v[i] > 0 and a[i] > 0:
            views[i] = VIEW_MULTI
        elif c[i] < s50[i] and v[i] < 0 and a[i] < 0:
            views[i] = VIEW_SHORT
        else:
            views[i] = VIEW_FLAT
    return pd.Series(views, index=raw.index)
