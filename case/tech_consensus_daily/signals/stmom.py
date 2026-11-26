# -*- coding: utf-8 -*-
"""STMOM 张量（SLP 截面动量）信号
使用已在 A股10标的面板（2017-08~2021-12训练）上训练好的5种子权重，
对 STOM_POOL 面板做前向推理得到每标的信号 s；
宽基池中在模型池内的标的：s>+0.1 多 / s<-0.1 空 / 否则震荡；
不在模型池内的标的（模型池外）返回 N/A，不参与投票。
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stmom_lib as SL  # noqa: E402

from config import STOM_POOL, STW_WEIGHTS  # noqa: E402

METHOD_NAME = "STMOM张量"

POS_SIG = 0.10
NEG_SIG = -0.10


def compute_signal_panel(panel):
    """panel: DataFrame[date, columns=STOM_POOL内代码]。返回 {code: sig}"""
    df = panel[[c for c in STOM_POOL if c in panel.columns]].copy()
    if df.shape[1] != len(STOM_POOL):
        raise ValueError(f"STMOM 面板缺失标的: {set(STOM_POOL) - set(df.columns)}")
    returns = df.pct_change()
    Xdf, Sigmadf, names = SL.build_tensor(df)
    Xmat = Xdf.values
    L = SL.L_WINDOW
    D_feat = 5 + len(SL.MACD_PAIRS)
    N = len(names)
    # 找最后一个全有限的特征日
    latest_idx = None
    for i in range(len(Xmat) - 1, L - 2, -1):
        block = Xmat[i - L + 1:i + 1]
        if np.isfinite(block).all():
            latest_idx = i
            break
    if latest_idx is None:
        raise ValueError("无完整特征日")
    block = Xmat[latest_idx - L + 1:latest_idx + 1].reshape(L, N, D_feat)
    x_new = block.transpose(1, 0, 2).reshape(1, -1)
    w = np.load(STW_WEIGHTS)
    ws = [w[k] for k in ["W0", "W1", "W2", "W3", "W4"]]
    bs = [w[k] for k in ["b0", "b1", "b2", "b3", "b4"]]
    sig = np.mean([np.tanh(x_new @ W.T + b)[0] for W, b in zip(ws, bs)], axis=0)
    return {names[i]: float(sig[i]) for i in range(len(names))}


def signal_for(code, sig_map, detail_prefix=""):
    """给定单标的 code（宽基池代码）与其模型信号 → (view, detail)"""
    if code not in sig_map:
        return "N/A", "模型池外，无信号"
    s = sig_map[code]
    if s > POS_SIG:
        return "多", f"{detail_prefix}sig={s:+.3f}（>+0.10）"
    if s < NEG_SIG:
        return "空", f"{detail_prefix}sig={s:+.3f}（<-0.10）"
    return "震荡", f"{detail_prefix}sig={s:+.3f}"


def views_for_pool(panel, target_codes):
    """批量返回 {code: (view, detail)}"""
    sig_map = compute_signal_panel(panel)
    out = {}
    for code in target_codes:
        out[code] = signal_for(code, sig_map)
    return out