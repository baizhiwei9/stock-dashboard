# -*- coding: utf-8 -*-
"""AC1-VMD 趋势信号
regime=good（AC1_20>0.05 且 20日实现波动率<70分位）
VMD 低频趋势（K=4, alpha=2000，取低序 2 模态合成）slope>0 且 close>MA40 且 MA40 斜率>0 -> 多
VMD slope<0（连续2日）或 regime=bad -> 空
其余 -> 震荡
"""
import os
import sys

import numpy as np
import pandas as pd

_LIBS = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/pylibs"
if _LIBS not in sys.path:
    sys.path.insert(0, _LIBS)
from vmdpy import VMD  # noqa: E402

from config import (AC1_WINDOW, AC1_GOOD_MIN, VMD_WINDOW, VMD_K, VMD_ALPHA,  # noqa: E402
                    VMD_N_LOW, VMD_SLOPE_N)

METHOD_NAME = "AC1-VMD"


def _ac1(r):
    r = r.dropna().values
    if len(r) < AC1_WINDOW:
        return np.nan
    r = r[-AC1_WINDOW:]
    r1, r2 = r[:-1], r[1:]
    s1, s2 = r1.std(ddof=1), r2.std(ddof=1)
    if s1 == 0 or s2 == 0 or np.isnan(s1) or np.isnan(s2):
        return 0.0
    return float(np.mean((r1 - r1.mean()) * (r2 - r2.mean())) / (s1 * s2))


def _vmd_trend_slope(prices):
    """对最近窗口价格做 VMD，返回低频合成趋势最后两点斜率及符号"""
    sig = np.asarray(prices[-VMD_WINDOW:], dtype=float)
    if len(sig) < VMD_K + 2:
        return 0.0, 0.0
    try:
        u, _, _ = VMD(sig, alpha=VMD_ALPHA, tau=0.0, K=VMD_K, DC=0, init=1, tol=1e-7)
    except Exception:  # noqa: BLE001
        return 0.0, 0.0
    u = np.asarray(u)
    n_use = min(VMD_N_LOW, u.shape[0])
    trend = np.sum(u[:n_use], axis=0)
    y = trend[-VMD_SLOPE_N:]
    if len(y) < 2:
        return 0.0, 0.0
    k = (y[-1] - y[0]) / max(len(y) - 1, 1)
    return k, k


def signal(df):
    c = df["close"]
    if len(c) < VMD_WINDOW + 5:
        return "震荡", "数据不足"
    ac1 = _ac1(c.pct_change())
    vol20 = c.pct_change().rolling(20).std()
    v_now = vol20.iloc[-1]
    v_th = vol20.quantile(0.70)
    vol_ok = bool(np.isfinite(v_now) and np.isfinite(v_th) and v_now < v_th)
    reg_good = bool(np.isfinite(ac1) and ac1 > AC1_GOOD_MIN and vol_ok)

    k, _ = _vmd_trend_slope(c)
    ma40 = c.rolling(40).mean()
    p = c.iloc[-1]
    ma40_now = ma40.iloc[-1]
    ma40_slope = ma40.iloc[-1] - ma40.iloc[-6] if len(ma40) >= 6 and not pd.isna(ma40.iloc[-6]) else 0

    if reg_good and k > 0 and p > ma40_now and ma40_slope > 0:
        return "多", f"regime=good, VMD趋势上行(k={k:.5f}), 收>MA40"
    if (not reg_good) or k < 0:
        reason = "AC1/波动率regime=bad" if not reg_good else f"VMD趋势下行(k={k:.5f})"
        return "空", reason
    return "震荡", f"ac1={ac1:.3f}, k={k:.5f}"