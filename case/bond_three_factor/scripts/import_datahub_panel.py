#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 DataHub 全市场期货日线导入 commodities_strategies 的 contract_daily（一次性/可重跑）。

用途：把策略需要的品种历史拉长到 2018 年起，避免逐合约回源。
数据来自 data_hub.futures_market.get_futures_daily（DataHub 缓存/增量）。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from commodities_strategies.config import load_settings  # noqa: E402
from commodities_strategies.Futures import datahub as dh  # noqa: E402
from commodities_strategies.storage.sqlite_store import SQLiteStore  # noqa: E402

DEFAULT_PRODUCTS = ["RB", "I", "J", "JM", "HC", "M", "RM", "Y", "P"]


def main() -> int:
    parser = argparse.ArgumentParser(description="DataHub 面板 → contract_daily")
    parser.add_argument("--start", default="20180101")
    parser.add_argument("--end", default="20261112")
    parser.add_argument("--products", nargs="*", default=DEFAULT_PRODUCTS)
    parser.add_argument("--refetch", action="store_true")
    args = parser.parse_args()

    settings = load_settings()
    root = dh.find_datahub_root(settings)
    if root is None:
        raise SystemExit("未找到 DataHub")
    for p in (root, root.parent.parent / "pylibs", root.parent.parent / "py_deps"):
        if p.is_dir() and str(p) not in sys.path:
            sys.path.insert(0, str(p))
    from data_hub.futures_market import get_futures_daily

    df = get_futures_daily(args.start, args.end, force=args.refetch)
    mask = df["ts_code"].astype(str).str.match(r"^[A-Za-z]{1,3}\d{4}\.")
    df = df[mask].copy()
    df["prod"] = df["ts_code"].str.extract(r"^([A-Za-z]+)")[0].str.upper()
    df = df[df["prod"].isin(args.products)]
    df = df.rename(columns={"vol": "volume"})

    store = SQLiteStore(settings.data.db_path)
    total = 0
    for prod, g in df.groupby("prod"):
        for contract, cg in g.groupby("ts_code"):
            code = str(contract).split(".")[0]
            n = store.upsert_contract_daily(cg, product=prod, contract=code,
                                            source="datahub:futures_market")
            total += n
        print(f"{prod}: {len(g):,} 行 / {g['ts_code'].nunique()} 合约")
    print(f"imported {total:,} rows -> {settings.data.db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
