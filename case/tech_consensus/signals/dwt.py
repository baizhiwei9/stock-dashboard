# -*- coding: utf-8 -*-
"""小波 DWT（sym4, 3层）低频趋势信号
对收盘价做 sym4 3 层小波分解，重构低频趋势 c_long；
close>低频趋势且低频斜率>0 -> 多；close<低频趋势且斜率<0 -> 空；否则震荡
"""
import os
import sys

import numpy as np
import pandas as pd

_LIBS = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/pylibs"
if _LIBS not in sys.path:
    sys.path.insert(0, _LIBS)
import pywt  # noqa: E402

METHOD_NAME = "小波DWT"

WAVELET = "sym4"
LEVEL = 3
SLOPE_N = 5


def _low_trend(prices):
    x = np.asarray(prices, dtype=float)
    n = len(x)
    # pad 至 2^level 倍数
    pad = (2 ** LEVEL) - (n % (2 ** LEVEL))
    p = np.pad(x, (0, pad), mode="edge")
    coeffs = pywt.wavedec(p, WAVELET, level=LEVEL)
    rec = pywt.waverec([coeffs[0]] + [np.zeros_like(c) for c in coeffs[1:]], WAVELET)[:n]
    return rec


def signal(df):
    c = df["close"].dropna()
    if len(c) < 60:
        return "震荡", "数据不足"
    trend = _low_trend(c.values)
    p = c.iloc[-1]
    t_last = trend[-1]
    slope = (trend[-1] - trend[-SLOPE_N - 1]) / SLOPE_N if len(trend) > SLOPE_N else 0.0
    if p > t_last and slope > 0:
        return "多", f"收>{低频趋势} 且低频斜率>0（{slope:.4f}）"
    if p < t_last and slope < 0:
        return "空", f"收<低频趋势 且低频斜率<0（{slope:.4f}）"
    return "震荡", f"收vs趋势偏差 {(p/t_last-1)*100:+.2f}%，斜率 {slope:.4f}"


低频趋势 = "低频趋势"