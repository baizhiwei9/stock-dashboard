# -*- coding: utf-8 -*-
"""均线双通路（MA40/MA160 + AC1自相关风险）信号
多头: close>MA40 且 close>MA160 且 MA40、MA160 上行
空头: close<MA40 且 close<MA160 且 双线下行 且 AC1风险(ac1<0)
其余: 震荡
（TDA 拓扑风险在本环境无 ripser，以 AC1 自相关作为风险确认）
"""
import numpy as np
import pandas as pd

from config import MADC_MA_SHORT, MADC_MA_LONG, AC1_WINDOW

METHOD_NAME = "均线双通路"


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


def signal(df):
    c = df["close"]
    ma_s = c.rolling(MADC_MA_SHORT).mean()
    ma_l = c.rolling(MADC_MA_LONG).mean()
    ms, ml = ma_s.iloc[-1], ma_l.iloc[-1]
    if pd.isna(ms) or pd.isna(ml):
        return "震荡", "MA未就绪"
    msu = ms > ma_s.iloc[-2] if len(ma_s) >= 2 and not pd.isna(ma_s.iloc[-2]) else False
    mlu = ml > ma_l.iloc[-2] if len(ma_l) >= 2 and not pd.isna(ma_l.iloc[-2]) else False
    p = df["close"].iloc[-1]
    ac1 = _ac1(df["close"].pct_change())
    if p > ms and p > ml and msu and mlu:
        return "多", f"收>{MA40_S}且>{MA160_S}，双线上行"
    if p < ms and p < ml and (not msu) and (not mlu) and ac1 < 0:
        return "空", f"收<MA40且<MA160，双线下行，AC1={ac1:.3f}风险确认"
    return "震荡", f"AC1={ac1:.3f}，MA40{ '上行' if msu else '下行' } MA160{ '上行' if mlu else '下行' }"


MA40_S, MA160_S = "MA40", "MA160"