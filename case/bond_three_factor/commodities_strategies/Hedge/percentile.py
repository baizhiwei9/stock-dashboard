# -*- coding: utf-8 -*-
"""价差分位回归回测（可复用模块）。

规则（与 config/percentile_reversion.yaml 对齐）：
- 252 个交易观测的滚动经验分位；
- 分位 ≥ entry_high 做空价差，≤ entry_low 做多价差；
- 分位回到 exit_mid 附近、达到最大持有期或触发 Z 止损时平仓；
- t 日收盘算信号，t+1 日收盘成交；
- 每条价差独立持仓，多价差按活跃等权组合。

不涉及横截面排序，不涉及多空对冲组合。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

from ..config import Settings, SpreadDef
from ..Futures import catalog
from ..storage.sqlite_store import SQLiteStore
from . import stats as st

TRADING_DAYS = 252


@dataclass
class Variant:
    name: str
    entry_high: float = 0.95
    entry_low: float = 0.05
    exit_mid: float = 0.50
    stop_z: Optional[float] = None
    max_hold_days: int = 40
    filter: str = "none"
    window: int = 252
    min_observations: int = 252
    ma_stop: bool = False
    ma_window: int = 40
    adf_alpha: float = 0.05
    hurst_threshold: float = 0.50


@dataclass
class SpreadResult:
    spread_id: str
    name: str
    sector: str
    daily: pd.DataFrame          # trade_date, ret
    trades: pd.DataFrame
    metrics: Dict[str, float]


def rolling_percentile(values: pd.Series, window: int = 252,
                        min_obs: int = 252) -> pd.Series:
    """当前值在最近 window 个观测中的经验分位（含当前值）。"""
    arr = pd.Series(values).astype(float)
    out = pd.Series(index=arr.index, dtype=float)
    vals = arr.to_numpy()
    for i in range(len(vals)):
        if i + 1 < min_obs:
            continue
        win = vals[max(0, i + 1 - window): i + 1]
        win = win[np.isfinite(win)]
        if len(win) < min_obs or not np.isfinite(vals[i]):
            continue
        out.iloc[i] = float(np.mean(win <= vals[i]))
    return out


def rolling_z(values: pd.Series, window: int = 252, min_obs: int = 252) -> pd.Series:
    s = pd.Series(values).astype(float)
    mean = s.rolling(window, min_periods=min_obs).mean()
    std = s.rolling(window, min_periods=min_obs).std(ddof=1)
    return (s - mean) / std.replace(0, np.nan)


def _multipliers(spread: SpreadDef) -> tuple[float, float]:
    def one(leg, override):
        if override:
            return float(override)
        code = leg.product if leg is not None else spread.product
        if not code and spread.contracts:
            code = spread.contracts[0].rstrip("0123456789")
        return float(catalog.product(code).multiplier)
    return one(spread.leg_a, spread.multiplier_a), one(spread.leg_b, spread.multiplier_b)


def ma_stop_triggered(pos: int, value: float, ma: float,
                      prev_value: float, prev_ma: float) -> bool:
    """MA 止损只认“跌破/涨破”这个穿越事件，不认静态在均线下方。

    - 多头：前一日收盘在 MA 之上（或持平），当日收盘跌破 MA；
    - 空头：前一日收盘在 MA 之下（或持平），当日收盘涨破 MA。
    """
    if pos == 0 or not all(np.isfinite([value, ma, prev_value, prev_ma])):
        return False
    if pos == 1:
        return prev_value >= prev_ma and value < ma
    if pos == -1:
        return prev_value <= prev_ma and value > ma
    return False


def _daily_pnl(row: pd.Series, prev: pd.Series, pos: int, spread: SpreadDef,
               lots: float, capital: float, mult_a: float, mult_b: float) -> float:
    if spread.calc == "spread":
        return float(pos * ((row["leg_a_price"] - prev["leg_a_price"]) * mult_a
                            - (row["leg_b_price"] - prev["leg_b_price"]) * mult_b) * lots)
    # ratio：两腿等名义，按比价收益率差
    ra = row["leg_a_price"] / prev["leg_a_price"] - 1
    rb = row["leg_b_price"] / prev["leg_b_price"] - 1
    return float(pos * (ra - rb) * capital)


def backtest_spread(store: SQLiteStore, spread: SpreadDef, sector: str, variant: Variant,
                    settings: Settings, start: Optional[str] = None,
                    end: Optional[str] = None) -> SpreadResult:
    df = store.load_spread_daily(spread.id, start, end)
    if df.empty:
        raise RuntimeError(f"{spread.id} 无价差数据")
    df = df.sort_values("trade_date").reset_index(drop=True)
    df["pct"] = rolling_percentile(df["value"], variant.window,
                                   variant.min_observations)
    df["z"] = rolling_z(df["value"], variant.window, variant.min_observations)
    df["ma"] = df["value"].rolling(variant.ma_window,
                                   min_periods=variant.ma_window).mean()
    mult_a, mult_b = _multipliers(spread)
    capital = settings.backtest.target_notional
    margin_capital = 2 * capital * settings.backtest.margin_rate
    rate = settings.backtest.commission_turnover + settings.backtest.slippage_turnover
    entry_leg_cost = 2 * capital * rate
    exit_leg_cost = entry_leg_cost

    n = len(df)
    daily_pnl = np.zeros(n)
    pos = 0
    entry_idx = -1
    entry_value = 0.0
    entry_pct = np.nan
    pending_entry = 0
    pending_exit_reason: Optional[str] = None
    entry_signals = 0
    filter_rejected = 0
    trade_pnl = 0.0
    trade_roll_cost = 0.0
    trades: List[dict] = []

    for i in range(1, n):
        row, prev = df.iloc[i], df.iloc[i - 1]
        # 1) 昨日收盘已建立的仓位，今日盯市
        if pos != 0:
            pnl = _daily_pnl(row, prev, pos, spread, 1.0, capital, mult_a, mult_b)
            # lots 依据入场时价格确定，_daily_pnl 中用 1 手，再乘 lots
            lots = capital / max(float(df.iloc[entry_idx]["leg_a_price"]) * mult_a, 1e-9)
            pnl *= lots if spread.calc == "spread" else 1.0
            daily_pnl[i] += pnl
            trade_pnl += pnl
            # 持仓期间的换月成本：两腿各平/开一次，由构建器写入 roll_cost
            rc = float(row.get("roll_cost") or 0.0)
            if row.get("roll_flag", 0) and rc:
                daily_pnl[i] -= rc
                trade_pnl -= rc
                trade_roll_cost += rc

        # 2) 执行昨日收盘发出的离场
        if pos != 0 and pending_exit_reason:
            exit_reason = pending_exit_reason
            exit_value = float(row["value"])
            daily_pnl[i] -= exit_leg_cost
            trades.append({
                "spread_id": spread.id, "name": spread.name, "sector": sector,
                "direction": int(pos), "entry_date": df.iloc[entry_idx]["trade_date"],
                "exit_date": row["trade_date"], "entry_value": entry_value,
                "exit_value": exit_value, "pnl": trade_pnl - exit_leg_cost,
                "cost": entry_leg_cost + exit_leg_cost + trade_roll_cost,
                "roll_cost": trade_roll_cost,
                "hold_days": int(i - entry_idx), "entry_pct": entry_pct,
                "exit_pct": float(row["pct"]) if pd.notna(row["pct"]) else np.nan,
                "reason": exit_reason,
            })
            pos, entry_idx, pending_exit_reason, trade_pnl, trade_roll_cost = 0, -1, None, 0.0, 0.0

        # 3) 执行昨日收盘发出的入场
        if pos == 0 and pending_entry != 0:
            pos = pending_entry
            entry_idx = i
            entry_value = float(row["value"])
            entry_pct = float(row["pct"]) if pd.notna(row["pct"]) else np.nan
            daily_pnl[i] -= entry_leg_cost
            trade_pnl = -entry_leg_cost
            trade_roll_cost = 0.0
            pending_entry = 0

        # 4) 收盘后生成下一交易日的信号（只用截至 i 的数据）
        pct_i, z_i = row["pct"], row["z"]
        if pos == 0:
            if pd.notna(pct_i) and pd.notna(z_i) and not pending_entry:
                trigger = 0
                if pct_i >= variant.entry_high:
                    trigger = -1
                elif pct_i <= variant.entry_low:
                    trigger = 1
                if trigger:
                    entry_signals += 1
                    if variant.filter == "adf_hurst":
                        # 只用截至 i 的窗口；ADF 与 Hurst 都必须通过
                        win = df["value"].iloc[max(0, i + 1 - variant.window): i + 1]
                        adf = st.adf_pvalue(win)
                        h = st.hurst_exponent(win)
                        if not (np.isfinite(adf) and adf < variant.adf_alpha
                                and np.isfinite(h) and h < variant.hurst_threshold):
                            trigger = 0
                            filter_rejected += 1
                if trigger:
                    pending_entry = trigger
        else:
            exit_reason = None
            # 1) MA40 穿越止损（优先标记，便于报告统计）
            if variant.ma_stop and i > entry_idx and ma_stop_triggered(
                    pos, float(row["value"]), float(row["ma"]),
                    float(prev["value"]), float(prev["ma"])):
                exit_reason = "ma40_stop"
            # 2) 分位回到中位
            if exit_reason is None and pd.notna(pct_i):
                if pos == -1 and pct_i <= variant.exit_mid:
                    exit_reason = "pct_exit"
                if pos == 1 and pct_i >= variant.exit_mid:
                    exit_reason = "pct_exit"
            # 3) 固定 Z 止损（本版配置已关闭）
            if exit_reason is None and variant.stop_z is not None and pd.notna(z_i):
                if pos == -1 and z_i >= variant.stop_z:
                    exit_reason = "z_stop"
                if pos == 1 and z_i <= -variant.stop_z:
                    exit_reason = "z_stop"
            # 4) 时间止损
            if exit_reason is None and i - entry_idx >= variant.max_hold_days:
                exit_reason = "max_hold"
            if exit_reason:
                pending_exit_reason = exit_reason

    if pos != 0 and entry_idx >= 0:
        # 期末仍持仓：按最后收盘平仓，计入离场成本
        i = n - 1
        exit_value = float(df.iloc[i]["value"])
        daily_pnl[i] -= exit_leg_cost
        trades.append({
            "spread_id": spread.id, "name": spread.name, "sector": sector,
            "direction": int(pos), "entry_date": df.iloc[entry_idx]["trade_date"],
            "exit_date": df.iloc[i]["trade_date"], "entry_value": entry_value,
            "exit_value": exit_value, "pnl": trade_pnl - exit_leg_cost,
            "cost": entry_leg_cost + exit_leg_cost + trade_roll_cost,
            "roll_cost": trade_roll_cost,
            "hold_days": int(i - entry_idx), "entry_pct": entry_pct,
            "exit_pct": float(df.iloc[i]["pct"]) if pd.notna(df.iloc[i]["pct"]) else np.nan,
            "reason": "eod",
        })

    returns = pd.Series(daily_pnl / margin_capital, index=pd.to_datetime(df["trade_date"]))
    daily = pd.DataFrame({"trade_date": pd.to_datetime(df["trade_date"]), "ret": returns.values})
    trade_df = pd.DataFrame(trades)
    metrics = metrics_from_returns(returns, trade_df)
    metrics["entry_signals"] = entry_signals
    metrics["filter_rejected"] = filter_rejected
    return SpreadResult(spread.id, spread.name, sector, daily, trade_df, metrics)


def metrics_from_returns(returns: pd.Series, trades: pd.DataFrame) -> Dict[str, float]:
    r = pd.Series(returns).dropna().astype(float)
    if len(r) == 0:
        return {"n_days": 0, "cagr": 0.0, "vol": 0.0, "sharpe": 0.0, "mdd": 0.0,
                "calmar": 0.0, "win_rate": np.nan, "n_trades": 0, "avg_hold": np.nan,
                "profit_factor": np.nan, "total_return": 0.0}
    nav = (1 + r).cumprod()
    years = max(len(r) / TRADING_DAYS, 1 / TRADING_DAYS)
    cagr = float(nav.iloc[-1] ** (1 / years) - 1) if nav.iloc[-1] > 0 else -1.0
    vol = float(r.std(ddof=1) * np.sqrt(TRADING_DAYS)) if len(r) > 1 else 0.0
    sharpe = float(r.mean() / r.std(ddof=1) * np.sqrt(TRADING_DAYS)) if len(r) > 1 and r.std(ddof=1) > 0 else 0.0
    dd = nav / nav.cummax() - 1
    mdd = float(dd.min())
    n_trades = int(len(trades))
    win = float((trades["pnl"] > 0).mean()) if n_trades else np.nan
    gains = trades.loc[trades["pnl"] > 0, "pnl"].sum() if n_trades else 0.0
    losses = -trades.loc[trades["pnl"] < 0, "pnl"].sum() if n_trades else 0.0
    pf = float(gains / losses) if losses > 0 else np.nan
    return {
        "n_days": int(len(r)), "cagr": cagr, "vol": vol, "sharpe": sharpe,
        "mdd": mdd, "calmar": float(cagr / abs(mdd)) if mdd < 0 else np.nan,
        "win_rate": win, "n_trades": n_trades,
        "avg_hold": float(trades["hold_days"].mean()) if n_trades else np.nan,
        "profit_factor": pf, "total_return": float(nav.iloc[-1] - 1),
    }


def combine_results(results: Iterable[SpreadResult]) -> pd.DataFrame:
    """按交易日对齐，活跃价差等权（空仓计 0）。"""
    frames = []
    for r in results:
        s = r.daily.set_index("trade_date")["ret"].rename(r.spread_id)
        frames.append(s)
    if not frames:
        return pd.DataFrame()
    panel = pd.concat(frames, axis=1).fillna(0.0)
    return panel


def aggregate(panel: pd.DataFrame, ids: List[str]) -> pd.Series:
    cols = [c for c in ids if c in panel.columns]
    if not cols:
        return pd.Series(0.0, index=panel.index)
    return panel[cols].mean(axis=1)


def load_variants(path) -> Dict[str, Variant]:
    import yaml
    raw = yaml.safe_load(open(path, encoding="utf-8"))
    base = raw["signal"]
    out = {}
    for name, v in raw["variants"].items():
        ma_cfg = base.get("ma_stop") or {}
        fcfg = base.get("filter") or {}
        out[name] = Variant(
            name=name,
            entry_high=float(v.get("entry_high", base["entry_high"])),
            entry_low=float(v.get("entry_low", base["entry_low"])),
            exit_mid=float(v.get("exit_mid", base["exit_mid"])),
            stop_z=None if v.get("stop_z", base["stop_z"]) is None else float(v["stop_z"]),
            max_hold_days=int(base["max_hold_days"]),
            filter=v.get("filter", "none"),
            window=int(v.get("window", base.get("window", 252))),
            min_observations=int(v.get("min_observations", v.get("window", 252))),
            ma_stop=bool(v.get("ma_stop", ma_cfg.get("enabled", False))),
            ma_window=int(ma_cfg.get("window", 40)),
            adf_alpha=float(fcfg.get("adf_alpha", 0.05)),
            hurst_threshold=float(fcfg.get("hurst_threshold", 0.50)),
        )
    return out
