#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STMOM 张量面板重训：10 标的面板扩展为 13 标的
新增：科创50(sh588000)、中证1000(sh512100)、恒生科技(sh513180)
训练窗口：2021-06-01（恒生科技上市后全池对齐）~ 2024-12-31，2025-01-01 起样本外
流程：拉13标的面板 -> build_tensor/make_samples -> 随机搜索超参(24) -> 5种子平均
输出：signals/stmom_weights/model_weights.npz（覆盖为扩展版 13 标的面板权重）
"""
import os
import sys
import time
import json

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "signals"))

import data_fetch  # noqa: E402
import stmom_lib as SL  # noqa: E402
from config import STOM_POOL, STOM_NAMES  # noqa: E402
# 2023 回测版：剔除恒生科技(sh513180)（2021-05 上市，252日滞后拖累训练样本），
# 不影响 4 个有期货的宽基（沪深300/中证500/上证50/中证1000）的信号。
STOM_POOL = [c for c in STOM_POOL if c != "sh513180"]
STOM_NAMES = {k: v for k, v in STOM_NAMES.items() if k != "sh513180"}
STW_WEIGHTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "signals", "stmom_weights", "model_weights_2022.npz")

TRAIN_START = "2021-01-04"
TRAIN_END = "2022-12-31"
# 2023 起为纯样本外（回测）

OUT_HP = os.path.join(os.path.dirname(STW_WEIGHTS), "hp_2022.json")


def main():
    t0 = time.time()
    print("1) 拉取 13 标的面板数据 ...")
    _panel_csv = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "backtest_panel_2019.csv")
    if os.path.exists(_panel_csv):
        panel = pd.read_csv(_panel_csv, index_col=0, parse_dates=True).sort_index()
        panel = panel[STOM_POOL]
        # QDII（纳指/标普）与 A 股交易日历不同步：停牌缺口前向填充（停牌沿用前收）
        panel = panel.ffill()
        print(f"  使用回测面板缓存 {_panel_csv} (ffill QDII缺口)")
    else:
        data = data_fetch.load_all(target="stom", use_cache=True)
        miss = [c for c in STOM_POOL if c not in data]
        if miss:
            print(f"  [X] 缺标的: {miss}")
            return 1
        panel = data_fetch.aligned_panel(data, STOM_POOL)
    print(f"  面板全区间 {panel.shape[0]} 日 x {panel.shape[1]} 标的（{panel.index[0].date()} ~ {panel.index[-1].date()}）")
    print(f"  训练截止 {TRAIN_END}，此后为纯样本外")

    print("2) 构建时空张量样本 ...")
    df = panel.copy()
    returns = df.pct_change()
    Xdf, Sigmadf, names = SL.build_tensor(df)
    X, y, V, dates = SL.make_samples(Xdf, Sigmadf, returns, names)
    print(f"  样本数 {X.shape[0]}，特征维 D_in={X.shape[1]}（N={len(names)} L={SL.L_WINDOW} F={5 + len(SL.MACD_PAIRS)}）")

    tm = (dates <= pd.Timestamp(TRAIN_END))
    if tm.sum() < 200:
        print(f"  [X] 训练样本不足: {tm.sum()}")
        return 1
    Xtr_all, ytr_all, Vtr_all = X[tm], y[tm], V[tm]
    Xeval, yeval, Veval = X[~tm], y[~tm], V[~tm]
    split = int(len(Xtr_all) * 0.9)
    print(f"  训练窗口 {Xtr_all.shape[0]} 样本，90/10 切分：{split}/{len(Xtr_all)-split}；样本外 {Xeval.shape[0]}")

    print("3) 随机搜索超参（24 trials） ...")
    results = SL.random_search(Xtr_all[:split], ytr_all[:split], Vtr_all[:split],
                               Xtr_all[split:], ytr_all[split:], Vtr_all[split:],
                               names, n_trials=24)
    best = results[0]
    hp = best["hparams"]
    print(f"  最优超参: {hp}")
    print(f"  验证Sharpe: {best['val_sharpe']:.3f}")

    print("4) 5 种子重训并平均 ...")
    sigs = []
    for i in range(5):
        seed = 1000 + i
        W, b = SL.init_slp(X.shape[1], len(names), seed)
        (Wb, bb), _ = SL.train_model(Xtr_all[:split], ytr_all[:split], Vtr_all[:split],
                                     Xtr_all[split:], ytr_all[split:], Vtr_all[split:],
                                     W, b, hp, np.random.default_rng(seed))
        _, s = SL.forward(Xeval, Wb, bb)
        sigs.append(s)
    s_all = np.mean(sigs, axis=0)
    print(f"  样本外中位信号: {np.median(s_all):+.3f}  宽基平均: "
          + "  ".join(f"{STOM_NAMES.get(c, c)}={np.median(s_all[:, i]):+.3f}"
                      for i, c in enumerate(names)))

    print("5) 保存权重 ...")
    ws = []
    for i in range(5):
        seed = 1000 + i
        W, b = SL.init_slp(X.shape[1], len(names), seed)
        (Wb, bb), _ = SL.train_model(Xtr_all[:split], ytr_all[:split], Vtr_all[:split],
                                     Xtr_all[split:], ytr_all[split:], Vtr_all[split:],
                                     W, b, hp, np.random.default_rng(seed))
        ws.append((Wb, bb))
    os.makedirs(os.path.dirname(STW_WEIGHTS), exist_ok=True)
    np.savez(STW_WEIGHTS, **{f"W{i}": ws[i][0] for i in range(5)},
             **{f"b{i}": ws[i][1] for i in range(5)})
    meta = {"pool": names, "pool_names": STOM_NAMES,
            "trained": f"{TRAIN_START}~{TRAIN_END}",
            "hparams": hp, "N": len(names), "L": SL.L_WINDOW,
            "F": 5 + len(SL.MACD_PAIRS), "D_in": X.shape[1]}
    with open(OUT_HP, "w") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"  已保存: {STW_WEIGHTS}（13 标的面板，{X.shape[1]} 维输入）")
    print(f"  元数据: {OUT_HP}")
    print(f"耗时 {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())