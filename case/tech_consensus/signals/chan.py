# -*- coding: utf-8 -*-
"""缠论分型（B1/B2/B3 · S1/S2/S3）信号 —— 日线级别判定
按「行号升序取最近一个信号」+「买点/卖点有效性校验」：
  最近信号为买点 且 现价未跌破买点价 -> 多（买点有效）
  最近信号为卖点 且 现价未上破卖点价 -> 空（卖点有效）
  买点被跌破 / 卖点被上破 / 信号距当前过远 -> 震荡（信号失效）
  无任何信号 -> 震荡
"""
import sys
import os

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from chanlib import chan_fractal as cf  # noqa: E402

from config import CHAN_VALID_BARS  # noqa: E402

METHOD_NAME = "缠论分型"


def signal(df):
    """df: 标准日线 DataFrame；返回 (view, detail)"""
    d = df.copy()
    d.rename(columns={"date": "time"}, inplace=True)
    try:
        res = cf.analyze(d, level="day", backtest=False)
    except Exception as e:  # noqa: BLE001
        return "震荡", f"分析失败: {type(e).__name__}"
    opps = res.get("交易机会") or []
    if not opps:
        return "震荡", "无买卖点"
    # 去重（同一 行号+方向+类型 只留一个）并按行号升序
    seen = set()
    uniq = []
    for o in opps:
        k = (o["行号"], o["方向"], o["类型"])
        if k not in seen:
            seen.add(k)
            uniq.append(o)
    uniq.sort(key=lambda x: int(x["行号"]))
    last_sig = uniq[-1]
    gap = max(0, len(d) - 1 - int(last_sig["行号"]))
    if gap > CHAN_VALID_BARS:
        return "震荡", f"最近{last_sig['方向']}点({last_sig['类型']})已失效{gap}根"
    price_now = float(d["close"].iloc[-1])
    sig_px = float(last_sig["价格"])
    if last_sig["方向"] == "买入":
        if price_now < sig_px:
            return "震荡", f"{last_sig['类型']}买点@{sig_px:.4f}已跌破(现价{price_now:.4f})"
        return "多", f"{last_sig['类型']}买点@{sig_px:.4f}({last_sig['时间']})"
    if price_now > sig_px:
        return "震荡", f"{last_sig['类型']}卖点@{sig_px:.4f}已上破(现价{price_now:.4f})"
    return "空", f"{last_sig['类型']}卖点@{sig_px:.4f}({last_sig['时间']})"