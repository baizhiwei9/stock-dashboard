# -*- coding: utf-8 -*-
"""行情装载与主力连续构建。

- 合约日线：优先读 SQLite，缺失区间才回源（DataHub → AKShare → TuShare → Web）；
- 主力连续：按持仓量最大原则逐日选主力，换月用价差法后复权，避免换月跳空污染分位数。

价差本身的构建在 Hedge/spreads.py，数据层只负责提供干净的合约与连续行情。
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from ..config import Settings
from ..storage.sqlite_store import SQLiteStore
from . import catalog, sources

log = logging.getLogger(__name__)


def _fmt(d) -> str:
    return pd.to_datetime(d).strftime("%Y%m%d")


def _is_expired(contract: str) -> bool:
    try:
        info = catalog.parse_contract(contract)
    except ValueError:
        return False
    today = date.today()
    return (int(info["year"]), int(info["month"])) < (today.year, today.month)


def load_contract(settings: Settings, store: SQLiteStore, code: str, contract: str,
                  start: str, end: str, force: bool = False) -> Optional[pd.DataFrame]:
    """取单个合约日线，优先命中 SQLite 缓存。"""
    p = catalog.product(code)
    start_d, end_d = pd.to_datetime(start), pd.to_datetime(end)
    cached = store.load_contract_daily(contract)
    if not force and len(cached) > 0:
        cmin, cmax = cached["trade_date"].min(), cached["trade_date"].max()
        fresh_enough = cmax >= min(end_d, pd.Timestamp.today().normalize()) - pd.Timedelta(days=7)
        # 已到期合约的缓存即为全生命周期数据；未到期合约要求末端足够新
        if len(cached) > 20 and (_is_expired(contract) or fresh_enough):
            return cached

    if not force and store.recent_empty(contract):
        return cached if len(cached) else None

    df, src = sources.fetch_any(settings, code, contract, p.exchange, _fmt(start), _fmt(end))
    if df is None or df.empty:
        store.log("fetch_contract", contract, "empty", 0, src)
        return cached if len(cached) else None
    n = store.upsert_contract_daily(df, product=code, contract=contract, source=src)
    store.log("fetch_contract", contract, "ok", n, src)
    return store.load_contract_daily(contract, start=None, end=None)


def build_continuous(settings: Settings, store: SQLiteStore, code: str,
                     start: str, end: str, force: bool = False) -> pd.DataFrame:
    """构建主力连续后复权序列（按 OI 最大选主力）。"""
    cached = store.load_continuous(code, start, end)
    if not force and len(cached) > 120:
        return cached

    p = catalog.product(code)
    start_d, end_d = pd.to_datetime(start), pd.to_datetime(end)
    years = list(range(start_d.year - 1, end_d.year + 2))
    contracts = catalog.contracts_for_years(code, years, catalog.continuous_months(code))

    frames: Dict[str, pd.DataFrame] = {}
    for contract in contracts:
        df = load_contract(settings, store, code, contract, start, end)
        if df is not None and len(df) > 5:
            frames[contract] = df
    if not frames:
        raise RuntimeError(f"{code} 无任何合约数据，无法构建主力连续")

    oi = pd.DataFrame({c: d.set_index("trade_date")["oi"] for c, d in frames.items()})
    close = pd.DataFrame({c: d.set_index("trade_date")["close"] for c, d in frames.items()})
    oi = oi.sort_index()
    close = close.reindex(oi.index)
    oi = oi.where(oi > 0)
    oi = oi[oi.notna().any(axis=1)]
    dominant = oi.idxmax(axis=1, skipna=True)
    dominant = dominant[oi.max(axis=1).fillna(0) > 0]

    out_rows: List[dict] = []
    offset = 0.0
    prev_contract = None
    prev_dt = None
    for dt, contract in dominant.items():
        if dt < start_d or dt > end_d:
            continue
        px = close.at[dt, contract]
        if pd.isna(px):
            continue
        roll = 0
        if prev_contract is not None and contract != prev_contract:
            # 用换月前一日两合约收盘价计算价差，保证换月当天收益连续
            old_px = close.at[prev_dt, prev_contract] if prev_dt is not None and prev_contract in close.columns else np.nan
            new_px = close.at[prev_dt, contract] if prev_dt is not None else np.nan
            if pd.notna(old_px) and pd.notna(new_px):
                offset += float(old_px) - float(new_px)
                roll = 1
            else:
                old_px = close.at[dt, prev_contract] if prev_contract in close.columns else np.nan
                if pd.notna(old_px):
                    offset += float(old_px) - float(px)
                    roll = 1
        prev_contract = contract
        prev_dt = dt
        out_rows.append({
            "trade_date": dt,
            "close": float(px),
            "adj_close": float(px) + offset,
            "dominant": contract,
            "oi": float(oi.at[dt, contract]),
            "roll_flag": roll,
        })
    out = pd.DataFrame(out_rows)
    if out.empty:
        raise RuntimeError(f"{code} 主力连续为空")
    store.upsert_continuous(code, out)
    store.log("build_continuous", code, "ok", len(out), "sqlite")
    return out
