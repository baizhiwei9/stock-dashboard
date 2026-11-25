"""Slope（10Y-2Y，代码 CGB_SPREAD_10Y2Y）机会判定。

口径：250 日滚动分位 + DR007 方向过滤 + 残差波动分位过滤；
与曲线交易回测推荐方案 X_tp20.70_h20 一致（阈值 0.08/0.92、残差 0.80）。
"""
from __future__ import annotations

from typing import Tuple

import numpy as np
import pandas as pd

from .dataset import SLOPE_TERMS

PCA_WIN = 60
PCT_WIN = 250
DR007_WIN = 20
LOW_Q = 0.08
HIGH_Q = 0.92
RESID_LIM = 0.80
LOT = {"steep": "TS×3 多 + T×1 空（做陡）", "flat": "TS×3 空 + T×1 多（做平）"}


def compute(y: pd.DataFrame, d: pd.Series, terms=None, pca_win: int = PCA_WIN,
            pct_win: int = PCT_WIN, dr_win: int = DR007_WIN) -> Tuple[pd.DataFrame, pd.Series]:
    """重算最新交易日的 Slope 指标：返回 (历史明细 DataFrame, 最新行)。"""
    cols = list(terms or SLOPE_TERMS)
    yy = y[cols].ffill().dropna()
    dy = yy.diff().dropna()
    X = dy.values.astype(float)
    n = len(dy)
    resid = np.full(n, np.nan)
    for t in range(pca_win, n):
        w = X[t - pca_win:t]
        mu = w.mean(axis=0)
        cov = np.cov((w - mu).T)
        evals, evecs = np.linalg.eigh(cov)
        Lw = evecs[:, np.argsort(evals)[::-1][:3]]
        xc = X[t] - mu
        F = xc @ Lw
        resid[t] = np.std(xc - F @ Lw.T)
    out = pd.DataFrame(index=dy.index)
    out["SPREAD210"] = (yy["10年"] - yy["2年"]) * 100
    out["RESID_STD"] = resid
    out["SPREAD_PCT"] = np.nan
    out["RESID_PCT"] = np.nan
    for t in range(pct_win, len(out)):
        s = out["SPREAD210"].iloc[t - pct_win + 1:t + 1]
        out.iloc[t, out.columns.get_loc("SPREAD_PCT")] = (s <= s.iloc[-1]).mean()
        r = out["RESID_STD"].iloc[t - pct_win + 1:t + 1]
        out.iloc[t, out.columns.get_loc("RESID_PCT")] = (r <= r.iloc[-1]).mean()
    dr_dir = np.sign(d.rolling(5).mean() - d.rolling(dr_win).mean())
    out["DR007_DIR"] = dr_dir.reindex(out.index).ffill()
    row = out.dropna(subset=["SPREAD_PCT"]).iloc[-1]
    return out, row


def judge(row: pd.Series) -> Tuple[str, str]:
    """返回 (signal, note)；signal ∈ {steep, flat, None}。"""
    sp, dr, res = float(row["SPREAD_PCT"]), float(row["DR007_DIR"]), float(row["RESID_PCT"])
    if sp < LOW_Q and dr < 0 and res < RESID_LIM:
        return "steep", (f"做陡：SPREAD_PCT={sp:.3f}<{LOW_Q} 且 DR007 下行 且残差{res:.3f}<{RESID_LIM}，"
                         "押曲线陡峭化")
    if sp > HIGH_Q and dr > 0 and res < RESID_LIM:
        return "flat", (f"做平：SPREAD_PCT={sp:.3f}>{HIGH_Q} 且 DR007 上行 且残差{res:.3f}<{RESID_LIM}，"
                        "押曲线平坦化")
    return None, (f"观望：利差分位未达极值或资金面/残差过滤未通过，不构成 Slope 机会"
                  f"（SPREAD_PCT={sp:.3f}, DR007_DIR={dr:.0f}, RESID_PCT={res:.3f}）")


def near_zone(row: pd.Series) -> str:
    sp = float(row["SPREAD_PCT"])
    msgs = []
    if LOW_Q <= sp < 0.15:
        msgs.append(f"SPREAD_PCT={sp:.3f} 已进入 0.08~0.15 低分位带，接近做陡候选区，重点关注 DR007 是否转松")
    if 0.85 < sp <= HIGH_Q:
        msgs.append(f"SPREAD_PCT={sp:.3f} 已进入 0.85~0.92 高分位带，接近做平候选区，重点关注 DR007 是否转紧")
    return "；".join(msgs)


def label(signal: str) -> str:
    return "做陡" if signal == "steep" else ("做平" if signal == "flat" else "Slope 机会")