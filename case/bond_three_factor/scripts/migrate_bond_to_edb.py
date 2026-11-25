#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性把既有利率/宏观历史缓存迁入 DataHub EDB。

迁移目标：DataHub EDB 长表 edb_series(code, date, value)。
迁移后 bond_strategies 只经 EDB 取数，不再读 CSV 缓存；后续增量由
EDB 的 bond_cgb / bond_funding / bond_macro / bond_ext 通道负责。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
WS = ROOT.parent.parent
DEFAULT_SRC = Path(os.environ.get("BOND_LEGACY_DATA",
                                  WS / "ses_gffe5ecfae4e5bffelKf2SjHn8w8zAu" / "data"))

CGB_MAP = {"1年": "CGB_YIELD_1Y", "2年": "CGB_YIELD_2Y", "5年": "CGB_YIELD_5Y",
           "7年": "CGB_YIELD_7Y", "10年": "CGB_YIELD_10Y", "30年": "CGB_YIELD_30Y"}
MACRO_DAILY = {"INTERBANK_RATE": "INTERBANK_RATE", "OIL_SC": "OIL_SC"}
MACRO_MONTH = {"M2_YoY": "M2_YOY", "CPI_YoY": "CPI_YOY", "PPI_YoY": "PPI_YOY", "PMI": "PMI"}
EXT_MAP = {"美国国债收益率2年": "US2Y", "美国国债收益率10年": "US10Y",
           "美国国债收益率30年": "US30Y", "USDCNY": "USDCNY",
           "SHIBOR_1W": "SHIBOR_1W", "AU99.99": "AU99.99"}


def _edb():
    # 统一指向 DataHub 唯一仓库 projects/datahub-multi-tier（原 WS/datahub 副本已废弃）
    for p in (WS / "projects" / "datahub-multi-tier", WS / "py_deps", WS / "pylibs", WS / ".venv_pkgs"):
        if p.is_dir() and str(p) not in sys.path:
            sys.path.insert(0, str(p))
    from data_hub import edb
    return edb


def _rows(s: pd.Series, fmt: str) -> dict:
    s = pd.to_numeric(s, errors="coerce").dropna()
    return {pd.Timestamp(d).strftime(fmt): float(v) for d, v in s.items()}


def main() -> int:
    ap = argparse.ArgumentParser(description="迁入债券/宏观历史到 EDB")
    ap.add_argument("--src", default=str(DEFAULT_SRC))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    src = Path(a.src)
    if not src.is_dir():
        print(f"[fail] 源目录不存在: {src}")
        return 1
    edb = _edb()
    edb.init_db()
    plan = []

    p = src / "国债到期收益率_财政部中债_2010-2026.csv"
    if p.is_file():
        df = pd.read_csv(p, index_col=0, parse_dates=True)
        for cn, code in CGB_MAP.items():
            if cn in df.columns:
                plan.append((code, _rows(df[cn], "%Y-%m-%d"),
                             {"source": "chinabond_czb", "freq": "D", "unit": "%"}))
    p = src / "macro_daily.csv"
    if p.is_file():
        df = pd.read_csv(p, index_col=0, parse_dates=True)
        for cn, code in MACRO_DAILY.items():
            if cn in df.columns:
                plan.append((code, _rows(df[cn], "%Y-%m-%d"),
                             {"source": "akshare", "freq": "D", "unit": "%"}))
    p = src / "macro_monthly.csv"
    if p.is_file():
        df = pd.read_csv(p, index_col=0, parse_dates=True)
        for cn, code in MACRO_MONTH.items():
            if cn in df.columns:
                plan.append((code, _rows(df[cn], "%Y-%m"),
                             {"source": "nbs_akshare", "freq": "M", "unit": "%"}))
    p = src / "external_vars.csv"
    if p.is_file():
        df = pd.read_csv(p, index_col=0, parse_dates=True)
        for cn, code in EXT_MAP.items():
            if cn in df.columns:
                plan.append((code, _rows(df[cn], "%Y-%m-%d"),
                             {"source": "fred_akshare_sge", "freq": "D", "unit": ""}))
    p = src / "bt" / "dr007_proxy.csv"
    if p.is_file():
        df = pd.read_csv(p, index_col=0, parse_dates=True)
        if "DR007_PROXY" in df.columns:
            plan.append(("DR007_PROXY", _rows(df["DR007_PROXY"], "%Y-%m-%d"),
                         {"source": "akshare_rate_interbank", "freq": "D", "unit": "%"}))

    total = 0
    for code, rows, meta in plan:
        if not rows:
            print(f"[skip] {code}: 无有效数据")
            continue
        if a.dry_run:
            print(f"[dry] {code}: {len(rows)} 行 {min(rows)}~{max(rows)}")
            total += len(rows)
            continue
        n = edb.put_series(code, rows, meta)
        total += n
        last, lastv = edb.latest(code)
        print(f"[ok] {code}: +{n} 行，最新 {last} = {lastv}")

    # 派生 10Y-2Y 利差（bp），与策略口径一致
    y10, y2 = edb.series("CGB_YIELD_10Y"), edb.series("CGB_YIELD_2Y")
    if len(y10) and len(y2):
        sp = (y10 - y2).dropna() * 100
        rows = _rows(sp, "%Y-%m-%d")
        if not a.dry_run:
            edb.put_series("CGB_SPREAD_10Y2Y", rows,
                           {"source": "derived", "freq": "D", "unit": "bp"}, derived=1)
        print(f"[ok] CGB_SPREAD_10Y2Y: {len(rows)} 行（派生）")
        total += len(rows)

    print(f"[done] 迁入序列 {len(plan)} 个，写入 {total} 行 -> {edb.DB}")
    return 0


if __name__ == "__main__":
    sys.exit(main())