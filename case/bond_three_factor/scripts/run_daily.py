#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日跑批：构建价差 → 扫描信号 → 输出 CSV。

用法：
    python scripts/run_daily.py --spread MA_05_09 M_RM
    python scripts/run_daily.py --all
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from commodities_strategies.cli import (  # noqa: E402
    cmd_fetch, cmd_scan, build_parser,
)
from commodities_strategies.config import load_settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="commodities_strategies 每日跑批")
    parser.add_argument("--spread", nargs="*", default=[])
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--asof")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-fundamental", action="store_true")
    args = parser.parse_args()
    if not args.spread and not args.all:
        parser.error("请指定 --spread 或 --all")
    # 复用 CLI 的参数结构
    ns = argparse.Namespace(spread=args.spread, all=args.all, start=None, end=args.asof,
                            force=args.force, asof=args.asof,
                            no_fundamental=args.no_fundamental)
    rc = cmd_fetch(ns)
    if rc != 0:
        return rc
    rc = cmd_scan(ns)
    if rc != 0:
        return rc
    settings = load_settings()
    df = pd.read_csv(settings.data.db_path.parent / "last_scan.csv")
    out = settings.data.db_path.parent / f"scan_{pd.Timestamp.now():%Y%m%d}.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"已输出 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
