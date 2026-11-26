# -*- coding: utf-8 -*-
"""动力学MACD 势能>动能做空变体
只修改空头逻辑:
  原版空头: C<S50 且 v<0 且 a<0
  新空头:   C<S50 且 v<0 且 a<0 且 a<v (|a|>|v|, 势能>动能) 且 a_t<a_{t-1} (加速度继续向下)
  原版多头: 完全不变 = C>S50 且 v>0 且 a>0
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
    """逐日观点: 多头=原版不变, 空头=势能>动能+jerk"""
    raw = compute(df)
    views = np.full(len(raw), 0)
    v, a, s50, c = raw["v"].values, raw["a"].values, raw["s50"].values, raw["close"].values
    
    for i in range(len(raw)):
        if np.isnan(v[i]) or np.isnan(a[i]) or np.isnan(s50[i]):
            views[i] = 0
            continue
        
        # 多头: 完全原版, 不加任何新约束
        if c[i] > s50[i] and v[i] > 0 and a[i] > 0:
            views[i] = 1
            continue
        
        # 空头: 原版 + 势能>动能 + jerk (加速度继续向下)
        if c[i] < s50[i] and v[i] < 0 and a[i] < 0:
            # 势能 > 动能: |a| > |v| 即 a < v (两者都为负)
            if a[i] < v[i]:
                # jerk: 加速度继续向下 (更负)
                if i > 0 and not np.isnan(a[i-1]) and a[i] < a[i-1]:
                    views[i] = -1
                    continue
        
        views[i] = 0
    
    return pd.Series(views, index=raw.index)


def signal(df):
    """返回 (view, detail)"""
    raw = compute(df)
    last = raw.iloc[-1]
    v, a = last["v"], last["a"]
    if np.isnan(v) or np.isnan(a) or np.isnan(last["s50"]):
        return "震荡", "指标未就绪"
    
    # 多头: 原版
    if last["close"] > last["s50"] and v > 0 and a > 0:
        return "多", f"原版多头(v={v:.3f}, a={a:.3f})"
    
    # 空头: 新版
    if last["close"] < last["s50"] and v < 0 and a < 0 and a < v:
        if len(raw) > 1:
            prev_a = raw["a"].iloc[-2]
            if not np.isnan(prev_a) and a < prev_a:
                return "空", f"势能>动能+jerk(v={v:.3f}, a={a:.3f}, a_prev={prev_a:.3f})"
    
    return "震荡", f"v={v:.3f}, a={a:.3f}"
