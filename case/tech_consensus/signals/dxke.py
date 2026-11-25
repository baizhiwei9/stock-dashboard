# -*- coding: utf-8 -*-
"""动力学 MACD（DX-KE 动能注入版）信号 · 2026-09-26 定稿口径
指标口径（与 dif_ku_balance_test / kinetic 回测框架对齐）：
  快慢线:    DIF = EMA(FAST=10) - EMA(SLOW=20)   （原 12/26 已调整为 10/20）
  速度/动量: v  = DIF / ATR14
  加速度:    a  = MACD柱(HT=DIF-EMA9(DIF)) / ATR14
  F 值:      F  = v² / a （动能-势能比，F 过大=动能过激，用于仓位控制判断）
  动能衰减:  K  = ½·vK², U = ½·z40², z40=(close−MA40)/ATR14；
             K < U（动能<势能）→ 动能衰减，配合减仓/清仓
信号（view 口径保持日报原有逻辑）：
  多头: close>EMA50 且 v>0 且 a>0（加速上涨象限）
  空头: close<EMA50 且 v<0 且 a<0（加速下跌象限）
  否则: 震荡
detail 中带出 F 值与 K/U 仓位建议，供日报文字说明使用。
"""
import numpy as np
import pandas as pd

from config import DX_EMA_FAST, DX_EMA_SLOW, DX_SIG, DX_TREND

METHOD_NAME = "动力学MACD"


def ema(s, span):
    return s.ewm(span=span, adjust=False).mean()


def atr(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(span=n, adjust=False).mean()


def compute(df):
    c = df["close"]
    e_fast = ema(c, DX_EMA_FAST)
    e_slow = ema(c, DX_EMA_SLOW)
    dif = e_fast - e_slow                       # DIF(10/20)
    dea = ema(dif, DX_SIG)
    hist = dif - dea                             # MACD 柱
    s50 = ema(c, DX_TREND)
    a14 = atr(df, 14)
    v = dif / a14                                # 速度/动量
    a = hist / a14                               # 加速度
    # F 值：动能-势能比 v²/a（a→0 时为 inf/nan，保留供分位判断）
    with np.errstate(divide="ignore", invalid="ignore"):
        F = pd.Series(np.where(np.abs(a) > 1e-12, v ** 2 / a, np.nan), index=df.index)
    # 动能 K / 势能 U（MA40 偏离口径，与回测框架 dif_ku_balance_test 对齐）
    ma40 = c.rolling(40).mean()
    z40 = (c - ma40) / a14.replace(0, np.nan)
    vK = z40.diff()
    K = 0.5 * (vK ** 2)
    U = 0.5 * (z40 ** 2)
    return pd.DataFrame({"close": c, "s50": s50, "v": v, "a": a,
                         "F": F, "K": K, "U": U})


def signal(df):
    """返回 (view, detail)。view: 多/空/震荡"""
    d = compute(df)
    last = d.iloc[-1]
    v, a = last["v"], last["a"]
    F = last["F"]
    K, U = last["K"], last["U"]
    if np.isnan(v) or np.isnan(a) or np.isnan(last["s50"]):
        return "震荡", "指标未就绪"
    kd = ""
    if np.isfinite(K) and np.isfinite(U) and K < U:
        kd = "；动能衰减(K<U)建议减仓/清仓"
    fstr = f", F={F:.2f}" if np.isfinite(F) else ""
    if last["close"] > last["s50"] and v > 0 and a > 0:
        return "多", f"收>EMA50且DIF加速(v={v:.3f}, a={a:.3f}{fstr}){kd}"
    if last["close"] < last["s50"] and v < 0 and a < 0:
        return "空", f"收<EMA50且DIF加速下行(v={v:.3f}, a={a:.3f}{fstr}){kd}"
    return "震荡", f"v={v:.3f}, a={a:.3f}{fstr}{kd}"