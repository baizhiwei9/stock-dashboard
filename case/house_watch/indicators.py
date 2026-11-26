# -*- coding: utf-8 -*-
"""指标计算：KDJ / MACD / VWAP ±σ / 变化率 —— 京沪监控用。

全部是纯函数，不读库不联网，方便单测。参数沿用此前的口径（KDJ(9,3,3)、
MACD(12,26,9)、VWAP 12 个月窗口），与 `data_hub.house.analysis` 一致。
"""
from __future__ import annotations

import math


def ema(vals, n):
    k = 2.0 / (n + 1.0)
    out, prev = [], None
    for v in vals:
        prev = v if prev is None else v * k + prev * (1 - k)
        out.append(prev)
    return out


def kdj(close, n=9, k_n=3, d_n=3):
    """KDJ：RSV 用 n 期高低，K/D 做 SMA(X,3)（等价 ewm(alpha=1/3)），J=3K-2D。"""
    ks, ds, js = [], [], []
    k_prev = d_prev = 50.0
    for i, c in enumerate(close):
        lo = min(close[max(0, i - n + 1): i + 1])
        hi = max(close[max(0, i - n + 1): i + 1])
        rsv = 50.0 if hi == lo else (c - lo) / (hi - lo) * 100.0
        k_prev = (rsv + (k_n - 1) * k_prev) / k_n
        d_prev = (k_prev + (d_n - 1) * d_prev) / d_n
        ks.append(round(k_prev, 2))
        ds.append(round(d_prev, 2))
        js.append(round(3 * k_prev - 2 * d_prev, 2))
    return ks, ds, js


def macd(close, fast=12, slow=26, signal=9):
    dif = [a - b for a, b in zip(ema(close, fast), ema(close, slow))]
    dea = ema(dif, signal)
    hist = [2 * (a - b) for a, b in zip(dif, dea)]
    return [round(x, 3) for x in dif], [round(x, 3) for x in dea], [round(x, 3) for x in hist]


def level_from_mom(series, key="mom", start=100.0):
    """环比连乘还原价格水平；环比缺失的月份沿用前值（不能当 0）。"""
    level, out = start, []
    for m in series:
        v = m.get(key)
        if v:
            level *= v / 100.0
        out.append(round(level, 3))
    return out


def vwap_bands(prices, weights=None, window=12):
    """滚动 VWAP 与 ±1σ/±2σ 通道。

    权重是当月**网签成交套数**；某窗口内有效权重不足两期就退化为等权，并在
    ``equal_windows`` 里计数——新城市/新库的时候权重根本没攒够，不能装作成交量加权。
    """
    n = len(prices)
    weights = list(weights or [])
    weighted = bool(weights) and any(w for w in weights)
    weights = (weights + [0.0] * n)[:n] if weights else [1.0] * n
    out = {"vwap": [], "sigma": [], "equal_windows": 0, "weighted": weighted,
           "window": window}
    for i in range(n):
        lo = max(0, i - window + 1)
        ps, ws = prices[lo:i + 1], weights[lo:i + 1]
        if sum(1 for w in ws if w) < 2:
            ws = [1.0] * len(ps)
            out["equal_windows"] += 1
        sw = sum(ws) or len(ps)
        m = sum(p * w for p, w in zip(ps, ws)) / sw
        var = sum(w * (p - m) ** 2 for p, w in zip(ps, ws)) / sw
        out["vwap"].append(round(m, 3))
        out["sigma"].append(round(math.sqrt(var), 3))
    for name, k in (("upper1", 1), ("lower1", -1), ("upper2", 2), ("lower2", -2)):
        out[name] = [None if s is None else round(v + k * s, 3)
                     for v, s in zip(out["vwap"], out["sigma"])]
    return out


def zscore(price, vwap, sigma):
    """偏离 VWAP 的标准差倍数；σ=0 时按方向给 ±inf（平线突然跳起也要能判穿越）。"""
    if price is None or vwap in (None, 0):
        return None
    if not sigma:
        if price == vwap:
            return 0.0
        return float("inf") if price > vwap else float("-inf")
    return (price - vwap) / sigma


def pct(cur, prev):
    if cur is None or prev in (None, 0):
        return None
    return round((cur - prev) / float(prev) * 100.0, 2)