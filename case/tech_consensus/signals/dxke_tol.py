# -*- coding: utf-8 -*-
"""动力学MACD 加速度容忍窗口变体 — 基于原始 dxke 三态信号做后处理平滑
原版 dxke 信号: 多=1/空=-1/震荡=0 (a 回正/转负当天立即退出)
变体: 加速度回正/转负后需连续 N 天才确认退出, 增加抗噪能力

实现方式: 在原始 dxke_daily 信号上增加"退出冷却期"
- 原始信号给出多/空 → 立即接受(入场不延迟)
- 当前持有多头, 原始信号变震荡/空 → 开始冷却计数, 连续 n_tol 天不在多状态才正式退出
- 当前持有空头, 原始信号变震荡/多 → 开始冷却计数, 连续 n_tol 天不在空状态才正式退出
"""
import numpy as np
import pandas as pd

VIEW_MULTI = 1
VIEW_SHORT = -1
VIEW_FLAT = 0


def daily_views(base_signal, n_tol=1):
    """
    base_signal: pd.Series, 原始 dxke_daily 输出 (1/-1/0)
    n_tol: int, 退出冷却天数 (1=原版, 2=连续2天确认, 3=连续3天确认)
    """
    sig = base_signal.fillna(0).astype(int).values
    views = np.full(len(sig), VIEW_FLAT)
    cur_state = VIEW_FLAT
    exit_counter = 0
    
    for i in range(len(sig)):
        raw = int(sig[i])
        
        # 入场: 原始信号给出多/空, 立即接受
        if raw == VIEW_MULTI and cur_state != VIEW_MULTI:
            cur_state = VIEW_MULTI
            exit_counter = 0
            views[i] = VIEW_MULTI
            continue
        if raw == VIEW_SHORT and cur_state != VIEW_SHORT:
            cur_state = VIEW_SHORT
            exit_counter = 0
            views[i] = VIEW_SHORT
            continue
        
        # 出场: 原始信号不再支持当前方向, 开始冷却计数
        if cur_state == VIEW_MULTI:
            if raw != VIEW_MULTI:  # 变震荡或空
                exit_counter += 1
                if exit_counter >= n_tol:
                    cur_state = VIEW_FLAT
                    views[i] = VIEW_FLAT
                    exit_counter = 0
                else:
                    views[i] = VIEW_MULTI
            else:
                exit_counter = 0
                views[i] = VIEW_MULTI
        elif cur_state == VIEW_SHORT:
            if raw != VIEW_SHORT:  # 变震荡或多
                exit_counter += 1
                if exit_counter >= n_tol:
                    cur_state = VIEW_FLAT
                    views[i] = VIEW_FLAT
                    exit_counter = 0
                else:
                    views[i] = VIEW_SHORT
            else:
                exit_counter = 0
                views[i] = VIEW_SHORT
        else:
            views[i] = VIEW_FLAT
            exit_counter = 0
    
    return pd.Series(views, index=base_signal.index)
