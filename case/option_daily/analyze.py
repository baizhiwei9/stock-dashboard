# -*- coding: utf-8 -*-
"""option_daily 计算层 —— 四个指标，全部复用既有实现，不自己重写算法

指标与来源
----------
    option.atm_iv     DataHub ``core.option_metrics.get_atm_iv``（最近月 ATM 购/沽 IV 均值）
    option.iv_skew    DataHub ``core.option_metrics.calc_25d_risk_reversal().rr``
                      （= put25Δ IV − call25Δ IV，原始差值，与落库口径一致）
    option.pcr_oi     DataHub ``core.option_metrics.calc_pcr(metric="oi")``
    option.gex_flip   options_utility ``calc.option_monitor.bs_gamma`` 做逐档 Gamma，
                      在现价 ±10% 网格上重算全链净 GEX 并找变号零点
                      （与原 ``bin/option_daily.py`` / ``gex_multi.py`` 同一算法，
                       不另写一套 BS/Gamma）

为什么 GEX 不在 DataHub 里
--------------------------
DataHub 的 option_metrics 只到 IV/偏度/PCR/VIX，没有 GEX 净额与 Flip 点；
GEX 目前只存在于 options_utility 的 ``calc/gex.py`` 与仓内
``case/option_risk_cn_us/gex_core.py``。这里按后者的做法做**薄适配**：
取数仍走 DataHub，Gamma 用 options_utility 的原函数，找不到库就报 missing，
不自己实现一套「差不多的」Gamma。

单位
----
GEX 论文口径：gamma(per share) × OI × 乘数 × S² × 1% ÷ 1e8 → 亿元 / 每 1% 变动。
"""
from __future__ import annotations

import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

CONTRACT_MULT = 10000.0     # 沪深300ETF 期权：10000 份/张
DTE_LO, DTE_HI = 7, 180     # 参与 GEX 的到期区间（剔当月临到期与超远期）
GRID_PCT = 0.10             # Flip 搜索范围：现价 ±10%
GRID_STEP = 0.005           # 网格步长 0.5%


# ── options_utility 定位：统一走 optlib（目录探测 + alert 同名模块隔离）──────
OPTSTACK_SUB = "options_utility"   # 只用于说明文案


def _bs_gamma():
    """返回 (bs_gamma, 失败原因)。目录探测与 alert 同名模块隔离都在 optlib 里。"""
    try:
        import optlib
        return optlib.bs_gamma(), ""
    except Exception as e:                                       # noqa: BLE001
        return None, str(e)[:220]


def _optstack_candidates():
    """候选目录列表（排障时打印用）。"""
    try:
        import optlib
        return optlib.optstack_paths()
    except Exception:                                            # noqa: BLE001
        return []


# ── 指标计算 ────────────────────────────────────────────────────────────────
def _contracts(rows, trade_date, spot):
    """DataHub 期权链 records → DataHub OptionContract 列表（IV 由 DataHub 反解）。"""
    import data                       # 先走 data 的引导，保证用同一份 data_hub
    data._hub()
    try:
        import optlib
        optlib.prepare_env()           # DataHub 的 IV 反解要在首次调用前认到路径
        optlib.ensure_option_iv()      # 再借一次 bs_iv，触发 DataHub 侧惰性绑定
    except Exception:                                            # noqa: BLE001
        pass
    from data_hub.core.option_metrics import standardize_contracts, calc_iv_for_chain
    import pandas as pd
    df = pd.DataFrame(rows)
    if df.empty:
        return []
    cs = standardize_contracts(df, source="tushare", exchange="SSE",
                               trade_date=trade_date)
    if not cs:
        return []
    try:
        cs = calc_iv_for_chain(cs, float(spot), r=0.025)
    except Exception:                                            # noqa: BLE001
        pass
    return cs


def with_iv(rows, trade_date, spot):
    """返回补好 IV 的合约列表（render 出微笑图要用）。"""
    return _contracts(rows, trade_date, spot)


