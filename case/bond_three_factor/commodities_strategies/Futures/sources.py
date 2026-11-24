# -*- coding: utf-8 -*-
"""原生行情源兜底：AKShare（新浪/同花顺口径）> TuShare > 新浪 Web JSONP。

DataHub 可用时不会走到这里；保留该层是为了在没有 DataHub 的机器上也能独立运行。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

import pandas as pd

from ..config import ROOT, Settings
from . import datahub

_EXCHANGE_SUFFIX = {
    "czce": "ZCE", "dce": "DCE", "shfe": "SHF", "ine": "INE",
    "cffex": "CFX", "gfex": "GFE",
}
_SINA_URL = ("https://stock2.finance.sina.com.cn/futures/api/jsonp.php/"
             "var%20_%20=/InnerFuturesNewService.getDailyKLine?symbol={sym}")


def _ensure_paths() -> None:
    for p in (ROOT.parent.parent / "py_deps", ROOT.parent.parent / "pylibs", ROOT.parent.parent / ".venv_pkgs"):
        if p.is_dir() and str(p) not in sys.path:
            sys.path.insert(0, str(p))


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy()
    if "date" in work.columns:
        work = work.rename(columns={"date": "trade_date"})
    if "hold" in work.columns:
        work = work.rename(columns={"hold": "oi"})
    if "vol" in work.columns and "volume" not in work.columns:
        work = work.rename(columns={"vol": "volume"})
    work["trade_date"] = pd.to_datetime(work["trade_date"], errors="coerce")
    for col in ("open", "high", "low", "close", "settle", "volume", "oi"):
        if col not in work.columns:
            work[col] = None
        work[col] = pd.to_numeric(work[col], errors="coerce")
    work = work.dropna(subset=["trade_date", "close"]).sort_values("trade_date")
    cols = ["trade_date", "open", "high", "low", "close", "settle", "volume", "oi"]
    return work[cols].drop_duplicates("trade_date", keep="last").reset_index(drop=True)


def fetch_akshare(contract: str, start: str, end: str) -> Optional[pd.DataFrame]:
    _ensure_paths()
    try:
        import akshare as ak
        df = ak.futures_zh_daily_sina(symbol=contract)
    except Exception:
        return None
    if df is None or len(df) == 0:
        return None
    df = _normalize(df)
    return _slice(df, start, end)


def fetch_tushare(settings: Settings, contract: str, exchange: str,
                  start: str, end: str) -> Optional[pd.DataFrame]:
    _ensure_paths()
    token = os.environ.get(settings.data.tushare_token_env, "").strip()
    if not token:
        for p in (ROOT.parent / "pylibs" / "tushare_config.py",
                  ROOT.parent / "py_deps" / "tushare_config.py"):
            if p.is_file():
                try:
                    text = p.read_text(encoding="utf-8", errors="ignore")
                    m = re.search(r"TOKEN\s*=\s*['\"]([0-9a-f]{20,})['\"]", text, re.I)
                    if m:
                        token = m.group(1)
                        break
                except OSError:
                    pass
    if not token:
        return None
    try:
        import tushare as ts
        pro = ts.pro_api(token)
        suffix = _EXCHANGE_SUFFIX.get(exchange.lower(), "SHF")
        df = pro.fut_daily(ts_code=f"{contract}.{suffix}", start_date=start, end_date=end)
    except Exception:
        return None
    if df is None or len(df) == 0:
        return None
    return _slice(_normalize(df), start, end)


def fetch_sina_web(contract: str, start: str, end: str) -> Optional[pd.DataFrame]:
    import requests
    url = _SINA_URL.format(sym=contract)
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
    try:
        resp = requests.get(url, timeout=20, headers=headers)
        text = resp.text
        # 返回形如 /*<script>...*/ var _ =([{...}]);  ，直接截取首尾中括号最稳
        lo, hi = text.find("["), text.rfind("]")
        if lo < 0 or hi <= lo:
            return None
        payload = json.loads(text[lo:hi + 1])
        if not payload:
            return None
        df = pd.DataFrame(payload)
        rename = {"d": "trade_date", "o": "open", "h": "high", "l": "low",
                  "c": "close", "v": "volume", "p": "oi", "s": "settle"}
        df = df.rename(columns=rename)
    except Exception:
        return None
    return _slice(_normalize(df), start, end)


def _slice(df: pd.DataFrame, start: str, end: str) -> Optional[pd.DataFrame]:
    if df is None or df.empty:
        return None
    s = pd.to_datetime(start) if start else None
    e = pd.to_datetime(end) if end else None
    out = df
    if s is not None:
        out = out[out["trade_date"] >= s]
    if e is not None:
        out = out[out["trade_date"] <= e]
    return out.reset_index(drop=True) if len(out) else None


def fetch_any(settings: Settings, product: str, contract: str, exchange: str,
              start: str, end: str) -> Tuple[Optional[pd.DataFrame], str]:
    """按 settings.data.source_priority 逐级取数，返回 (df, 实际来源)。"""
    reasons = []
    for src in settings.data.source_priority:
        df = None
        if src == "datahub":
            df, info = datahub.fetch_contract(settings, contract, start, end)
            if df is None:
                reasons.append(info)
            else:
                # 保留 DataHub 内部实际命中的层级（如 datahub:akshare）
                return df, info
        elif src == "akshare":
            df = fetch_akshare(contract, start, end)
        elif src == "tushare":
            df = fetch_tushare(settings, contract, exchange, start, end)
        elif src == "sina_web":
            df = fetch_sina_web(contract, start, end)
        if df is not None and len(df):
            return df, src
        time.sleep(max(0.0, settings.data.request_sleep))
    return None, "; ".join(reasons) or "all_sources_failed"