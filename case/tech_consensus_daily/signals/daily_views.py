# -*- coding: utf-8 -*-
"""六方法逐日观点序列适配层（供 2023+ 历史回测使用）
统一输出: {code: pd.Series(index=date, values=view)}
view: 多=1 / 空=-1 / 震荡=0 / N/A=nan
口径修正说明:
  1. AC1-VMD 原 signal() 的 vol20.quantile(0.70) 为全样本分位（前视），
     此处改为 expansion(截至当日) 分位，回测不偷看未来。
  2. 缠论: 逐日重跑 analyze() 成本高（labamba 全量 0.14s/次，可接受），
     直接用 chan.signal() 语义 -> 对每个历史日取截至该日最近买卖点并校验。
DNS: 日线级别判定，与日报口径保持一致。
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dxke  # noqa: E402
import madc  # noqa: E402
import dwt  # noqa: E402
import ac1vmd  # noqa: E402
from chanlib import chan_fractal as cf  # noqa: E402
import stmom_lib as SL  # noqa: E402

from config import (CHAN_VALID_BARS, AC1_WINDOW, AC1_GOOD_MIN, VMD_WINDOW,  # noqa: E402
                    VMD_K, VMD_ALPHA, VMD_N_LOW, VMD_SLOPE_N,
                    MADC_MA_SHORT, MADC_MA_LONG)

VIEW_MULTI, VIEW_SHORT, VIEW_FLAT = 1, -1, 0


def _ac1_series(r):
    """输入 pd.Series（截至当日），返回窗口 AC1"""
    return madc._ac1(r)


def dxke_daily(df):
    """动力学MACD 逐日观点。compute 本身全序列向量化，逐日判定即可。"""
    d = dxke.compute(df)
    views = np.full(len(d), VIEW_FLAT)
    v, a, s50, c = d["v"].values, d["a"].values, d["s50"].values, d["close"].values
    for i in range(len(d)):
        if np.isnan(v[i]) or np.isnan(a[i]) or np.isnan(s50[i]):
            views[i] = VIEW_FLAT
        elif c[i] > s50[i] and v[i] > 0 and a[i] > 0:
            views[i] = VIEW_MULTI
        elif c[i] < s50[i] and v[i] < 0 and a[i] < 0:
            views[i] = VIEW_SHORT
        else:
            views[i] = VIEW_FLAT
    return pd.Series(views, index=df.index)


def madc_daily(df):
    """均线双通路 逐日观点。MA 可向量化；AC1 用滚动窗口（截至当日）。"""
    c = df["close"]
    ma_s = c.rolling(MADC_MA_SHORT).mean()
    ma_l = c.rolling(MADC_MA_LONG).mean()
    r = c.pct_change()
    # 滚动 AC1：对每个历史日取前 AC1_WINDOW 个已实现收益
    ac1 = r.rolling(AC1_WINDOW).apply(lambda x: _ac1_series(pd.Series(x)), raw=False)
    p = c.values
    views = np.full(len(c), VIEW_FLAT)
    n = len(c)
    for i in range(n):
        ms, ml = ma_s.iloc[i], ma_l.iloc[i]
        if pd.isna(ms) or pd.isna(ml):
            continue
        msu = ms > ma_s.iloc[i - 1] if i >= 1 and not pd.isna(ma_s.iloc[i - 1]) else False
        mlu = ml > ma_l.iloc[i - 1] if i >= 1 and not pd.isna(ma_l.iloc[i - 1]) else False
        a1 = ac1.iloc[i]
        a1 = 0.0 if (pd.isna(a1) or np.isnan(a1)) else float(a1)
        if p[i] > ms and p[i] > ml and msu and mlu:
            views[i] = VIEW_MULTI
        elif p[i] < ms and p[i] < ml and (not msu) and (not mlu) and a1 < 0:
            views[i] = VIEW_SHORT
        else:
            views[i] = VIEW_FLAT
    return pd.Series(views, index=df.index)


def dwt_daily(df):
    """小波DWT 逐日观点。对每个历史日重算低频趋势（截至当日）。"""
    c = df["close"].dropna()
    n = len(c)
    views = np.full(n, VIEW_FLAT)
    if n < 60:
        return pd.Series(views, index=c.index)
    for i in range(59, n):
        seg = c.iloc[:i + 1]
        trend = dwt._low_trend(seg.values)
        p_now = c.iloc[i]
        t_last = trend[-1]
        slope = (trend[-1] - trend[-dwt.SLOPE_N - 1]) / dwt.SLOPE_N if len(trend) > dwt.SLOPE_N else 0.0
        if p_now > t_last and slope > 0:
            views[i] = VIEW_MULTI
        elif p_now < t_last and slope < 0:
            views[i] = VIEW_SHORT
    return pd.Series(views, index=c.index)


def _vmd_trend_slope(prices):
    """最近 VMD_WINDOW 窗口 VMD，返回低频合成趋势斜率"""
    sig = np.asarray(prices[-VMD_WINDOW:], dtype=float)
    if len(sig) < VMD_K + 2:
        return 0.0
    try:
        u, _, _ = ac1vmd.VMD(sig, alpha=VMD_ALPHA, tau=0.0, K=VMD_K, DC=0, init=1, tol=1e-7)
    except Exception:  # noqa: BLE001
        return 0.0
    u = np.asarray(u)
    n_use = min(VMD_N_LOW, u.shape[0])
    trend = np.sum(u[:n_use], axis=0)
    y = trend[-VMD_SLOPE_N:]
    if len(y) < 2:
        return 0.0
    return (y[-1] - y[0]) / max(len(y) - 1, 1)


def ac1vmd_daily(df):
    """AC1-VMD 逐日观点。vol20 分位改为截至当日的 expanding 分位（防前视）。"""
    c = df["close"]
    n = len(c)
    views = np.full(n, VIEW_FLAT)
    if n < VMD_WINDOW + 5:
        return pd.Series(views, index=df.index)
    r = c.pct_change()
    # 截至当日的实现波动率值序列
    vol20 = r.rolling(20).std()
    # expanding 分位（每一步只用当日及之前数据）
    vol_th = vol20.expanding().quantile(0.70)
    ma40 = c.rolling(40).mean()
    ac1_all = r.rolling(AC1_WINDOW).apply(lambda x: _ac1_series(pd.Series(x)), raw=False)
    for i in range(VMD_WINDOW + 4, n):
        a1 = ac1_all.iloc[i]
        a1 = float(a1) if not (pd.isna(a1) or np.isnan(a1)) else np.nan
        v_now = vol20.iloc[i]
        v_th = vol_th.iloc[i]
        vol_ok = bool(np.isfinite(v_now) and np.isfinite(v_th) and v_now < v_th)
        reg_good = bool(np.isfinite(a1) and a1 > AC1_GOOD_MIN and vol_ok)
        k = _vmd_trend_slope(c.iloc[:i + 1].values)
        p = c.iloc[i]
        ma40_now = ma40.iloc[i]
        ma40_slope = ma40.iloc[i] - ma40.iloc[i - 5] if i >= 5 and not pd.isna(ma40.iloc[i - 5]) else 0
        if (not np.isfinite(p)) or (pd.isna(ma40_now)):
            views[i] = VIEW_FLAT
        elif reg_good and k > 0 and p > ma40_now and ma40_slope > 0:
            views[i] = VIEW_MULTI
        elif (not reg_good) or k < 0:
            views[i] = VIEW_SHORT
        else:
            views[i] = VIEW_FLAT
    return pd.Series(views, index=df.index)


def chan_daily(df):
    """缠论分型 逐日观点。
    与 chan.signal() 相同语义：逐历史日重跑 analyze（截至当日数据），
    取当日最近买卖点，校验 gap 与价格有效性。
    """
    d = df.copy()
    d.rename(columns={"date": "time"}, inplace=True)
    n = len(d)
    views = np.full(n, VIEW_FLAT)
    for i in range(n):
        seg = d.iloc[:i + 1]
        try:
            res = cf.analyze(seg, level="day", backtest=False)
        except Exception:  # noqa: BLE001
            continue
        opps = res.get("交易机会") or []
        if not opps:
            continue
        seen = set()
        uniq = []
        for o in opps:
            k = (o["行号"], o["方向"], o["类型"])
            if k not in seen:
                seen.add(k)
                uniq.append(o)
        uniq.sort(key=lambda x: int(x["行号"]))
        last_sig = uniq[-1]
        gap = i - int(last_sig["行号"])
        if gap > CHAN_VALID_BARS:
            continue
        price_now = float(d["close"].iloc[i])
        sig_px = float(last_sig["价格"])
        if last_sig["方向"] == "买入":
            if price_now >= sig_px:
                views[i] = VIEW_MULTI
        else:
            if price_now <= sig_px:
                views[i] = VIEW_SHORT
    return pd.Series(views, index=df.index)


def stmom_daily(panel, weights_path, target_codes):
    """STMOM 张量 逐日观点：滚动面板逐日前推推理。
    panel: DataFrame[index=date, columns=代码]（STOM_POOL 面板，按日期升序）
    对每个历史日，用截至该日的 L 窗口特征块 forward，5 种子平均得 sig；
    对 target_codes 输出 多/空/震荡（sig>+0.10 / sig<-0.10）。
    """
    df = panel.copy()
    missing = [c for c in DF_COLS_REQ if c not in df.columns]
    if missing:
        raise ValueError(f"STMOM 面板缺失标的: {sorted(missing)}")
    df = df[DF_COLS_REQ]
    returns = df.pct_change()
    Xdf, Sigmadf, names = SL.build_tensor(df)
    Xmat = Xdf.values
    L = SL.L_WINDOW
    D_feat = 5 + len(SL.MACD_PAIRS)
    N = len(names)
    w = np.load(weights_path)
    ws = [w[k] for k in ["W0", "W1", "W2", "W3", "W4"]]
    bs = [w[k] for k in ["b0", "b1", "b2", "b3", "b4"]]
    dates = Xdf.index
    out = {c: pd.Series(np.nan, index=dates) for c in target_codes}
    # 预计算 Xmat 每个位置的块有限性（避免重复切片）
    finite = np.zeros(len(Xmat), dtype=bool)
    for i in range(len(Xmat)):
        if i < L - 1:
            continue
        block = Xmat[i - L + 1:i + 1]
        finite[i] = np.isfinite(block).all()
    last_valid = -1
    sig_cache = None
    for i in range(len(Xmat)):
        if finite[i]:
            if i != last_valid:
                block = Xmat[i - L + 1:i + 1].reshape(L, N, D_feat)
                x_new = block.transpose(1, 0, 2).reshape(1, -1)
                sig = np.mean([np.tanh(x_new @ W.T + b)[0] for W, b in zip(ws, bs)], axis=0)
                sig_cache = sig
                last_valid = i
        else:
            continue
        if sig_cache is not None:
            name2idx = {nm: k for k, nm in enumerate(names)}
            for c in target_codes:
                if c not in name2idx:
                    continue
                s = sig_cache[name2idx[c]]
                if s > 0.10:
                    out[c].iloc[i] = VIEW_MULTI
                elif s < -0.10:
                    out[c].iloc[i] = VIEW_SHORT
                else:
                    out[c].iloc[i] = VIEW_FLAT
    return out


def SL_names():
    from config import STOM_POOL
    return STOM_POOL


# STMOM 面板所需标的列（2023 回测版：12 标的，剔除恒生科技）
DF_COLS_REQ = ["sh510300", "sh510500", "sh510050", "sz159915", "sz159949",
               "sh512880", "sh512660", "sh512010", "sh513100", "sh513500",
               "sh588000", "sh512100"]


METHODS = {
    "dxke": (dxke_daily, "动力学MACD"),
    "madc": (madc_daily, "均线双通路"),
    "dwt": (dwt_daily, "小波DWT"),
    "ac1vmd": (ac1vmd_daily, "AC1-VMD"),
    "chan": (chan_daily, "缠论分型"),
}