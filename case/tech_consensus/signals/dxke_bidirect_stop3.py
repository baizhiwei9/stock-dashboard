# -*- coding: utf-8 -*-
"""动力学MACD 双向入场严格 + 三项强平条件
入场条件（严格）:
  多头: C>S50 且 v>0 且 a>0 且 v>a 且 a_t>=a_{t-1}
  空头: C<S50 且 v<0 且 a<0 且 a<v 且 a_t<a_{t-1}
持仓条件（宽松）:
  多头: C>S50 且 v>0 且 a>0
  空头: C<S50 且 v<0 且 a<0
强平/止损条件（入场后触发任一即平）:
  ① 2倍ATR: 多头跌破 entry-2*ATR14, 空头涨破 entry+2*ATR14
  ② 3%回撤: 多头跌破 entry*0.97, 空头涨破 entry*1.03
  ③ MA40: 多头收盘价<MA40, 空头收盘价>MA40
注意: 强平信号当日收盘触发 -> T+1执行平仓
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
    return pd.DataFrame({"close": c, "s50": s50, "v": v, "a": a, "atr14": a14, "ma40": ma40})


def daily_views(df):
    raw = compute(df)
    views = np.full(len(raw), 0)
    v, a, s50, c, atr, ma40 = (
        raw["v"].values, raw["a"].values, raw["s50"].values,
        raw["close"].values, raw["atr14"].values, raw["ma40"].values,
    )
    in_long = False
    in_short = False
    entry_price = 0.0
    stop_2atr = 0.0
    stop_3pct = 0.0
    stop_ma40_dir = 0  # 1=多头需>MA40, -1=空头需<MA40

    for i in range(1, len(raw)):
        if np.isnan(v[i]) or np.isnan(a[i]) or np.isnan(s50[i]) or np.isnan(atr[i]) or np.isnan(ma40[i]):
            views[i] = 0
            in_long = False
            in_short = False
            continue

        # === 强平检查: T日收盘触发 -> T+1执行平仓 ===
        # 用上一日持仓状态判断是否需要平仓
        if in_long:
            # 三个条件任一触发: 收盘价<2倍ATR止损位 或 收盘价<3%回撤位 或 收盘价<MA40
            if c[i] < stop_2atr or c[i] < stop_3pct or c[i] < ma40[i]:
                views[i] = 0  # 平仓信号 -> 次日空仓
                in_long = False
                continue
        if in_short:
            if c[i] > stop_2atr or c[i] > stop_3pct or c[i] > ma40[i]:
                views[i] = 0
                in_short = False
                continue

        # === 多头入场 + 持仓 ===
        if c[i] > s50[i] and v[i] > 0 and a[i] > 0:
            if in_long:
                views[i] = 1
                continue
            if v[i] > a[i]:
                prev_a = a[i - 1]
                if not np.isnan(prev_a) and a[i] >= prev_a:
                    in_long = True
                    entry_price = c[i]
                    stop_2atr = entry_price - 2 * atr[i]
                    stop_3pct = entry_price * 0.97
                    views[i] = 1
                    continue

        # === 空头入场 + 持仓 ===
        elif c[i] < s50[i] and v[i] < 0 and a[i] < 0:
            if in_short:
                views[i] = -1
                continue
            if a[i] < v[i]:
                prev_a = a[i - 1]
                if not np.isnan(prev_a) and a[i] < prev_a:
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
