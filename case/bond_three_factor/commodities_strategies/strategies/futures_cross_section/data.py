# -*- coding: utf-8 -*-
"""数据层：只调用 DataHub，不直连 TuShare。

DataHub 公共入口（底层实现 data_hub.core.futures_market）：
- get_futures_daily(start, end)  全市场合约日线截面
- get_futures_basic()            合约基础信息
- get_trade_calendar(start,end)  交易日历

本模块把结果落成策略层约定的文件，后续 panel/factors 仍用原有代码读取。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional, Tuple

import pandas as pd

from .common import DATA, log


def _load_datahub():
    from ...config import load_settings
    from ...Futures import datahub as dh

    settings = load_settings()
    env_root = os.environ.get("FUTURES_DATAHUB_ROOT") or os.environ.get("DATAHUB_ROOT")
    root = Path(env_root).expanduser() if env_root and (Path(env_root).expanduser() / "data_hub").is_dir() else dh.find_datahub_root(settings)
    if root is None:
        raise RuntimeError("未找到 DataHub 仓库；请设置 FUTURES_DATAHUB_ROOT")
    for p in (root, Path(root).parent.parent / "pylibs", Path(root).parent.parent / "py_deps"):
        if Path(p).is_dir() and str(p) not in sys.path:
            sys.path.insert(0, str(p))
    import data_hub
    return data_hub, root


def sync(start: str = "20090101", end: Optional[str] = None,
         refresh: bool = False) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """从 DataHub 拉取（或读缓存）并写入策略层数据文件。"""
    data_hub, root = _load_datahub()
    from data_hub import (get_futures_daily, get_futures_basic,
                         get_trade_calendar)

    end = end or pd.Timestamp.today().strftime("%Y%m%d")
    log(f"DataHub: {root}")
    daily = get_futures_daily(start, end, force=refresh)
    basic = get_futures_basic(force=refresh)
    cal = get_trade_calendar(start, end)

    daily = daily.copy()
    daily["trade_date"] = daily["trade_date"].astype(str)
    daily.to_parquet(DATA / "fut_daily_all.parquet", index=False)
    basic.to_csv(DATA / "fut_basic.csv", index=False)
    cal[["cal_date"]].to_csv(DATA / "trade_dates.csv", index=False)

    log(f"sync daily={len(daily)} ({daily['trade_date'].min()}~{daily['trade_date'].max()}) "
        f"basic={len(basic)} cal={len(cal)}")
    return daily, basic, cal
