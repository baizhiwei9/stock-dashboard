#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gex_core.py —— 科创50ETF(588000) 期权 GEX / Gamma Flip 纯计算库
================================================================================

设计原则
--------
本模块**只做计算，不做 IO**（不读文件、不写文件、不发邮件、不读系统时间）。
调用方（gex_flip_monitor.py）负责喂 DataFrame 进来、决定输出到哪。

口径（与《GEX_Flip 设计文档》v1.0 一致）
----------------------------------------
    单腿 Gamma(per share)  Γ = e^{-qT} φ(d1) / (S σ √T)
        d1 = [ln(S/K) + (r − q + σ²/2)·T] / (σ√T),  T = DTE/365
    单腿 GEX               GEX_i = sign_i · Γ_i · OI_i · MULT · S² · 0.01
        sign_i = +1(Call) / −1(Put)
        单位：元 / 每 1% 标的变动；对外展示统一 ÷1e8 → 亿元
    净 GEX                  Σ GEX_i （每个到期月先各自汇总，再合计）

期限口径
--------
    * 剔除近月：DTE < dte_lo(默认 30)
    * 剔除远月：DTE > dte_hi(默认 180)
    * 30/90 恒定期限加权（VIX 式插值）：gex_weighted = 0.5·gex30 + 0.5·gex90

Gamma Flip
----------
    在 S* ∈ [S·0.90, S·1.10]（步长 0.5%）网格上逐点重算全链 Γ，
    在曲线变号处线性插值求零点；flip_1 取离现价最近者。

公开 API
--------
    prepare_chain / build_legs / net_gex_at / gex_curve / find_flips /
    weighted_gex / daily_snapshot / percentile_rank

