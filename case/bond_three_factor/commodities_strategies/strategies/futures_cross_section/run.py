# -*- coding: utf-8 -*-
"""横截面对冲策略入口：DataHub 取数 → 面板 → 因子 → 核心回测。

示例：
    python -m commodities_strategies.strategies.futures_cross_section.run --core
    python -m commodities_strategies.strategies.futures_cross_section.run --full --start 20090101 --end 20261112
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from . import build_panel, data, engine as E, lib_factors, run_all
from .common import DATA, OUT, log


def core_backtest() -> pd.DataFrame:
    """原组合主口径：三重筛选 | 传统价差 | 等权 | 同板块 / 全市场。"""
    p, dirs = E.load()
    liq, keep, full, blocks = E.universe(p)
    Rt = E.wide(p, "ret_near")
    sigs = E.build_signals(p, dirs)
    cfg = dict(spread="trad", weight="equal", cost_bp=5.0, params={})
    rows = []
    block_runs = {}
    for b, members in blocks.items():
        members = [x for x in members if x in Rt.columns]
        if len(members) >= 6:
            block_runs[b] = E.run_pool(Rt, sigs[("triple", "12m")], members, cfg)
    blk = pd.concat([v["pnl"].rename(b) for b, v in block_runs.items()], axis=1).mean(axis=1)
    fl = E.run_pool(Rt, sigs[("triple", "12m")], full, cfg)
    for name, series in [("同板块", blk), ("全市场", fl["pnl"])]:
        m = E.metrics(series)
        rows.append({"config": f"三重筛选|传统价差|equal|{name}", **{k: m.get(k) for k in
                    ["n", "cagr", "vol", "sharpe", "sortino", "mdd", "calmar", "nav", "hit", "skew", "tail5"]}})
    out = pd.DataFrame(rows)
    out.to_csv(OUT / "core_metrics.csv", index=False)
    log("core metrics")
    print(out.to_string(index=False))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="期货横截面对冲策略")
    parser.add_argument("--start", default="20090101")
    parser.add_argument("--end", default=None)
    parser.add_argument("--refresh", action="store_true", help="强制刷新 DataHub 行情")
    parser.add_argument("--skip-sync", action="store_true", help="跳过取数，直接用已有数据文件")
    parser.add_argument("--core", action="store_true", help="只跑主口径回测")
    parser.add_argument("--full", action="store_true", help="跑原 run_all 全量结果表")
    args = parser.parse_args()

    if not args.skip_sync:
        data.sync(args.start, args.end, refresh=args.refresh)
    build_panel.main()
    lib_factors.main()
    if args.core or not args.full:
        core_backtest()
    if args.full:
        run_all.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
