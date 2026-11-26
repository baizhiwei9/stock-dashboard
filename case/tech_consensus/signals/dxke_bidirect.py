# -*- coding: utf-8 -*-
"""动力学MACD 双向入场严格+持仓宽松变体
多头入场: C>S50 且 v>0 且 a>0 且 v>a (动能>势能) 且 a_t>=a_{t-1} (加速度不减速)
多头持仓: 入场后, 只要 C>S50 且 v>0 且 a>0 就继续持有 (不再要求a不减速)
空头入场: C<S50 且 v<0 且 a<0 且 a<v (势能>动能) 且 a_t<a_{t-1} (加速度继续向下)
空头持仓: 入场后, 只要 C<S50 且 v<0 且 a<0 就继续持有 (不再要求a继续向下)
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
    """逐日观点: 双向入场严格+持仓宽松（旧版，保留用于对比）"""
    raw = compute(df)
    views = np.full(len(raw), 0)
    v, a, s50, c = raw["v"].values, raw["a"].values, raw["s50"].values, raw["close"].values
    in_long = False
    in_short = False

    for i in range(1, len(raw)):
        if np.isnan(v[i]) or np.isnan(a[i]) or np.isnan(s50[i]):
            views[i] = 0
            in_long, in_short = False, False
            continue

        # 多头入场 + 持仓
        if c[i] > s50[i] and v[i] > 0 and a[i] > 0:
            if in_long:
                views[i] = 1
                continue
            # 未持仓: 检查严格入场条件
            if v[i] > a[i]:  # 动能>势能
                prev_a = a[i - 1]
                if not np.isnan(prev_a) and a[i] >= prev_a:  # 加速度不减速
                    in_long = True
                    views[i] = 1
                    continue

        # 空头入场 + 持仓
        elif c[i] < s50[i] and v[i] < 0 and a[i] < 0:
            if in_short:
                views[i] = -1
                continue
            # 未持仓: 检查严格入场条件
            if a[i] < v[i]:  # 势能>动能 (|a|>|v|)
                prev_a = a[i - 1]
                if not np.isnan(prev_a) and a[i] < prev_a:  # 加速度继续向下
                    in_short = True
                    views[i] = -1
                    continue

        views[i] = 0
        in_long, in_short = False, False

    return pd.Series(views, index=raw.index)


def etf_views(df):
    """ETF只做多版本
    做多: c > s50 AND v > 0 AND v > a（动能大于势能，加速度方向不限）
    不做空
    持仓宽松: c > s50 AND v > 0
    """
    raw = compute(df)
    views = np.full(len(raw), 0)
    v, a, s50, c = raw["v"].values, raw["a"].values, raw["s50"].values, raw["close"].values
    in_long = False

    for i in range(1, len(raw)):
        if np.isnan(v[i]) or np.isnan(a[i]) or np.isnan(s50[i]):
            views[i] = 0
            in_long = False
            continue

        # 多头入场 + 持仓
        if c[i] > s50[i] and v[i] > 0:
            if in_long:
                views[i] = 1
                continue
            # 未持仓: 检查严格入场条件
            if v[i] > a[i]:  # 动能大于势能
                in_long = True
                views[i] = 1
                continue

        views[i] = 0
        in_long = False

    return pd.Series(views, index=raw.index)


def futures_views(df):
    """期货多空双向版本
    做多: c > s50 AND v > 0 AND v > a AND a > 0（动能大于势能，且加速度向上）
    做空: c < s50 AND v < 0 AND a < v AND a < 0（势能大于动能，且加速度向下）
    持仓宽松结构保留: 多头持仓 c>s50&v>0&a>0; 空头持仓 c<s50&v<0&a<0
    """
    raw = compute(df)
    views = np.full(len(raw), 0)
    v, a, s50, c = raw["v"].values, raw["a"].values, raw["s50"].values, raw["close"].values
    in_long = False
    in_short = False

    for i in range(1, len(raw)):
        if np.isnan(v[i]) or np.isnan(a[i]) or np.isnan(s50[i]):
            views[i] = 0
            in_long, in_short = False, False
            continue

        # 多头入场 + 持仓
        if c[i] > s50[i] and v[i] > 0 and a[i] > 0:
            if in_long:
                views[i] = 1
                continue
            # 未持仓: 检查严格入场条件
            if v[i] > a[i]:  # 动能大于势能
                in_long = True
                views[i] = 1
                continue

        # 空头入场 + 持仓
        elif c[i] < s50[i] and v[i] < 0 and a[i] < 0:
            if in_short:
                views[i] = -1
                continue
            # 未持仓: 检查严格入场条件
            if a[i] < v[i]:  # 势能大于动能
                in_short = True
                views[i] = -1
                continue

        views[i] = 0
        in_long, in_short = False, False

    return pd.Series(views, index=raw.index)