单位约定：本模块所有 gex_* 返回值均为 **亿元**（已 ÷1e8）。
"""
from __future__ import annotations

import math
import datetime as _dt
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

import lib_adapter as LIB  # 期权计算原语走 options_utility（用户口径 2026-11-13）

# ------------------------------------------------------------------ 常量
SQRT2 = math.sqrt(2.0)
INV_SQRT_2PI = 1.0 / math.sqrt(2.0 * math.pi)

MULT = 10000.0           # 科创50ETF 期权合约乘数：10000 股/张
IV_LO, IV_HI = 0.02, 4.5  # 可接受 IV 区间，超出视为反解失败
SCALE = 1e8              # 元 -> 亿元

DEF_R = 0.02             # 无风险利率（与设计文档默认一致）
DEF_Q = 0.0              # 股息率
DEF_DTE_LO = 30
DEF_DTE_HI = 180
DEF_TARGETS = (30, 90)
DEF_WEIGHTS = (0.5, 0.5)
DEF_CURVE_LO = 0.90
DEF_CURVE_HI = 1.10
DEF_CURVE_STEP = 0.005


# ================================================================== BSM 基础
def npdf(x: float) -> float:
    return INV_SQRT_2PI * math.exp(-0.5 * x * x)


def ncdf(x: float) -> float:
    return 0.5 * math.erfc(-x / SQRT2)


def bs_price(S: float, K: float, T: float, vol: float,
             r: float, q: float, call: bool = True) -> float:
    """BS 理论价（转调 options_utility.calc.option_monitor.bs_call/bs_put）。"""
    if T <= 0 or vol <= 0:
        return max(0.0, (S - K) if call else (K - S))
    return LIB.bs_call(S, K, T, r, vol) if call else LIB.bs_put(S, K, T, r, vol)


def implied_vol(price: float, S: float, K: float, T: float,
                r: float, q: float, call: bool = True,
                lo: float = 1e-4, hi: float = IV_HI,
                iters: int = 100) -> float:
    """BS 反解 IV（转调 calc.option_monitor.bs_iv），保留本地可接受 IV 区间判定。"""
    if (not np.isfinite(price)) or price <= 0 or S <= 0 or K <= 0 or T <= 0:
        return np.nan
    intr = max(0.0, (S - K) if call else (K - S))
    if price < intr * 0.98:
        return np.nan
    v = LIB.bs_iv(S, K, T, r, float(price), "call" if call else "put")
    if v is None or not np.isfinite(v):
        return np.nan
    v = float(v)
    if not (IV_LO < v < IV_HI):
        return np.nan
    return v


def gamma_at(S: float, K: float, T: float, vol: float,
             r: float = DEF_R, q: float = DEF_Q) -> float:
    """BS 单腿 Gamma（转调 calc.option_monitor.bs_gamma）。"""
    if T <= 0 or vol <= 0 or S <= 0 or K <= 0:
        return 0.0
    return float(LIB.bs_gamma(S, K, T, r, vol))


# ================================================================== 数据整形
def prepare_chain(hist_df: pd.DataFrame) -> pd.DataFrame:
    """规范化期权链 DataFrame（幂等）。

    期望列：trade_date, K, cp, maturity, close, settle, oi
    输出附加列：mid（close>0 用 close，否则 settle）、call(bool)
    同时剔除 mid<=0 / K<=0 / oi<=0 的脏腿。
    """
    d = hist_df.copy()
    d['trade_date'] = pd.to_numeric(d['trade_date'], errors='coerce').astype('Int64')
    if not np.issubdtype(d['maturity'].dtype, np.datetime64):
        d['maturity'] = pd.to_datetime(
            d['maturity'].astype(str).str.replace(r'\.0$', '', regex=True).str[:8],
            format='%Y%m%d', errors='coerce')
    for c in ('close', 'settle', 'vol', 'oi', 'K'):
        if c in d.columns:
            d[c] = pd.to_numeric(d[c], errors='coerce')
    d['mid'] = d['close'].where(d['close'] > 0, d['settle'])
    d['call'] = d['cp'].astype(str).str.upper().str.startswith('C')
    d = d[(d['mid'] > 0) & (d['K'] > 0) & (d['oi'] > 0)
          & d['trade_date'].notna() & d['maturity'].notna()]
    return d.reset_index(drop=True)


def _to_date(x) -> _dt.date:
    if isinstance(x, _dt.date) and not isinstance(x, _dt.datetime):
        return x
    if isinstance(x, _dt.datetime):
        return x.date()
    s = str(int(x))
    return _dt.date(int(s[:4]), int(s[4:6]), int(s[6:8]))


# ================================================================== 单日腿表
def build_legs(chain: pd.DataFrame, date: int, S: float,
               r: float = DEF_R, q: float = DEF_Q,
               dte_lo: int = DEF_DTE_LO, dte_hi: int = DEF_DTE_HI,
               mult: float = MULT) -> Tuple[pd.DataFrame, Dict]:
    d = chain[chain['trade_date'] == int(date)]
    if d.empty:
        raise ValueError('期权链快照为空: %s' % date)
    d0 = _to_date(date)
    d = d.assign(dte=[(_to_date(m) - d0).days for m in d['maturity']])
    n_raw = len(d)
    d = d[(d['dte'] >= dte_lo) & (d['dte'] <= dte_hi)]
    n_dte = len(d)
    cols = ['K', 'call', 'maturity', 'dte', 'T', 'iv', 'oi', 'sign', 'gamma', 'gex', 'gex_abs']
    if d.empty:
        return pd.DataFrame(columns=cols), dict(n_raw=n_raw, n_dte=0, n_mid=0, n_iv=0)

    d = d.assign(T=d['dte'] / 365.0)
    d = d.assign(iv=[implied_vol(row.mid, S, row.K, row.T, r, q, row.call)
                     for row in d.itertuples()])
    n_mid = len(d)
    d = d[np.isfinite(d['iv'])]
    n_iv = len(d)
    if d.empty:
        return d.assign(sign=[], gamma=[], gex=[], gex_abs=[]), \
            dict(n_raw=n_raw, n_dte=n_dte, n_mid=n_mid, n_iv=0)

    d = d.assign(sign=np.where(d['call'], 1.0, -1.0))
    d = d.assign(gamma=[gamma_at(S, row.K, row.T, row.iv, r, q)
                        for row in d.itertuples()])
    unit = mult * S * S * 0.01 / SCALE
    d = d.assign(gex=d['sign'] * d['gamma'] * d['oi'] * unit)
    d = d.assign(gex_abs=d['gamma'] * d['oi'] * unit)
    return d.reset_index(drop=True), dict(n_raw=n_raw, n_dte=n_dte, n_mid=n_mid, n_iv=n_iv)


# ================================================================== 曲线 / Flip
def net_gex_at(legs: pd.DataFrame, S_star: float,
               r: float = DEF_R, q: float = DEF_Q, mult: float = MULT) -> float:
    if legs.empty:
        return 0.0
    unit = mult * S_star * S_star * 0.01 / SCALE
    tot = 0.0
    for row in legs.itertuples():
        tot += row.sign * gamma_at(S_star, row.K, row.T, row.iv, r, q) * row.oi
    return tot * unit


def gex_curve(legs: pd.DataFrame, S: float,
              r: float = DEF_R, q: float = DEF_Q,
              lo: float = DEF_CURVE_LO, hi: float = DEF_CURVE_HI,
              step: float = DEF_CURVE_STEP,
              mult: float = MULT) -> Tuple[np.ndarray, np.ndarray]:
    n = int(round((hi - lo) / step)) + 1
    s_arr = np.round(np.linspace(S * lo, S * hi, n), 6)
    if legs.empty:
        return s_arr, np.zeros(len(s_arr))
    K = legs['K'].to_numpy(float)
    T = legs['T'].to_numpy(float)
    iv = legs['iv'].to_numpy(float)
    oi = legs['oi'].to_numpy(float)
    sign = legs['sign'].to_numpy(float)
    sq = iv * np.sqrt(T)
    s2 = s_arr[:, None]
    d1 = (np.log(s2 / K[None, :]) + (r - q + 0.5 * iv * iv)[None, :] * T[None, :]) / sq[None, :]
    gam = np.exp(-q * T)[None, :] * np.exp(-0.5 * d1 * d1) * INV_SQRT_2PI / (s2 * sq[None, :])
    contrib = (sign * oi)[None, :] * gam
    y = contrib.sum(axis=1) * (mult * s_arr * s_arr * 0.01 / SCALE)
    return s_arr, y


def _zeros(s_arr: np.ndarray, y: np.ndarray) -> List[Tuple[float, float]]:
    zs: List[Tuple[float, float]] = []
    for i in range(len(y) - 1):
        a, b = float(y[i]), float(y[i + 1])
        if a == 0.0:
            zs.append((float(s_arr[i]), b - a))
        elif a * b < 0:
            t = a / (a - b)
            zs.append((float(s_arr[i] + t * (s_arr[i + 1] - s_arr[i])), b - a))
    if len(y) and float(y[-1]) == 0.0:
        zs.append((float(s_arr[-1]), 0.0))
    out: List[Tuple[float, float]] = []
    for z, sl in zs:
        if not out or abs(z - out[-1][0]) > 1e-9:
            out.append((z, sl))
    return out


def find_flips(s_arr: np.ndarray, y: np.ndarray,
               S: float) -> Tuple[List[Dict], Optional[float]]:
    zs = _zeros(s_arr, y)
    zs = sorted(zs, key=lambda t: abs(t[0] - S))
    flips = [dict(S=float(z), slope=float(sl), dist_pct=float((S - z) / S * 100.0))
             for z, sl in zs]
    return flips, (flips[0]['S'] if flips else None)


# ================================================================== 30/90 加权
def _interp_at(pts: Sequence[Tuple[int, float]], target: float) -> Tuple[float, str]:
    if not pts:
        return float('nan'), 'none'
    ds = [p[0] for p in pts]
    if target in ds:
        return float(pts[ds.index(target)][1]), 'exact'
    if len(pts) == 1:
        return float(pts[0][1]), 'single'
    if target < ds[0]:
        (x0, y0), (x1, y1) = pts[0], pts[1]
        mode = 'extrap_lo'
    elif target > ds[-1]:
        (x0, y0), (x1, y1) = pts[-2], pts[-1]
        mode = 'extrap_hi'
    else:
        mode = 'interp'
        x0 = y0 = x1 = y1 = None
        for i in range(len(pts) - 1):
            if ds[i] < target < ds[i + 1]:
                (x0, y0), (x1, y1) = pts[i], pts[i + 1]
                break
        if x0 is None:
            return float('nan'), 'none'
    if x1 == x0:
        return float(y0), mode
    return float(y0 + (y1 - y0) * (target - x0) / (x1 - x0)), mode


def weighted_gex(legs: pd.DataFrame, S: Optional[float] = None,
                 targets: Tuple[int, int] = DEF_TARGETS,
                 weights: Tuple[float, float] = DEF_WEIGHTS) -> Dict[str, object]:
    """30/90 恒定期限加权 GEX（转调 calc.cn.gex_weighted.weighted_gex，权重口径不变）。"""
    if legs.empty:
        return dict(gex30=float('nan'), gex90=float('nan'),
                    gex_weighted=float('nan'), gex_mix_simple=float('nan'),
                    n_legs=0, n_maturities=0, dte_list='', interp_mode='none/none')
    grp = legs.groupby('maturity').agg(dte=('dte', 'first'),
                                       net=('gex', 'sum'),
                                       n=('gex', 'size'))
    grp = grp.sort_values('dte')
    legs_in = [dict(maturity=None, dte=int(r.dte), gex=float(r.net))
               for r in grp.itertuples()]
    out = LIB.lib_weighted_gex(legs_in, S, targets=targets, weights=weights)
    mode = str(out.get('interp_mode', '')).replace('-', '_')
    out['interp_mode'] = mode
    return out


# ================================================================== 分位数
def percentile_rank(hist_values: Iterable[float], value: float) -> Tuple[float, int]:
    """pct = 100 × #(样本 ≤ value) / N（转调 calc.cn.gex_weighted.percentile_rank）。"""
    p, n = LIB.lib_percentile_rank(list(hist_values), value)
    return float(p), int(n)


