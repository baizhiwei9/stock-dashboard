# -*- coding: utf-8 -*-
"""价差回归的轻量回测：保留旧 commodity_spread_scanner 的核心交易规则。

规则：
- |Z|≥entry_z 且分位≥extreme_pct 才触发；
- 可回归性用 ADF/Hurst 在入场日复核；
- 可选 N 日两腿动量反转确认（旧扫描器 N=3 口径）；
- |Z|≤exit_z 平仓；
- 等名义手数、双边手续费+滑点、保证金口径收益。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..config import BacktestConfig, HedgeConfig, Settings, SpreadDef
from ..Futures import catalog
from ..storage.sqlite_store import SQLiteStore
from . import stats as st


@dataclass
class Trade:
    spread_id: str
    direction: int          # +1 多价差，-1 空价差
    entry_date: str
    exit_date: str
    entry_value: float
    exit_value: float
    pnl: float
    cost: float
    hold_days: int
    entry_z: float
    exit_z: float


def _multipliers(spread: SpreadDef) -> Tuple[float, float]:
    def one(leg, override):
        if override:
            return float(override)
        code = leg.product if leg is not None else spread.product
        if not code and spread.contracts:
            code = spread.contracts[0].rstrip("0123456789")
        return float(catalog.product(code).multiplier)
    return one(spread.leg_a, spread.multiplier_a), one(spread.leg_b, spread.multiplier_b)


def backtest(settings: Settings, store: SQLiteStore, spread: SpreadDef,
             start: Optional[str] = None, end: Optional[str] = None,
             confirm_days: Optional[int] = None) -> Dict[str, object]:
    cfg: HedgeConfig = settings.hedge
    bt: BacktestConfig = settings.backtest
    confirm_days = cfg.confirm_days if confirm_days is None else confirm_days
    df = store.load_spread_daily(spread.id, start, end)
    if df.empty:
        raise RuntimeError(f"{spread.id} 无价差数据，请先运行 fetch/scan")

    df = df.sort_values("trade_date").reset_index(drop=True)
    win = cfg.percentile_window
    s = df["value"].astype(float)
    df["mean"] = s.rolling(win, min_periods=cfg.min_observations).mean()
    df["std"] = s.rolling(win, min_periods=cfg.min_observations).std(ddof=1)
    df["z"] = (s - df["mean"]) / df["std"].replace(0, np.nan)
    df["pct"] = s.rolling(win, min_periods=cfg.min_observations).apply(
        lambda w: st.percentile_rank(w, w.iloc[-1]), raw=False)
    df["ret_a"] = df["leg_a_price"].pct_change()
    df["ret_b"] = df["leg_b_price"].pct_change()

    mult_a, mult_b = _multipliers(spread)
    target = bt.target_notional
    cost_rate = bt.commission_turnover + bt.slippage_turnover

    trades: List[Trade] = []
    pos = 0
    entry_i = -1
    entry_value = 0.0
    entry_z = 0.0
    entry_cost = 0.0
    entry_lots = 1
    candidate = 0
    streak = 0

    for i, row in df.iterrows():
        z, pct = row["z"], row["pct"]
        if not np.isfinite(z) or not np.isfinite(pct):
            continue

        if pos != 0:
            if abs(z) <= cfg.exit_z:
                exit_value = float(row["value"])
                exit_cost = _round_trip_cost(row, target, cost_rate) / 2
                if spread.calc == "spread":
                    raw = pos * (exit_value - entry_value) * mult_a * entry_lots
                else:
                    raw = pos * (exit_value / entry_value - 1.0) * target * entry_lots
                trades.append(Trade(
                    spread_id=spread.id, direction=pos,
                    entry_date=df.at[entry_i, "trade_date"].strftime("%Y-%m-%d"),
                    exit_date=row["trade_date"].strftime("%Y-%m-%d"),
                    entry_value=entry_value, exit_value=exit_value,
                    pnl=raw - entry_cost - exit_cost, cost=entry_cost + exit_cost,
                    hold_days=i - entry_i, entry_z=entry_z, exit_z=float(z),
                ))
                pos, entry_i, candidate, streak = 0, -1, 0, 0
            continue

        # 触发候选
        new_candidate = 0
        if z >= cfg.entry_z and pct >= cfg.extreme_pct:
            new_candidate = -1
        elif z <= -cfg.entry_z and pct <= (1 - cfg.extreme_pct):
            new_candidate = 1
        if new_candidate == 0:
            candidate, streak = 0, 0
            continue
        candidate = new_candidate
        if confirm_days > 0:
            ok = (row["ret_a"] < 0 and row["ret_b"] > 0) if candidate == -1 else (row["ret_a"] > 0 and row["ret_b"] < 0)
            streak = streak + 1 if ok else 0
            if streak < confirm_days:
                continue

        # 统计稳健性复核（只用截至当日的窗口）
        hist = s.iloc[max(0, i - win + 1): i + 1]
        chk = st.compute_stats(hist, window=win, min_observations=cfg.min_observations,
                               vol_window=cfg.vol_window, adf_alpha=cfg.adf_alpha,
                               hurst_threshold=cfg.hurst_threshold, vol_extreme=cfg.vol_extreme)
        if chk.get("regime") in ("insufficient_history", "trend", "extreme_vol"):
            continue
        if not chk.get("stationary"):
            continue

        pos = candidate
        entry_i = i
        entry_value = float(row["value"])
        entry_z = float(z)
        entry_lots = 1
        if spread.calc == "spread":
            px = float(row["leg_a_price"]) * mult_a
            entry_lots = max(1, int(round(target / px))) if px > 0 else 1
        entry_cost = _round_trip_cost(row, target, cost_rate) / 2
        streak = 0

    # 期末仍持仓：按最后价格盯市，不记平仓成本
    if pos != 0 and entry_i >= 0:
        last = df.iloc[-1]
        if spread.calc == "spread":
            raw = pos * (float(last["value"]) - entry_value) * mult_a * entry_lots
        else:
            raw = pos * (float(last["value"]) / entry_value - 1.0) * target * entry_lots
        trades.append(Trade(
            spread_id=spread.id, direction=pos,
            entry_date=df.at[entry_i, "trade_date"].strftime("%Y-%m-%d"),
            exit_date=last["trade_date"].strftime("%Y-%m-%d") + "(持仓)",
            entry_value=entry_value, exit_value=float(last["value"]),
            pnl=raw - entry_cost, cost=entry_cost, hold_days=len(df) - 1 - entry_i,
            entry_z=entry_z, exit_z=float(last["z"]),
        ))

    trade_df = pd.DataFrame([t.__dict__ for t in trades])
    if trade_df.empty:
        return {"spread_id": spread.id, "trades": trade_df, "n_trades": 0,
                "total_pnl": 0.0, "win_rate": np.nan, "max_drawdown": 0.0,
                "sharpe": np.nan, "total_return": 0.0, "annualized": 0.0,
                "capital": 2 * target * bt.margin_rate, "avg_hold": np.nan}

    curve = trade_df.set_index("exit_date")["pnl"].cumsum()
    equity = curve.copy()
    running_max = equity.cummax()
    dd = equity - running_max
    max_dd = float(dd.min())
    capital = 2 * target * bt.margin_rate
    total_pnl = float(trade_df["pnl"].sum())
    duration_years = max((df["trade_date"].iloc[-1] - df["trade_date"].iloc[0]).days / 365.25, 1 / 365.25)
    ann = (1 + total_pnl / capital) ** (1 / duration_years) - 1 if capital > 0 else np.nan
    daily_pnl = equity.diff().dropna()
    sharpe = (daily_pnl.mean() / daily_pnl.std(ddof=1) * np.sqrt(bt.annual_days)) if len(daily_pnl) > 1 and daily_pnl.std(ddof=1) > 0 else np.nan
    return {
        "spread_id": spread.id, "trades": trade_df, "n_trades": len(trade_df),
        "total_pnl": total_pnl, "win_rate": float((trade_df["pnl"] > 0).mean()),
        "max_drawdown": max_dd, "sharpe": float(sharpe) if np.isfinite(sharpe) else np.nan,
        "total_return": total_pnl / capital if capital else np.nan,
        "annualized": float(ann), "capital": capital,
        "avg_hold": float(trade_df["hold_days"].mean()),
    }


def _round_trip_cost(row: pd.Series, target: float, rate: float) -> float:
    return 2 * (target + target) * rate