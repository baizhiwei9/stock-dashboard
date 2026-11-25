# -*- coding: utf-8 -*-
"""动力学MACD 动能-势能版 (PE = Potential Energy)
物理直觉:
- 动能 = v = DIF/ATR (速度/动量)
- 势能 = a = MACD柱/ATR (加速度/趋势强度)
- 做多: 动能>势能 (v>a, 同正) → 趋势有速度支撑
- 做空: 势能>动能 (a<v, 同负) → 趋势在加速, 速度还没跟上, 做空窗口
- 出场: 加速度停止同向变化 (a_{t} 与 a_{t-1} 异号或变平)
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


def daily_views(df, require_jerk=True):
    """
    require_jerk: 是否要求加速度继续同向变化 (a_t 与 a_{t-1} 同号)
    """
    raw = compute(df)
    views = np.full(len(raw), 0)  # 0=震荡
    v, a, s50, c = raw["v"].values, raw["a"].values, raw["s50"].values, raw["close"].values

    for i in range(1, len(raw)):
        if np.isnan(v[i]) or np.isnan(a[i]) or np.isnan(s50[i]):
            views[i] = 0
            continue

        # 判断动能vs势能
        # 同号时才比较
        same_sign = (v[i] > 0 and a[i] > 0) or (v[i] < 0 and a[i] < 0)

        if not same_sign:
            views[i] = 0
            continue

        # 加速度继续同向变化? (jerk)
        if require_jerk and i > 0:
            prev_a = a[i - 1]
            if np.isnan(prev_a):
                views[i] = 0
                continue
            # 当前加速度方向应与前一时刻一致或更强
            # 多: a_t > 0 and a_t >= a_{t-1} (加速向上)
            # 空: a_t < 0 and a_t <= a_{t-1} (加速向下, 更负)
            if a[i] > 0 and a[i] < prev_a:
                views[i] = 0
                continue
            if a[i] < 0 and a[i] > prev_a:
                views[i] = 0
                continue

        # 做多: 动能>势能 (v > a, 都为正)
        if c[i] > s50[i] and v[i] > 0 and a[i] > 0 and v[i] > a[i]:
            views[i] = 1
            continue

        # 做空: 势能>动能 (a < v, 都为负, 即 |a| > |v|)
        if c[i] < s50[i] and v[i] < 0 and a[i] < 0 and a[i] < v[i]:
            views[i] = -1
            continue

        views[i] = 0

    return pd.Series(views, index=raw.index)


def signal(df, require_jerk=True):
    """返回 (view, detail)"""
    raw = compute(df)
    last = raw.iloc[-1]
    v, a = last["v"], last["a"]
    if np.isnan(v) or np.isnan(a) or np.isnan(last["s50"]):
        return "震荡", "指标未就绪"

    # 检查jerk
    if require_jerk and len(raw) > 1:
        prev_a = raw["a"].iloc[-2]
        if not np.isnan(prev_a):
            if a > 0 and a < prev_a:
                return "震荡", f"a减速 v={v:.3f} a={a:.3f}"
            if a < 0 and a > prev_a:
                return "震荡", f"a减速 v={v:.3f} a={a:.3f}"

    same_sign = (v > 0 and a > 0) or (v < 0 and a < 0)
    if not same_sign:
        return "震荡", f"v/a异号 v={v:.3f} a={a:.3f}"

    if last["close"] > last["s50"] and v > 0 and a > 0 and v > a:
        return "多", f"动能>势能(v={v:.3f}>a={a:.3f})"
    if last["close"] < last["s50"] and v < 0 and a < 0 and a < v:
        return "空", f"势能>动能(|a|={abs(a):.3f}>|v|={abs(v):.3f})"

    return "震荡", f"v={v:.3f} a={a:.3f}"
