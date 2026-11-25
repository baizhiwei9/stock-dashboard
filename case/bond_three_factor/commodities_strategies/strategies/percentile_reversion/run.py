# -*- coding: utf-8 -*-
"""分位回归套利：参数对照与分板块回测入口。

用法：
    python -m commodities_strategies.strategies.percentile_reversion.run
    python -m commodities_strategies.strategies.percentile_reversion.run --variant baseline
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List

import pandas as pd
import yaml

from ...config import ROOT, get_spread, load_settings
from ...Hedge import percentile as P
from ...storage.sqlite_store import SQLiteStore

CONFIG = ROOT / "config" / "percentile_reversion.yaml"
OUT = ROOT / "data" / "percentile_reversion"

SECTOR_LABELS = {"nonferrous": "有色", "agri": "农产品", "black": "黑色",
                 "precious_observation": "贵金属观察", "out_of_scope": "范围外对照",
                 "rolling": "滚动期限差"}


def load_universe() -> dict:
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    return cfg["universe"]


def run_variant(name: str, variant: P.Variant, settings, store, universe: dict,
                start=None, end=None) -> dict:
    group_ids = {
        "农产品": universe["agri"],
        "黑色": universe["black"],
        "滚动期限差": universe.get("rolling", []),
    }
    group_ids["两板块合并"] = group_ids["农产品"] + group_ids["黑色"]

    results = {}
    frames = {}
    trades = []
    spread_rows = []
    for sector_key in ("agri", "black", "rolling"):
        for sid in universe.get(sector_key, []):
            try:
                sp = get_spread(sid)
            except KeyError:
                print(f"跳过未定义价差 {sid}")
                continue
            res = P.backtest_spread(store, sp, sector_key, variant, settings, start, end)
            results[sid] = res
            frames[sid] = res.daily.set_index("trade_date")["ret"]
            spread_rows.append({"variant": name, "spread_id": sid, "name": res.name,
                                "sector": SECTOR_LABELS.get(sector_key, sector_key),
                                "ma40_stops": int((res.trades.get("reason") == "ma40_stop").sum()) if len(res.trades) else 0,
                                **res.metrics})
            if len(res.trades):
                trades.append(res.trades)
    panel = pd.concat(frames, axis=1).fillna(0.0) if frames else pd.DataFrame()
    rows = []
    for label, ids in group_ids.items():
        ids = [i for i in ids if i in panel.columns]
        ret = P.aggregate(panel, ids)
        tdf = pd.concat([results[i].trades for i in ids if i in results and len(results[i].trades)],
                        ignore_index=True) if any(i in results and len(results[i].trades) for i in ids) else pd.DataFrame()
        m = P.metrics_from_returns(ret, tdf)
        m["ma40_stops"] = int((tdf["reason"] == "ma40_stop").sum()) if len(tdf) else 0
        m["pct_exits"] = int((tdf["reason"] == "pct_exit").sum()) if len(tdf) else 0
        m["max_hold_exits"] = int((tdf["reason"] == "max_hold").sum()) if len(tdf) else 0
        m["entry_signals"] = int(sum(results[i].metrics.get("entry_signals", 0) for i in ids if i in results))
        m["filter_rejected"] = int(sum(results[i].metrics.get("filter_rejected", 0) for i in ids if i in results))
        rows.append({"variant": name, "group": label, "n_spreads": len(ids), **m})
    metrics = pd.DataFrame(rows)

    OUT.mkdir(parents=True, exist_ok=True)
    metrics.to_csv(OUT / f"metrics_{name}.csv", index=False)
    pd.DataFrame(spread_rows).to_csv(OUT / f"spread_metrics_{name}.csv", index=False)
    panel.to_csv(OUT / f"panel_{name}.csv")
    pd.concat(trades, ignore_index=True).to_csv(OUT / f"trades_{name}.csv", index=False) if trades else pd.DataFrame().to_csv(OUT / f"trades_{name}.csv", index=False)
    # 长表：日期 / 分组 / 收益
    long_rows = []
    for label, ids in group_ids.items():
        ret = P.aggregate(panel, [i for i in ids if i in panel.columns])
        for d, v in ret.items():
            long_rows.append({"variant": name, "group": label, "trade_date": d, "ret": v})
    pd.DataFrame(long_rows).to_csv(OUT / f"daily_{name}.csv", index=False)
    return {"metrics": metrics, "panel": panel, "results": results, "trades": trades}


def main() -> int:
    parser = argparse.ArgumentParser(description="价差分位回归套利回测")
    parser.add_argument("--variant", default=None, help="只跑某个变体")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    args = parser.parse_args()

    settings = load_settings()
    store = SQLiteStore(settings.data.db_path)
    universe = load_universe()
    variants = P.load_variants(CONFIG)
    names = [args.variant] if args.variant else list(variants)
    all_metrics = []
    for name in names:
        print(f"\n== variant {name} ==")
        out = run_variant(name, variants[name], settings, store, universe, args.start, args.end)
        print(out["metrics"].to_string(index=False))
        all_metrics.append(out["metrics"])
    pd.concat(all_metrics, ignore_index=True).to_csv(OUT / "metrics_all.csv", index=False)
    print(f"\n输出目录: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
