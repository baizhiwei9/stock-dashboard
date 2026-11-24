# -*- coding: utf-8 -*-
"""周线价格：外盘 AKShare 外盘日线，国内优先 DataHub，失败回退新浪主力连续。

⚠️ 已知例外（README 也写了）：COMEX / CBOT 的价格 **DataHub 没有**，
   所以外盘只能走 `akshare.futures_foreign_hist`，这是本仓库数据层「优先 DataHub」
   原则的唯一例外，其余国内腿一律先 DataHub。

时间对齐
--------
CFTC 周 W（周二持仓）在周五 15:30 ET 才发布，因此：
  · 外盘默认用**周五收盘**（沿用文献惯例，需注意结算价 13:30 ET 早于发布，
    所以本口径存在「2 小时前视」，敏感性见 README）；
  · 国内默认用**下周一收盘**（严格发布后成交），索引重贴回信号周周五。
"""
from __future__ import annotations

import warnings
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import pandas as pd

from ...config import Settings
from .cot import REPO_CACHE as COT_CACHE
from .universe import Commodity

warnings.filterwarnings("ignore", category=FutureWarning)

_CACHE: dict = {}


def _daily_to_weekly(s: pd.Series, anchor: str = "W-FRI") -> pd.Series:
    s = pd.Series(s).copy()
    s.index = pd.to_datetime(s.index)
    s = pd.to_numeric(s, errors="coerce").dropna()
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s.resample(anchor).last().dropna()


def _to_datetime_index(df: pd.DataFrame, col: str) -> pd.Series:
    d = df.copy()
    d[col] = pd.to_datetime(d[col])
    d["close"] = pd.to_numeric(d["close"], errors="coerce")
    d = d.dropna(subset=["close"])
    d = d[d["close"] > 0]
    return d.set_index(col)["close"].sort_index()


def foreign_weekly(commodity: Commodity, align: str = "fri", use_cache: bool = True,
                   offline: bool = False) -> pd.Series:
    """外盘周线收盘（COMEX/CBOT），align=fri|mon。

    mon 口径：取下一周周一收盘（发布后可成交），索引减 3 天贴回信号周周五。
    `offline=True` 只读 `data/cache/cftc_cot/px_*.parquet`（由上一次在线跑写入）。
    """
    ck = ("foreign", commodity.key, align)
    if use_cache and ck in _CACHE:
        return _CACHE[ck].copy()

    disk = COT_CACHE / f"px_{commodity.key}_{align}.parquet"
    if offline:
        if not disk.exists():
            raise RuntimeError(f"离线模式且无外盘价格缓存 {disk}")
        s = pd.read_parquet(disk)["close"]
        s.index = pd.to_datetime(s.index)
        _CACHE[ck] = s
        return s.copy()

    import akshare as ak

    df = ak.futures_foreign_hist(symbol=commodity.akshare_symbol)
    if df is None or df.empty:
        raise RuntimeError(f"{commodity.name_cn} 外盘价格为空（akshare.futures_foreign_hist={commodity.akshare_symbol}）")
    df = df.rename(columns={"日期": "date"})
    close = _to_datetime_index(df, "date")

    if align == "mon":
        w = _daily_to_weekly(close, "W-MON")
        w.index = w.index - pd.Timedelta(days=3)     # Mon W+1 → 信号周 Fri
    else:
        w = _daily_to_weekly(close, "W-FRI")
    w.name = commodity.key
    try:
        disk.parent.mkdir(parents=True, exist_ok=True)
        w.rename("close").to_frame().to_parquet(disk)
    except Exception:                                    # noqa: BLE001
        pass
    _CACHE[ck] = w
    return w.copy()


def _datahub_domestic(commodity: Commodity, settings: Settings, start: str, end: str) -> Optional[pd.Series]:
    """尝试用仓库 DataHub/loader 构建国内主力连续（按 OI 选主力 + 后复权）。"""
    if not commodity.dom_code:
        return None
    try:
        from ...Futures import datahub, loader
        from ...storage.sqlite_store import SQLiteStore

        if not datahub.available(settings):
            return None
        store = SQLiteStore(settings.data.db_path)
        df = loader.build_continuous(settings, store, commodity.dom_code, start, end)
        if df is None or df.empty:
            return None
        col = "adj_close" if "adj_close" in df.columns else "close"
        s = df.set_index("trade_date")[col]
        return pd.to_numeric(s, errors="coerce").dropna()
    except Exception:                                    # noqa: BLE001
        return None


def _akshare_domestic(commodity: Commodity) -> Optional[pd.Series]:
    """回退：AKShare 新浪主力连续（不复权，跨月跳空存在，但周频够用）。"""
    if not commodity.dom_ak_symbol:
        return None
    import akshare as ak

    for sym in (commodity.dom_ak_symbol, commodity.dom_code):
        if not sym:
            continue
        try:
            df = ak.futures_main_sina(symbol=sym, start_date="20180101", end_date="20301231")
        except Exception:                                # noqa: BLE001
            continue
        if df is None or df.empty:
            continue
        df = df.rename(columns={"日期": "date", "收盘价": "close"})
        if "close" not in df.columns:
            continue
        return _to_datetime_index(df, "date")
    return None


def domestic_weekly(commodity: Commodity, align: str = "mon",
                    settings: Optional[Settings] = None, start: str = "2019-01-01",
                    end: Optional[str] = None, use_cache: bool = True) -> Tuple[Optional[pd.Series], str]:
    """国内周线，返回 (series, source)。source ∈ {datahub, akshare, None}。

    align=mon 时索引减 3 天，贴回信号周周五。
    """
    ck = ("domestic", commodity.key, align)
    if use_cache and ck in _CACHE:
        s, src = _CACHE[ck]
        return s.copy(), src

    end = end or pd.Timestamp.today().strftime("%Y-%m-%d")
    daily: Optional[pd.Series] = None
    src = "none"
    if settings is not None:
        daily = _datahub_domestic(commodity, settings, start, end)
        if daily is not None and len(daily) > 60:
            src = "datahub"
        else:
            daily = None
    if daily is None:
        daily = _akshare_domestic(commodity)
        if daily is not None and len(daily) > 0:
            src = "akshare"

    if daily is None:
        _CACHE[ck] = (None, "none")
        return None, "none"

    w = _daily_to_weekly(daily, "W-MON" if align == "mon" else "W-FRI")
    if align == "mon":
        w.index = w.index - pd.Timedelta(days=3)
    w.name = commodity.key
    _CACHE[ck] = (w, src)
    return w.copy(), src


def clear_cache() -> None:
    _CACHE.clear()
