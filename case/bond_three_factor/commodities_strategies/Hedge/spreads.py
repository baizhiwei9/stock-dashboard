# -*- coding: utf-8 -*-
"""价差构建（Hedge 子模块）。

把价差计算放在对冲套利层，而不是数据层：
- seasonal：同品种跨年度季节性跨期，按最近可交易年份拼接；
- calendar：两个指定合约；
- cross：两个品种主力连续或指定合约。

数据获取与连续序列由 Futures 层负责，本模块只消费已落库的合约/连续行情。
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import List, Optional

import numpy as np
import pandas as pd

from ..config import Settings, SpreadDef
from ..Futures import catalog
from ..Futures.loader import build_continuous, load_contract
from ..storage.sqlite_store import SQLiteStore


def _pair_frame(store: SQLiteStore, contract_a: str, contract_b: str,
                calc: str, start: str, end: str) -> pd.DataFrame:
    a = store.load_contract_daily(contract_a, start, end)
    b = store.load_contract_daily(contract_b, start, end)
    if a.empty or b.empty:
        return pd.DataFrame()
    a = a[["trade_date", "close"]].rename(columns={"close": "leg_a_price"})
    b = b[["trade_date", "close"]].rename(columns={"close": "leg_b_price"})
    m = a.merge(b, on="trade_date", how="inner").dropna()
    if m.empty:
        return m
    m["value"] = m["leg_a_price"] - m["leg_b_price"] if calc == "spread" else m["leg_a_price"] / m["leg_b_price"]
    m["leg_a"] = contract_a
    m["leg_b"] = contract_b
    m["calc"] = calc
    return m


def build_seasonal(settings: Settings, store: SQLiteStore, spread: SpreadDef,
                   start: str, end: str, force: bool = False) -> pd.DataFrame:
    """季节性跨期：逐年取同月份合约对，按“最近可交易年份”优先拼接。"""
    parts: List[pd.DataFrame] = []
    for year in spread.years:
        ca = catalog.contract_code(spread.product, year, spread.months[0])
        cb = catalog.contract_code(spread.product, year, spread.months[1])
        load_contract(settings, store, spread.product, ca, start, end, force)
        load_contract(settings, store, spread.product, cb, start, end, force)
        df = _pair_frame(store, ca, cb, spread.calc, start, end)
        if not df.empty:
            df["year"] = year
            parts.append(df)
    if not parts:
        raise RuntimeError(f"{spread.id} 无可用合约对")
    merged = pd.concat(parts, ignore_index=True).sort_values(["trade_date", "year"])
    merged = merged.drop_duplicates("trade_date", keep="first").sort_values("trade_date").reset_index(drop=True)
    merged["roll_flag"] = (merged["year"] != merged["year"].shift(1)).astype(int)
    merged.loc[0, "roll_flag"] = 0
    store.upsert_spread_daily(spread.id, merged)
    store.log("build_spread", spread.id, "ok", len(merged), "sqlite")
    return merged


def build_calendar_roll(settings: Settings, store: SQLiteStore, spread: SpreadDef,
                       start: str, end: str, force: bool = False) -> pd.DataFrame:
    """逐月平移的固定期限差（如 1-6 → 2-7 → 3-8）。

    每个交易日选“最近未到期且仍有持仓/成交的近月”，远月 = 近月 + gap_months；
    近月到期后自动切到下一月，保持固定月份间隔，直到价差回归。
    """
    if not spread.product or spread.gap_months <= 0:
        raise ValueError(f"{spread.id} calendar_roll 需要 product 与 gap_months")
    panel = store.load_contract_panel(spread.product, start, end)
    if panel.empty:
        raise RuntimeError(f"{spread.id} 无合约面板数据")

    def _ym(contract: str):
        m = re.match(r"^[A-Za-z]+(\d{2})(\d{2})$", str(contract).upper())
        return (2000 + int(m.group(1))) * 12 + int(m.group(2)) if m else None

    panel = panel.dropna(subset=["close"]).copy()
    panel["ym"] = panel["contract"].map(_ym)
    panel = panel[panel["ym"].notna()]
    close = panel.pivot_table(index="trade_date", columns="contract", values="close", aggfunc="last")
    oi = panel.pivot_table(index="trade_date", columns="contract", values="oi", aggfunc="last")
    vol = panel.pivot_table(index="trade_date", columns="contract", values="volume", aggfunc="last")
    by_ym = {int(ym): c for c, ym in panel.drop_duplicates("contract")[["contract", "ym"]].itertuples(index=False)}
    contracts = list(close.columns)
    cym = {c: int(panel.loc[panel.contract == c, "ym"].iloc[0]) for c in contracts}

    capital = settings.backtest.target_notional
    rate = settings.backtest.commission_turnover + settings.backtest.slippage_turnover
    roll_cost = 4 * capital * rate      # 近月+远月，各平/开一次

    rows = []
    prev_front = None
    for dt in close.index:
        cur = dt.year * 12 + dt.month
        cands = [c for c in contracts
                 if cym[c] >= cur and pd.notna(close.at[dt, c])]
        if not cands:
            continue
        cands.sort(key=lambda c: cym[c])
        front = None
        for c in cands:
            if (pd.notna(oi.at[dt, c]) and oi.at[dt, c] > 0) or (pd.notna(vol.at[dt, c]) and vol.at[dt, c] > 0):
                front = c
                break
        if front is None:
            front = cands[0]
        # 目标远月可能不存在（如豆粕/豆油没有 6 月合约），取不早于目标月份的最近挂牌月
        back_cands = [c for c in contracts
                      if cym[c] >= cym[front] + spread.gap_months and pd.notna(close.at[dt, c])]
        if not back_cands:
            continue
        back = min(back_cands, key=lambda c: cym[c])
        fp, bp = float(close.at[dt, front]), float(close.at[dt, back])
        val = fp - bp if spread.calc == "spread" else fp / bp
        roll = 1 if prev_front is not None and front != prev_front else 0
        rows.append({"trade_date": dt, "leg_a": front, "leg_b": back,
                     "leg_a_price": fp, "leg_b_price": bp, "value": val,
                     "calc": spread.calc, "roll_flag": roll,
                     "roll_cost": roll_cost if roll else 0.0})
        prev_front = front
    out = pd.DataFrame(rows)
    if out.empty:
        raise RuntimeError(f"{spread.id} 滚动期限差为空")
    store.upsert_spread_daily(spread.id, out)
    store.log("build_spread", spread.id, "ok", len(out), "sqlite")
    return out


def build_calendar(settings: Settings, store: SQLiteStore, spread: SpreadDef,
                   start: str, end: str, force: bool = False) -> pd.DataFrame:
    if len(spread.contracts) != 2:
        raise ValueError(f"{spread.id} calendar 类型需要两个合约")
    ca, cb = spread.contracts
    load_contract(settings, store, ca.rstrip("0123456789"), ca, start, end, force)
    load_contract(settings, store, cb.rstrip("0123456789"), cb, start, end, force)
    df = _pair_frame(store, ca, cb, spread.calc, start, end)
    if df.empty:
        raise RuntimeError(f"{spread.id} 两腿无重叠数据")
    df["roll_flag"] = 0
    store.upsert_spread_daily(spread.id, df)
    store.log("build_spread", spread.id, "ok", len(df), "sqlite")
    return df


def build_cross(settings: Settings, store: SQLiteStore, spread: SpreadDef,
                start: str, end: str, force: bool = False) -> pd.DataFrame:
    def leg_frame(leg) -> pd.DataFrame:
        if leg is None:
            raise ValueError(f"{spread.id} 缺少 leg 定义")
        if leg.mode == "contract" and leg.contract:
            df = load_contract(settings, store, leg.product, leg.contract, start, end, force)
            if df is None:
                raise RuntimeError(f"{spread.id} 腿 {leg.contract} 无数据")
            out = df[["trade_date", "close"]].rename(columns={"close": "price"})
            out["roll_flag"] = 0
            return out
        df = build_continuous(settings, store, leg.product, start, end, force)
        return df[["trade_date", "adj_close", "roll_flag"]].rename(columns={"adj_close": "price"})

    a = leg_frame(spread.leg_a)
    b = leg_frame(spread.leg_b)
    m = a.merge(b, on="trade_date", how="inner", suffixes=("_a", "_b")).dropna(subset=["price_a", "price_b"])
    if m.empty:
        raise RuntimeError(f"{spread.id} 两腿无重叠数据")
    a_scaled = m["price_a"] * float(spread.scale_a)
    b_scaled = m["price_b"] * float(spread.scale_b)
    m["value"] = a_scaled - b_scaled if spread.calc == "spread" else a_scaled / b_scaled
    m["leg_a"] = spread.leg_a.contract if spread.leg_a.mode == "contract" else spread.leg_a.product
    m["leg_b"] = spread.leg_b.contract if spread.leg_b.mode == "contract" else spread.leg_b.product
    m["leg_a_price"] = m["price_a"]
    m["leg_b_price"] = m["price_b"]
    m["calc"] = spread.calc
    m["roll_flag"] = ((m.get("roll_flag_a", 0) == 1) | (m.get("roll_flag_b", 0) == 1)).astype(int)
    out = m[["trade_date", "leg_a", "leg_b", "leg_a_price", "leg_b_price", "value", "calc", "roll_flag"]]
    store.upsert_spread_daily(spread.id, out)
    store.log("build_spread", spread.id, "ok", len(out), "sqlite")
    return out


def build_spread_series(settings: Settings, store: SQLiteStore, spread: SpreadDef,
                        start: Optional[str] = None, end: Optional[str] = None,
                        force: bool = False) -> pd.DataFrame:
    end = end or datetime.now().strftime("%Y-%m-%d")
    start = start or (pd.to_datetime(end) - pd.Timedelta(days=settings.data.lookback_days)).strftime("%Y-%m-%d")
    store.upsert_spread_def(spread)
    if spread.type == "seasonal":
        return build_seasonal(settings, store, spread, start, end, force)
    if spread.type == "calendar":
        return build_calendar(settings, store, spread, start, end, force)
    if spread.type == "calendar_roll":
        return build_calendar_roll(settings, store, spread, start, end, force)
    if spread.type == "cross":
        return build_cross(settings, store, spread, start, end, force)
    raise ValueError(f"未知价差类型: {spread.type}")
