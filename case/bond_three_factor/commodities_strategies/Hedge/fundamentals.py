# -*- coding: utf-8 -*-
"""基本面佐证：基差分位与仓单变化。

对应截图里的两类驱动：
- 基差：现货相对期货的强弱，基差分位高=现货紧、期货相对便宜；
- 仓单：近 5 日仓单变化，大幅去库=现货紧，大幅累库=现货松。

规则不是"基本面单独决定方向"，而是当价差统计信号与现货结构同时相反时否决交易，
即截图中的"仓单和基差同时反对 → 基本面冲突"。
"""
from __future__ import annotations

import sys
import warnings
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd

from ..config import ROOT, Settings
from ..storage.sqlite_store import SQLiteStore
from . import stats as st

BASIS_EXTREME_HIGH = 0.90
BASIS_EXTREME_LOW = 0.10
WAREHOUSE_CHG_PCT = 5.0   # 5 日仓单变化超过 ±5% 视为方向性驱动
FUND_WINDOW = 252


def _ensure_paths() -> None:
    for p in (ROOT.parent.parent / "py_deps", ROOT.parent.parent / "pylibs", ROOT.parent.parent / ".venv_pkgs"):
        if p.is_dir() and str(p) not in sys.path:
            sys.path.insert(0, str(p))


def fetch_basis(code: str, days: int = 500) -> pd.DataFrame:
    """取品种现货基差（主力基差率），按 120 天分段规避接口区间限制。"""
    _ensure_paths()
    try:
        import akshare as ak
    except Exception:
        return pd.DataFrame()
    end = datetime.now().date()
    start = end - timedelta(days=days)
    frames = []
    cur = start
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        while cur < end:
            nxt = min(cur + timedelta(days=120), end)
            try:
                df = ak.futures_spot_price_daily(start_day=cur.strftime("%Y%m%d"),
                                                 end_day=nxt.strftime("%Y%m%d"),
                                                 vars_list=[code])
                if df is not None and len(df):
                    frames.append(df)
            except Exception:
                pass
            cur = nxt + timedelta(days=1)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["trade_date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d", errors="coerce")
    # 统一为“现货溢价”口径：基差率 = (现货-期货)/现货 = -dom_basis_rate
    df["basis_rate"] = -pd.to_numeric(df["dom_basis_rate"], errors="coerce")
    df = df.dropna(subset=["trade_date", "basis_rate"]).sort_values("trade_date")
    return df[["trade_date", "basis_rate"]].drop_duplicates("trade_date", keep="last").reset_index(drop=True)


def fetch_warehouse(code: str, name_cn: str, days: int = 400) -> pd.DataFrame:
    _ensure_paths()
    try:
        import akshare as ak
        df = ak.futures_inventory_em(symbol=name_cn)
    except Exception:
        return pd.DataFrame()
    if df is None or len(df) == 0:
        return pd.DataFrame()
    df = df.rename(columns={"日期": "trade_date", "库存": "warehouse", "增减": "change"})
    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce")
    df["warehouse"] = pd.to_numeric(df["warehouse"], errors="coerce")
    df = df.dropna(subset=["trade_date", "warehouse"]).sort_values("trade_date")
    df = df[df["trade_date"] >= pd.Timestamp(datetime.now().date() - timedelta(days=days))]
    return df[["trade_date", "warehouse"]].reset_index(drop=True)


