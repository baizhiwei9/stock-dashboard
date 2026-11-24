"""滚动因果 PCA（无前视），提取利率水平/斜率/曲率三因子。"""
from __future__ import annotations

from typing import Tuple

import numpy as np
import pandas as pd

WIN = 250


def rolling_pca(yw: pd.DataFrame, terms, win: int = WIN
                ) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """t 期因子用截至 t-1 的窗口载荷投影：返回 (F, L, MU)。"""
    y = yw[list(terms)].dropna()
    X = y.values.astype(float)
    dates = y.index
    n, p = X.shape
    F = np.full((n, 3), np.nan)
    L = np.full((n, p, 3), np.nan)
    MU = np.full((n, p), np.nan)
    for t in range(win, n + 1):
        w = X[t - win:t]
        mu = w.mean(axis=0)
        cov = np.cov((w - mu).T)
        evals, evecs = np.linalg.eigh(cov)
        Lw = evecs[:, np.argsort(evals)[::-1][:3]]
        if Lw[0, 0] < 0:  # 统一符号：长端为正
            Lw[:, 0] = -Lw[:, 0]
        for j in (1, 2):
            if Lw[-1, j] < 0:
                Lw[:, j] = -Lw[:, j]
        MU[t - 1] = mu
        L[t - 1] = Lw
        F[t - 1] = (X[t - 1] - mu) @ Lw
    df_f = pd.DataFrame(F, index=dates, columns=["PC1", "PC2", "PC3"])
    lcols = [f"{t}_{pc}" for t in terms for pc in ("PC1", "PC2", "PC3")]
    df_l = pd.DataFrame(L.reshape(n, p * 3), index=dates, columns=lcols)
    df_m = pd.DataFrame(MU, index=dates, columns=list(terms))
    return df_f, df_l, df_m