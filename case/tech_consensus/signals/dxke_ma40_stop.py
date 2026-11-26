# -*- coding: utf-8 -*-
"""动力学MACD MA40趋势边界 + 三项强平变体
趋势边界: MA40 (简单移动平均, 替代EMA50)
多头: C>MA40 且 v>0 且 a>0 且 v>a
空头: C<MA40 且 v<0 且 a<0
强平/止损条件(持仓期间触发任一即平, T+1执行):
  ① 2倍ATR: 多头跌破 entry-2*ATR14, 空头涨破 entry+2*ATR14
  ② 3%回撤: 多头跌破 entry*0.97, 空头涨破 entry*1.03
  ③ MA40: 多头收盘价<MA40, 空头收盘价>MA40
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
    ma40 = c.rolling(40).mean()
    return pd.DataFrame({"close": c, "s50": s50, "ma40": ma40, "v": v, "a": a, "atr14": a14})


def daily_views(df):
    raw = compute(df)
    views = np.full(len(raw), 0)
    v, a, ma40, c, atr = (
        raw["v"].values, raw["a"].values, raw["ma40"].values,
        raw["close"].values, raw["atr14"].values,
    )
    in_long = False
    in_short = False
    entry_price = 0.0
    stop_2atr = 0.0
    stop_3pct = 0.0

    for i in range(1, len(raw)):
        if np.isnan(v[i]) or np.isnan(a[i]) or np.isnan(ma40[i]) or np.isnan(atr[i]):
            views[i] = 0
            in_long, in_short = False, False
            continue

        # === 止损检查: T日收盘触发 -> T+1执行平仓 ===
        if in_long:
            if c[i] < stop_2atr or c[i] < stop_3pct or c[i] < ma40[i]:
                views[i] = 0
                in_long = False
                continue
        if in_short:
            if c[i] > stop_2atr or c[i] > stop_3pct or c[i] > ma40[i]:
                views[i] = 0
                in_short = False
                continue

        # === 多头入场 + 持仓 ===
        if c[i] > ma40[i] and v[i] > 0 and a[i] > 0:
            if in_long:
                views[i] = 1
                continue
            if v[i] > a[i]:
                in_long = True
                entry_price = c[i]
                stop_2atr = entry_price - 2 * atr[i]
                stop_3pct = entry_price * 0.97
                views[i] = 1
                continue

        # === 空头入场 + 持仓 ===
        elif c[i] < ma40[i] and v[i] < 0 and a[i] < 0:
            if in_short:
                views[i] = -1
                continue
            in_short = True
            entry_price = c[i]
            stop_2atr = entry_price + 2 * atr[i]
            stop_3pct = entry_price * 1.03
            views[i] = -1
            continue

        views[i] = 0
        in_long = False
        in_short = False

    return pd.Series(views, index=raw.index)
