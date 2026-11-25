# -*- coding: utf-8 -*-
"""价差统计稳健性：分位数、Z 值、ADF、Hurst、半衰期、波动率分位。

所有指标只使用 asof 当日及以前的数据，窗口为最近 percentile_window 个交易观测，
默认 252（约一个交易年），并由 min_observations 强制"至少近一年"。
"""
from __future__ import annotations

import warnings
from typing import Dict, List, Optional

import numpy as np
import pandas as pd


def percentile_rank(window: pd.Series, value: float) -> float:
    arr = pd.Series(window).dropna().to_numpy(dtype=float)
    if len(arr) == 0 or not np.isfinite(value):
        return float("nan")
    return float(np.mean(arr <= value))


def adf_pvalue(series: pd.Series) -> float:
    arr = pd.Series(series).dropna().to_numpy(dtype=float)
    if len(arr) < 30 or np.std(arr) == 0:
        return float("nan")
    try:
        from statsmodels.tsa.stattools import adfuller
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return float(adfuller(arr, regression="c", autolag="AIC")[1])
    except Exception:
        return float("nan")


def hurst_exponent(series: pd.Series) -> float:
    """DFA（去趋势波动分析）估计 Hurst 指数。

    对价差水平序列先积分成 profile，再按尺度做局部线性去趋势，
    F(s) ∝ s^(H+1)，故 H = 拟合斜率 - 1。随机游走≈0.5，趋势>0.5，均值回归<0.5。
    相比 R/S，DFA 对含趋势的价差更稳健。
    """
    x = pd.Series(series).dropna().to_numpy(dtype=float)
    n = len(x)
    if n < 100 or np.std(x) == 0:
        return float("nan")
    profile = np.cumsum(x - x.mean())
    scales = np.unique(np.geomspace(8, max(9, n // 4), 18).astype(int))
    fluct: List[float] = []
    used: List[int] = []
    for s in scales:
        k = n // s
        if k < 2:
            continue
        t = np.arange(s)
        sq = []
        for i in range(k):
            seg = profile[i * s:(i + 1) * s]
            coef = np.polyfit(t, seg, 1)
            resid = seg - (coef[0] * t + coef[1])
            sq.append(float(np.mean(resid ** 2)))
        f = float(np.sqrt(np.mean(sq)))
        if f > 0:
            fluct.append(f)
            used.append(int(s))
    if len(fluct) < 6:
        return float("nan")
    slope = float(np.polyfit(np.log(used), np.log(fluct), 1)[0])
    return float(np.clip(slope - 1.0, 0.0, 1.0))


def half_life(series: pd.Series) -> float:
    """AR(1) 半衰期：Δy_t = a + b·y_{t-1}，half_life = -ln2 / ln(1+b)。"""
    arr = pd.Series(series).dropna().to_numpy(dtype=float)
    if len(arr) < 30:
        return float("nan")
    y = arr[1:]
    x = arr[:-1]
    dy = y - x
    X = np.column_stack([np.ones(len(x)), x])
    try:
        beta = np.linalg.lstsq(X, dy, rcond=None)[0]
    except np.linalg.LinAlgError:
        return float("nan")
    b = float(beta[1])
    phi = 1.0 + b
    if b >= 0 or phi <= 0 or phi >= 1:
        return float("inf")
    return float(-np.log(2) / np.log(phi))


def compute_stats(values: pd.Series, window: int = 252, min_observations: int = 252,
                  vol_window: int = 20, adf_alpha: float = 0.05,
                  hurst_threshold: float = 0.50, vol_extreme: float = 0.80) -> Dict[str, object]:
    """对价差序列计算截至最后一个观测的完整统计画像。"""
    s = pd.Series(values).dropna().astype(float)
    if s.empty:
        return {"regime": "no_data", "n_obs": 0, "window": window}
    win = s.iloc[-window:] if len(s) > window else s
    n = len(win)
    if n < min_observations:
        return {
            "window": window, "n_obs": n, "mean": float(win.mean()),
            "std": float(win.std(ddof=1)) if n > 1 else float("nan"),
            "z": float("nan"), "pct": float("nan"), "adf_p": float("nan"),
            "hurst": float("nan"), "half_life": float("nan"), "vol_pct": float("nan"),
            "stationary": False, "regime": "insufficient_history",
        }
    mean = float(win.mean())
    std = float(win.std(ddof=1))
    latest = float(win.iloc[-1])
    z = float((latest - mean) / std) if std > 0 else float("nan")
    pct = percentile_rank(win, latest)
    adf_p = adf_pvalue(win)
    hurst = hurst_exponent(win)
    hl = half_life(win)

    changes = win.diff().dropna()
    vol_series = changes.rolling(vol_window, min_periods=max(5, vol_window // 2)).std(ddof=1).dropna()
    vol_pct = float("nan")
    if len(vol_series) >= 30:
        vol_pct = percentile_rank(vol_series, float(vol_series.iloc[-1]))

    stationary = bool(np.isfinite(adf_p) and adf_p < adf_alpha)
    if np.isfinite(hurst) and hurst >= hurst_threshold:
        regime = "trend"
    elif np.isfinite(vol_pct) and vol_pct > vol_extreme:
        regime = "extreme_vol"
    elif stationary and (not np.isfinite(hurst) or hurst < hurst_threshold):
        regime = "mean_reversion"
    else:
        regime = "weak_mean_reversion"

    return {
        "window": window, "n_obs": n, "mean": mean, "std": std, "z": z, "pct": pct,
        "adf_p": adf_p, "hurst": hurst, "half_life": hl, "vol_pct": vol_pct,
        "stationary": stationary, "regime": regime,
    }