def compute_metrics(rows, trade_date, spot):
    """四个指标 + 诊断信息。算不出来的字段给 None（→ emit 写 missing）。"""
    import data
    data._hub()
    from data_hub.core.option_metrics import (calc_25d_risk_reversal, calc_pcr,
                                             get_atm_iv)
    out = {"atm_iv": None, "iv_skew": None, "pcr_oi": None, "gex_flip": None,
           "spot": spot, "diagnostics": {}}
    cs = _contracts(rows, trade_date, spot)
    out["diagnostics"]["n_contracts"] = len(cs)
    if not cs:
        out["diagnostics"]["reason"] = "期权链标准化后为 0 个合约"
        return out
    out["diagnostics"]["n_with_iv"] = sum(1 for c in cs if c.iv and c.iv > 0)

    try:
        rr = calc_25d_risk_reversal(cs, float(spot), trade_date=trade_date, r=0.025)
    except Exception as e:                                       # noqa: BLE001
        rr = None
        out["diagnostics"]["skew_err"] = str(e)[:120]
    if rr:
        out["iv_skew"] = rr.get("rr")
        out["diagnostics"].update({"expiry": rr.get("expiry"), "dte": rr.get("dte"),
                                   "put25_iv": rr.get("put25_iv"),
                                   "call25_iv": rr.get("call25_iv")})
    else:
        out["diagnostics"]["skew_err"] = out["diagnostics"].get("skew_err") or "合约不足"

    try:
        pcr = calc_pcr(cs, metric="oi")
        # calc_pcr 在「无认购持仓」时返回 inf —— 那是数据缺失，不是极端值
        out["pcr_oi"] = pcr if (pcr is not None and math.isfinite(pcr) and pcr > 0) else None
        if out["pcr_oi"] is None:
            out["diagnostics"]["pcr_note"] = "PCR 非有限值（认购持仓为 0）"
    except Exception as e:                                       # noqa: BLE001
        out["diagnostics"]["pcr_err"] = str(e)[:120]

    try:
        atm = get_atm_iv(cs, float(spot))
        out["atm_iv"] = atm if (atm and atm > 0) else None
        if out["atm_iv"] is None:
            out["diagnostics"]["atm_note"] = "最近月 ATM 购/沽 IV 都取不到"
    except Exception as e:                                       # noqa: BLE001
        out["diagnostics"]["atm_err"] = str(e)[:120]

    gx = compute_gex_flip(cs, float(spot), trade_date)
    out["gex_flip"] = gx.get("flip")
    out["diagnostics"]["gex"] = gx
    return out


# ── GEX / Gamma Flip ────────────────────────────────────────────────────────
def _legs(cs, trade_date):
    """本月合约 → [(K, is_call, iv, oi, T)]，只保留 dte ∈ [DTE_LO, DTE_HI] 且 IV 可用。"""
    legs = []
    for c in cs:
        try:
            dte = int(c.dte)
            iv = float(c.iv or 0)
            k = float(c.strike or 0)
            oi = float(c.open_interest or 0)
        except (TypeError, ValueError):
            continue
        if dte < DTE_LO or dte > DTE_HI:
            continue
        if iv <= 0.02 or iv > 4.5 or k <= 0 or oi <= 0:
            continue
        legs.append((k, c.opt_type == "C", iv, oi, dte / 365.0))
    return legs


def net_gex(legs, s_star, gamma_fn, mult=CONTRACT_MULT, r=0.02):
    """给定标的价格 S*，全链净 GEX（亿元 / 每 1% 变动）。"""
    total = 0.0
    for k, is_call, iv, oi, T in legs:
        if T <= 0:
            continue
        try:
            g = float(gamma_fn(s_star, k, T, r, iv))
        except Exception:                                        # noqa: BLE001
            continue
        if not math.isfinite(g):
            continue
        sign = 1.0 if is_call else -1.0
        total += sign * g * oi * mult * (s_star ** 2) * 0.01
    return total / 1e8


def compute_gex_flip(cs, spot, trade_date, r=0.02):
    """在 spot±GRID_PCT 网格上重算净 GEX，取离现价最近的变号点作为 Flip。

    返回 {"flip": float|None, "spot": spot, "dist_pct":..., "net_gex_yi":...,
          "reason": 失败原因}
    """
    res = {"flip": None, "spot": spot, "net_gex_yi": None, "dist_pct": None,
           "n_legs": 0, "reason": ""}
    gamma_fn, why = _bs_gamma()
    if gamma_fn is None:
        res["reason"] = why
        return res
    legs = _legs(cs, trade_date)
    res["n_legs"] = len(legs)
    if len(legs) < 10:
        res["reason"] = "参与 GEX 的合约仅 %d 条（<10）" % len(legs)
        return res
    res["net_gex_yi"] = round(net_gex(legs, spot, gamma_fn, r=r), 4)

    grid = []
    n = int(GRID_PCT / GRID_STEP)
    for i in range(-n, n + 1):
        s = spot * (1 + i * GRID_STEP)
        if s <= 0:
            continue
        grid.append((s, net_gex(legs, s, gamma_fn, r=r)))
    grid.sort()
    flips = []
    for (s0, y0), (s1, y1) in zip(grid, grid[1:]):
        if y0 == 0:
            flips.append(s0)
        elif y0 * y1 < 0:
            # 线性插值求零点
            w = -y0 / (y1 - y0)
            flips.append(s0 + w * (s1 - s0))
    if not flips:
        res["reason"] = "现价 ±%.0f%% 内净 GEX 未变号（无 Flip 点）" % (GRID_PCT * 100)
        return res
    flip = min(flips, key=lambda x: abs(x - spot))
    res["flip"] = round(flip, 4)
    res["dist_pct"] = round((flip / spot - 1) * 100, 2)
    res["n_flips"] = len(flips)
    return res