# ================================================================== 单日快照
def daily_snapshot(chain: pd.DataFrame, date: int, S: float,
                   r: float = DEF_R, q: float = DEF_Q,
                   dte_lo: int = DEF_DTE_LO, dte_hi: int = DEF_DTE_HI,
                   targets: Tuple[int, int] = DEF_TARGETS,
                   weights: Tuple[float, float] = DEF_WEIGHTS,
                   curve_lo: float = DEF_CURVE_LO, curve_hi: float = DEF_CURVE_HI,
                   curve_step: float = DEF_CURVE_STEP,
                   with_flip: bool = True, mult: float = MULT) -> Dict[str, object]:
    legs, meta = build_legs(chain, date, S, r=r, q=q, dte_lo=dte_lo, dte_hi=dte_hi,
                            mult=mult)
    if legs.empty:
        return dict(trade_date=int(date), S=float(S), gex30=float('nan'),
                    gex90=float('nan'), gex_weighted=float('nan'),
                    gex_mix_simple=float('nan'), n_legs=0, n_maturities=0,
                    dte_list='', interp_mode='none/none', quality='empty',
                    flip_1=None, flip_2=None, flip_count=0, dist=float('nan'),
                    curve_min=float('nan'), curve_max=float('nan'),
                    legs=legs, meta=meta)
    w = weighted_gex(legs, S, targets=targets, weights=weights)
    out: Dict[str, object] = dict(trade_date=int(date), S=float(S), **w)
    out['quality'] = 'ok' if w['n_maturities'] >= 2 else 'low'
    out['flip_1'] = None
    out['flip_2'] = None
    out['flip_count'] = 0
    out['dist'] = float('nan')
    out['curve_min'] = float('nan')
    out['curve_max'] = float('nan')
    if with_flip:
        s_arr, y = gex_curve(legs, S, r=r, q=q, lo=curve_lo, hi=curve_hi, step=curve_step,
                             mult=mult)
        flips, f1 = find_flips(s_arr, y, S)
        out['flip_1'] = f1
        out['flip_2'] = flips[1]['S'] if len(flips) > 1 else None
        out['flip_count'] = len(flips)
        out['dist'] = float((S - f1) / S * 100.0) if f1 is not None else float('nan')
        out['curve_min'] = float(np.min(y))
        out['curve_max'] = float(np.max(y))
        out['curve'] = (s_arr, y)
    out['legs'] = legs
    out['meta'] = meta
    return out


# ================================================================== 自检
def _self_check() -> None:
    rows = []
    for k in (1.3, 1.4, 1.5):
        for cp in ('C', 'P'):
            rows.append(dict(trade_date=20261023, K=k, cp=cp, maturity=20261223,
                             close=0.05, settle=0.05, vol=1.0, oi=1000.0))
    chain = prepare_chain(pd.DataFrame(rows))
    legs, _ = build_legs(chain, 20261023, 1.42)
    a = float(legs['gex'].sum())
    b = net_gex_at(legs, 1.42)
    assert abs(a - b) < 1e-9, (a, b)
    print('self-check ok: ΣGEX_i=%.12f  curve(S)=%.12f  diff=%.3e' % (a, b, a - b))


if __name__ == '__main__':
    _self_check()