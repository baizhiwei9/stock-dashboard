# -*- coding: utf-8 -*-
"""动力学MACD 空头延迟平仓确认变体
在 dxke_pe_v3 (入场严格+持仓宽松) 基础上:
- 入场: 同v3 = C<S50 且 v<0 且 a<0 且 a<v 且 a_t<a_{t-1}
- 持仓中: 只要基础空头条件(C<S50, v<0, a<0)成立就持空
- 平仓延迟: 当基础空头条件不再满足时, 不立即平仓, 延迟 N 天确认才平仓
  若延迟期间条件恢复, 计数清零继续持有
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


def daily_views(df, exit_delay=0):
    """
    exit_delay: 平仓延迟确认天数
      0 = 同v3 (立即平仓)
      2/3/5 = 条件不满足后延迟N天才平仓
    """
    raw = compute(df)
    views = np.full(len(raw), 0)
    v, a, s50, c = raw["v"].values, raw["a"].values, raw["s50"].values, raw["close"].values

    in_short = False
    exit_counter = 0

    for i in range(1, len(raw)):
        if np.isnan(v[i]) or np.isnan(a[i]) or np.isnan(s50[i]):
            views[i] = 0
            in_short = False
            exit_counter = 0
            continue

        # 多头: 完全原版
        if c[i] > s50[i] and v[i] > 0 and a[i] > 0:
            views[i] = 1
            in_short = False
            exit_counter = 0
            continue

        # 空头基础条件 (持仓最低要求)
        short_base = (c[i] < s50[i] and v[i] < 0 and a[i] < 0)

        if short_base:
            # 基础条件满足: 如果在持仓中就继续, 如果未持仓检查入场
            exit_counter = 0
            if in_short:
                views[i] = -1
                continue
            # 未持仓: 检查严格入场条件
            if a[i] < v[i]:  # |a|>|v|
                prev_a = a[i - 1]
                if not np.isnan(prev_a) and a[i] < prev_a:  # 继续向下
                    in_short = True
                    views[i] = -1
                    continue
        else:
            # 基础条件不满足: 如果在持仓中, 启动延迟计数
            if in_short:
                if exit_delay > 0:
                    exit_counter += 1
                    if exit_counter >= exit_delay:
                        in_short = False
                        exit_counter = 0
                        views[i] = 0
                        continue
                    else:
                        views[i] = -1  # 延迟期间继续持仓
                        continue
                else:
                    in_short = False
                    views[i] = 0
                    continue

        views[i] = 0
        in_short = False
        exit_counter = 0

    return pd.Series(views, index=raw.index)