def build_fundamentals(settings: Settings, store: SQLiteStore, code: str,
                       name_cn: str) -> Optional[Dict[str, object]]:
    """抓取并落库某品种的基差分位与仓单 5 日变化，返回最新一行。"""
    basis = fetch_basis(code)
    wh = fetch_warehouse(code, name_cn)
    if basis.empty and wh.empty:
        store.log("fundamentals", code, "empty")
        return None

    out = basis.copy() if not basis.empty else pd.DataFrame(columns=["trade_date", "basis_rate"])
    if not out.empty:
        # 用最近 252 个观测的分位，而非全历史
        roll = out["basis_rate"].rolling(FUND_WINDOW, min_periods=60)
        out["basis_pct"] = roll.apply(lambda w: st.percentile_rank(w, w.iloc[-1]), raw=False)

    if not wh.empty:
        wh = wh.copy()
        wh["warehouse_chg_5d"] = wh["warehouse"].pct_change(5) * 100
        out = out.merge(wh, on="trade_date", how="outer") if not out.empty else wh
    else:
        out["warehouse"] = None
        out["warehouse_chg_5d"] = None

    out = out.sort_values("trade_date").tail(300)
    rows = [{
        "trade_date": r["trade_date"].strftime("%Y-%m-%d"),
        "basis_rate": None if pd.isna(r.get("basis_rate")) else float(r["basis_rate"]),
        "basis_pct": None if pd.isna(r.get("basis_pct")) else float(r["basis_pct"]),
        "warehouse": None if pd.isna(r.get("warehouse")) else float(r["warehouse"]),
        "warehouse_chg_5d": None if pd.isna(r.get("warehouse_chg_5d")) else float(r["warehouse_chg_5d"]),
    } for _, r in out.iterrows()]
    store.save_fundamentals(code, rows, source="akshare")
    store.log("fundamentals", code, "ok", len(rows), "akshare")
    latest = out.dropna(subset=["trade_date"]).iloc[-1].to_dict()
    return latest


def load_latest_fundamentals(store: SQLiteStore, code: str) -> Optional[Dict[str, object]]:
    df = store.load_fundamentals(code)
    if df.empty:
        return None
    return df.iloc[-1].to_dict()


def detect_conflict(direction: str, fund: Optional[Dict[str, object]],
                    require_both: bool = True, basis_high: float = BASIS_EXTREME_HIGH,
                    basis_low: float = BASIS_EXTREME_LOW,
                    wh_pct: float = WAREHOUSE_CHG_PCT) -> Tuple[bool, List[str]]:
    """判断基本面是否与统计信号相反。

    direction:
      SHORT_SPREAD —— 价差处于高分位，统计上做空价差（空 A 多 B）
      LONG_SPREAD  —— 价差处于低分位，统计上做多价差（多 A 空 B）
    """
    flags: List[str] = []
    if not fund:
        return False, flags
    bp = fund.get("basis_pct")
    wc = fund.get("warehouse_chg_5d")
    bp = None if bp is None or pd.isna(bp) else float(bp)
    wc = None if wc is None or pd.isna(wc) else float(wc)

    basis_conflict = False
    wh_conflict = False
    if direction == "SHORT_SPREAD":
        if bp is not None and bp >= basis_high:
            basis_conflict = True
            flags.append(f"基差分位 {bp:.0%} 处于高位（现货偏紧，反对做空价差）")
        if wc is not None and wc <= -wh_pct:
            wh_conflict = True
            flags.append(f"仓单 5 日 {wc:+.1f}%（去库，现货偏紧，反对做空价差）")
    elif direction == "LONG_SPREAD":
        if bp is not None and bp <= basis_low:
            basis_conflict = True
            flags.append(f"基差分位 {bp:.0%} 处于低位（现货偏松，反对做多价差）")
        if wc is not None and wc >= wh_pct:
            wh_conflict = True
            flags.append(f"仓单 5 日 {wc:+.1f}%（累库，现货偏松，反对做多价差）")

    veto = (basis_conflict and wh_conflict) if require_both else (basis_conflict or wh_conflict)
    return veto, flags

def load_fundamentals_at(store: SQLiteStore, code: str, asof: str) -> Optional[Dict[str, object]]:
    """取 asof 当日或之前最近一条基本面，避免历史回看时误用最新值。"""
    df = store.load_fundamentals(code)
    if df.empty:
        return None
    df = df[df["trade_date"] <= str(asof)]
    if df.empty:
        return None
    return df.iloc[-1].to_dict()
