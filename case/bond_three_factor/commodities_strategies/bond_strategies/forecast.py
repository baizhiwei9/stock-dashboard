"""XGBoost 三因子下周预测与收益率还原。"""
from __future__ import annotations

from typing import Dict, Tuple

import numpy as np
import pandas as pd

from .dataset import MAC_COLS, TERMS

HIT = {"2年": "72.0%", "5年": "82.8%", "10年": "73.1%", "30年": "80.6%"}
BP_THRESHOLD = 2.0


def predict_next(F: pd.DataFrame, L: pd.DataFrame, MU: pd.DataFrame, mx: pd.DataFrame,
                 mac_cols=None, seed=42) -> Tuple[np.ndarray, np.ndarray]:
    """用滞后三因子 + 宏观周变化训练 XGB，预测下周三因子并还原各期限收益率。"""
    from xgboost import XGBRegressor

    # 只保留面板里真实存在的宏观特征：DataHub 没有的口径（如黄金）直接不参与训练，
    # 而不是补零——补零会让模型把「缺数据」学成「数值为零」的假信号。
    cols = [c for c in (mac_cols or MAC_COLS) if c in mx.columns]
    mac = mx[cols].reindex(F.index).diff().values
    FEA = np.column_stack([F.values, mac])
    Fv = F.values
    p = len(Fv)
    start = np.where(np.isfinite(Fv).all(axis=1))[0][0]
    Xtr = FEA[start:p - 1]
    ytr = Fv[start + 1:p]
    mask = np.isfinite(Fv[start:p - 1]).all(axis=1)
    Xtr, ytr = Xtr[mask], ytr[mask]
    pred_f = np.full(3, np.nan)
    for j in range(3):
        xg = XGBRegressor(n_estimators=200, max_depth=4, learning_rate=0.05,
                          subsample=0.8, colsample_bytree=0.8, n_jobs=-1, verbosity=0,
                          random_state=seed)
        xg.fit(Xtr, ytr[:, j])
        pred_f[j] = xg.predict(FEA[p - 1:p])[0]
    y_next = MU.values[-1] + pred_f @ L.values[-1].reshape(len(TERMS), 3).T
    return pred_f, y_next


def actions(latest: pd.Series, pred: pd.Series, thr: float = BP_THRESHOLD) -> Dict[str, Dict]:
    """预测变化 → 买入/卖出/观望（bp 阈值 ±2.0）。"""
    out = {}
    for t in pred.index:
        d = float((pred[t] - latest[t]) * 100)
        act = "买入" if d <= -thr else ("卖出" if d >= thr else "观望")
        out[t] = {"bp": round(d, 1), "action": act,
                  "hint": "做多/加仓" if act == "买入" else ("做空/减仓" if act == "卖出" else "持仓不动")}
    return out