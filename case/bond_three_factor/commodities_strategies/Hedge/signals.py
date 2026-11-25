# -*- coding: utf-8 -*-
"""信号判定：统计稳健性门槛 + 极值分位 + 基本面冲突否决。

判定顺序与截图一致：
1. 样本是否足够（至少近一年）；
2. 价差是否可回归（ADF 平稳、Hurst<0.5）；
3. 波动率是否处于极端（>80% 分位不加新仓）；
4. 价差是否到极值（|Z|≥2 且分位≥95%/≤5%）；
5. 基差与仓单是否同时反对（基本面冲突 → 只观察不下单）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from ..config import HedgeConfig

LONG = "LONG_SPREAD"
SHORT = "SHORT_SPREAD"


@dataclass
class SignalDecision:
    spread_id: str
    asof_date: str
    action: str
    direction: str
    score: float
    z: float
    pct: float
    adf_p: float
    hurst: float
    vol_pct: float
    fundamental_flag: str = "unknown"
    reasons: List[str] = field(default_factory=list)

    def to_row(self) -> Dict[str, object]:
        return {
            "spread_id": self.spread_id, "asof_date": self.asof_date, "action": self.action,
            "direction": self.direction, "score": self.score, "z": self.z, "pct": self.pct,
            "adf_p": self.adf_p, "hurst": self.hurst, "vol_pct": self.vol_pct,
            "fundamental_flag": self.fundamental_flag, "reasons": self.reasons,
        }


def _f(v) -> float:
    try:
        return float(v) if v is not None and np.isfinite(float(v)) else float("nan")
    except (TypeError, ValueError):
        return float("nan")


def evaluate(spread_id: str, asof_date: str, stats: Dict[str, object], cfg: HedgeConfig,
             fund: Optional[Dict[str, object]] = None, position: int = 0) -> SignalDecision:
    z, pct = _f(stats.get("z")), _f(stats.get("pct"))
    adf_p, hurst, vol_pct = _f(stats.get("adf_p")), _f(stats.get("hurst")), _f(stats.get("vol_pct"))
    n_obs = int(stats.get("n_obs") or 0)
    base = dict(spread_id=spread_id, asof_date=asof_date, z=z, pct=pct, adf_p=adf_p,
                hurst=hurst, vol_pct=vol_pct, score=abs(z) if np.isfinite(z) else 0.0)

    if stats.get("regime") == "no_data" or n_obs == 0:
        return SignalDecision(action="NO_DATA", direction="FLAT", reasons=["价差序列为空"], **base)

    if n_obs < cfg.min_observations:
        return SignalDecision(
            action="INSUFFICIENT_HISTORY", direction="FLAT",
            reasons=[f"样本仅 {n_obs} 个交易日，不足最低要求 {cfg.min_observations}（约一年）"],
            **base,
        )

    if position == 0 and np.isfinite(z) and abs(z) <= cfg.exit_z:
        return SignalDecision(action="NO_SIGNAL", direction="FLAT",
                              reasons=[f"|Z|={abs(z):.2f} 在持仓区间内，无新开仓机会"], **base)

    # 已有持仓：先按平仓阈值处理，不被新的统计门槛"锁死"在仓位里
    if position != 0:
        if np.isfinite(z) and abs(z) <= cfg.exit_z:
            return SignalDecision(action="EXIT", direction="FLAT",
                                  reasons=[f"|Z|={abs(z):.2f} 回落至 {cfg.exit_z}，触发平仓"], **base)
        return SignalDecision(action=("HOLD_LONG" if position > 0 else "HOLD_SHORT"),
                              direction=(LONG if position > 0 else SHORT),
                              reasons=[f"持仓中，|Z|={abs(z):.2f} 未回落至平仓线"], **base)

    if np.isfinite(hurst) and hurst >= cfg.hurst_threshold:
        return SignalDecision(action="NO_SIGNAL", direction="FLAT",
                              reasons=[f"Hurst={hurst:.2f}≥{cfg.hurst_threshold:.2f}，价差偏趋势，均值回归失效"],
                              **base)
    if not np.isfinite(adf_p):
        return SignalDecision(action="NO_SIGNAL", direction="FLAT", reasons=["ADF 检验不可用"], **base)
    if adf_p >= cfg.adf_alpha:
        return SignalDecision(action="NO_SIGNAL", direction="FLAT",
                              reasons=[f"ADF p={adf_p:.4f}≥{cfg.adf_alpha}，价差非平稳，不做回归"], **base)

    if np.isfinite(vol_pct) and vol_pct > cfg.vol_extreme:
        return SignalDecision(action="RISK_OFF", direction="FLAT",
                              reasons=[f"波动率分位 {vol_pct:.1%}>{cfg.vol_extreme:.0%}，极端波动不加新仓"],
                              **base)

    short_trigger = np.isfinite(z) and np.isfinite(pct) and z >= cfg.entry_z and pct >= cfg.extreme_pct
    long_trigger = np.isfinite(z) and np.isfinite(pct) and z <= -cfg.entry_z and pct <= (1 - cfg.extreme_pct)
    if not (short_trigger or long_trigger):
        return SignalDecision(
            action="NO_SIGNAL", direction="FLAT",
            reasons=[f"未到极值：Z={z:.2f}，分位={pct:.1%}，阈值 |Z|≥{cfg.entry_z} 且分位≥{cfg.extreme_pct:.0%}"],
            **base,
        )

    direction = SHORT if short_trigger else LONG
    flag = "unknown"
    reasons: List[str] = []
    if cfg.fundamental_filter and fund:
        from .fundamentals import detect_conflict
        veto, flags = detect_conflict(direction, fund, require_both=cfg.require_both_conflicts,
                                      basis_high=cfg.basis_extreme_high, basis_low=cfg.basis_extreme_low,
                                      wh_pct=cfg.warehouse_chg_pct)
        if veto:
            return SignalDecision(action="WATCH_CONFLICT", direction=direction,
                                  fundamental_flag="conflict", reasons=flags or ["基本面冲突"],
                                  **base)
        flag = "neutral"
        if flags:
            reasons.extend(flags)
            flag = "warning"
    elif fund:
        flag = "neutral"

    extreme = "高分位" if direction == SHORT else "低分位"
    reasons.insert(0, f"Z={z:.2f}，分位={pct:.1%}，价差处于{extreme}，统计上{'做空' if direction == SHORT else '做多'}价差")
    reasons.append(f"ADF p={adf_p:.4f} 平稳，Hurst={hurst:.2f} 偏回归，波动率分位={vol_pct:.1%}")
    return SignalDecision(action=direction, direction=direction, fundamental_flag=flag,
                          reasons=reasons, **base)