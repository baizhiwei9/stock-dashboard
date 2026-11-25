# -*- coding: utf-8 -*-
"""扫描引擎：构建价差 → 计算统计画像 → 生成信号 → 落 SQLite。"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List, Optional, Sequence

import pandas as pd

from ..config import Settings, SpreadDef, load_products, load_spreads
from ..Futures import catalog
from .spreads import build_spread_series
from ..storage.sqlite_store import SQLiteStore
from . import fundamentals as fund_mod
from . import signals as sig_mod
from . import stats as st

log = logging.getLogger(__name__)


def _asof_value(df: pd.DataFrame, asof: Optional[str]) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    if asof:
        df = df[df["trade_date"] <= pd.to_datetime(asof)]
    return df.sort_values("trade_date").reset_index(drop=True)


def scan_spread(settings: Settings, store: SQLiteStore, spread: SpreadDef,
                asof: Optional[str] = None, force: bool = False,
                with_fundamentals: bool = True) -> Dict[str, object]:
    df = build_spread_series(settings, store, spread, end=asof, force=force)
    df = _asof_value(df, asof)
    if df.empty:
        raise RuntimeError(f"{spread.id} 价差序列为空")
    asof_date = df["trade_date"].iloc[-1].strftime("%Y-%m-%d")
    cfg = settings.hedge
    stats = st.compute_stats(df["value"], window=cfg.percentile_window,
                             min_observations=cfg.min_observations,
                             vol_window=cfg.vol_window, adf_alpha=cfg.adf_alpha,
                             hurst_threshold=cfg.hurst_threshold,
                             vol_extreme=cfg.vol_extreme)
    store.save_stats(spread.id, asof_date, stats)

    fund = None
    if with_fundamentals and cfg.fundamental_filter:
        code = spread.leg_a.product if spread.type == "cross" and spread.leg_a else spread.product
        if code:
            prod = catalog.product(code)
            try:
                fund = fund_mod.build_fundamentals(settings, store, code, prod.inventory_name or prod.name_cn)
            except Exception as exc:
                store.log("fundamentals", code, "error", 0, "", str(exc))
                fund = fund_mod.load_latest_fundamentals(store, code)
        # 历史扫描必须用 asof 当日或之前的基本面，不能用最新值
        if code:
            fund = fund_mod.load_fundamentals_at(store, code, asof_date) or fund

    decision = sig_mod.evaluate(spread.id, asof_date, stats, cfg, fund=fund)
    store.save_signal(spread.id, asof_date, decision)
    store.log("scan", spread.id, "ok", 1, decision.action)
    return {"spread": spread, "asof": asof_date, "stats": stats, "decision": decision, "fund": fund}


def scan_all(settings: Settings, store: SQLiteStore, spread_ids: Optional[Sequence[str]] = None,
             asof: Optional[str] = None, force: bool = False,
             with_fundamentals: bool = True) -> List[Dict[str, object]]:
    spreads = load_spreads()
    if spread_ids:
        wanted = set(spread_ids)
        spreads = [s for s in spreads if s.id in wanted]
    results: List[Dict[str, object]] = []
    for spread in spreads:
        try:
            results.append(scan_spread(settings, store, spread, asof=asof, force=force,
                                       with_fundamentals=with_fundamentals))
        except Exception as exc:
            log.exception("scan %s failed", spread.id)
            store.log("scan", spread.id, "error", 0, "", str(exc))
            results.append({"spread": spread, "error": str(exc)})
    return results


def print_report(results: List[Dict[str, object]]) -> None:
    header = (f"{'价差':<22}{'日期':<12}{'分位':>8}{'Z':>7}{'ADF':>8}"
              f"{'Hurst':>7}{'波动分位':>9}  {'动作':<20}")
    print(header)
    print("-" * 90)
    for r in results:
        if "error" in r:
            print(f"{r['spread'].name:<22}ERROR: {r['error']}")
            continue
        s, d = r["stats"], r["decision"]
        print(f"{r['spread'].name:<22}{r['asof']:<12}"
              f"{_num(s.get('pct'), pct=True):>8}{_num(s.get('z')):>7}"
              f"{_num(s.get('adf_p'), 4):>8}{_num(s.get('hurst'), 2):>7}"
              f"{_num(s.get('vol_pct'), pct=True):>9}  {d.action:<20}")
        print(f"  原因: {'；'.join(d.reasons)}")


def _num(v, digits: int = 2, pct: bool = False) -> str:
    try:
        if v is None or pd.isna(v):
            return "-"
        return f"{float(v):.{digits}{'%' if pct else 'f'}}"
    except (TypeError, ValueError):
        return "-"