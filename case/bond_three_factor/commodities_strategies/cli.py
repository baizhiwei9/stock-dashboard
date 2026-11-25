# -*- coding: utf-8 -*-
"""commodities_strategies 命令行入口。

示例：
    python -m commodities_strategies.cli doctor
    python -m commodities_strategies.cli fetch --spread MA_05_09
    python -m commodities_strategies.cli scan --spread MA_05_09
    python -m commodities_strategies.cli report
    python -m commodities_strategies.cli backtest --spread MA_05_09 --confirm 3
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import List, Optional

import pandas as pd

from .config import ROOT, get_spread, load_settings, load_spreads
from .Futures import catalog, datahub
from .Hedge.spreads import build_spread_series
from .Hedge import engine
from .Hedge.backtest import backtest as run_backtest
from .storage.sqlite_store import SQLiteStore


def _store():
    settings = load_settings()
    return settings, SQLiteStore(settings.data.db_path)


def cmd_doctor(args) -> int:
    settings, store = _store()
    print(f"项目根目录 : {ROOT}")
    print(f"SQLite     : {settings.data.db_path}")
    root = datahub.find_datahub_root(settings)
    print(f"DataHub    : {root or '未找到'} ({'可用' if datahub.available(settings) else '不可用'})")
    print(f"数据源顺序 : {' > '.join(settings.data.source_priority)}")
    for mod in ("akshare", "tushare", "statsmodels", "yaml"):
        try:
            __import__(mod)
            print(f"{mod:<11}: OK")
        except Exception as exc:
            print(f"{mod:<11}: 不可用 ({exc.__class__.__name__})")
    print("表行数     :", store.table_counts())
    return 0


def cmd_contracts(args) -> int:
    df = catalog.current_contracts(args.product)
    if df.empty:
        print(f"{args.product} 无实时合约数据")
        return 1
    print(df.head(args.limit).to_string(index=False))
    return 0


def cmd_fetch(args) -> int:
    settings, store = _store()
    if not args.spread and not args.all:
        print("请指定 --spread <id> 或 --all")
        return 1
    spreads = load_spreads() if args.all else [get_spread(s) for s in args.spread]
    for sp in spreads:
        try:
            df = build_spread_series(settings, store, sp, start=args.start, end=args.end, force=args.force)
            print(f"{sp.id:<14} 价差 {len(df):>5} 行  {df['trade_date'].min().date()} ~ {df['trade_date'].max().date()}")
        except Exception as exc:
            print(f"{sp.id:<14} 失败: {exc}")
            store.log("fetch", sp.id, "error", 0, "", str(exc))
    return 0


def cmd_scan(args) -> int:
    settings, store = _store()
    if not args.spread and not args.all:
        print("请指定 --spread <id> 或 --all")
        return 1
    results = engine.scan_all(settings, store, spread_ids=args.spread or None, asof=args.asof,
                              force=args.force, with_fundamentals=not args.no_fundamental)
    engine.print_report(results)
    rows = []
    for r in results:
        if "error" in r:
            rows.append({"spread_id": r["spread"].id, "error": r["error"]})
            continue
        d = r["decision"]
        rows.append({"spread_id": d.spread_id, "name": r["spread"].name, "asof": d.asof_date,
                     "action": d.action, "direction": d.direction, "z": d.z, "pct": d.pct,
                     "adf_p": d.adf_p, "hurst": d.hurst, "vol_pct": d.vol_pct,
                     "fundamental": d.fundamental_flag, "reasons": "；".join(d.reasons)})
    out = settings.data.db_path.parent / "last_scan.csv"
    pd.DataFrame(rows).to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n明细已写入 {out}")
    return 0


def cmd_report(args) -> int:
    settings, store = _store()
    df = store.latest_signals(asof=args.asof)
    if df.empty:
        print("暂无信号，请先运行 scan")
        return 1
    if args.asof:
        df = df[df["asof_date"] == args.asof]
    cols = ["name", "trade_date", "value", "pct", "z", "adf_p", "hurst", "vol_pct",
            "action", "direction", "fundamental_flag"]
    print(df[cols].to_string(index=False))
    if args.csv:
        df.to_csv(args.csv, index=False, encoding="utf-8-sig")
        print(f"已导出 {args.csv}")
    return 0


def cmd_backtest(args) -> int:
    settings, store = _store()
    sp = get_spread(args.spread)
    res = run_backtest(settings, store, sp, start=args.start, end=args.end, confirm_days=args.confirm)
    print(f"{sp.name} ({sp.id})  回测区间 {args.start or '全样本'} ~ {args.end or '最新'}")
    print(f"交易笔数 : {res['n_trades']}")
    print(f"胜率     : {res['win_rate']:.1%}" if pd.notna(res["win_rate"]) else "胜率     : -")
    print(f"累计盈亏 : {res['total_pnl']:,.0f} 元")
    print(f"累计收益 : {res['total_return']:.2%}（占用资本 {res['capital']:,.0f} 元）" if pd.notna(res["total_return"]) else "")
    print(f"年化     : {res['annualized']:.2%}" if pd.notna(res["annualized"]) else "年化     : -")
    print(f"最大回撤 : {res['max_drawdown']:,.0f} 元")
    print(f"夏普     : {res['sharpe']:.2f}" if pd.notna(res["sharpe"]) else "夏普     : -")
    print(f"平均持有 : {res['avg_hold']:.1f} 日" if pd.notna(res["avg_hold"]) else "平均持有 : -")
    if not res["trades"].empty:
        print("\n交易明细：")
        print(res["trades"].to_string(index=False))
    return 0


def cmd_init(args) -> int:
    settings, store = _store()
    print(f"SQLite 初始化完成: {settings.data.db_path}")
    print("表行数:", store.table_counts())
    return 0


def cmd_cftc(args) -> int:
    from .strategies.cftc_cot import run as cftc_run

    argv: List[str] = []
    if args.report:
        argv.append("--report")
    if args.backtest:
        argv.append("--backtest")
    if args.push and not getattr(args, "no_push", False):
        argv.append("--push")
    if args.start_year is not None:
        argv += ["--start-year", str(args.start_year)]
    if args.asof:
        argv += ["--asof", str(args.asof)]
    if args.no_fetch:
        argv.append("--no-fetch")
    if args.keys:
        argv += ["--keys", *args.keys]
    if args.no_chart:
        argv.append("--no-chart")
    if not argv:
        argv = ["--report"]
    return cftc_run.main(argv)


def cmd_bond(args) -> int:
    from .strategies.bond_analysis import run as bond_run

    argv: List[str] = []
    if args.json:
        argv.append("--json")
    if args.markdown:
        argv.append("--markdown")
    if args.chart:
        argv.append("--chart")
    if args.status:
        argv.append("--status")
    if args.push:
        argv.append("--push")
    if getattr(args, "text", False):
        argv.append("--text")
    if getattr(args, "refresh", False):
        argv.append("--refresh")
    if getattr(args, "push_webhook", None):
        argv += ["--push-webhook", args.push_webhook]
    return bond_run.main(argv or None)
def cmd_bond_weekly(args) -> int:
    """利率三因子周度预测 + Slope 交易机会（数据统一经 DataHub：债券走 bond 子包）。"""
    from .bond_strategies.run import main as bond_main

    argv: List[str] = []
    if getattr(args, "update", False):
        argv.append("--update")
    if getattr(args, "force", False):
        argv.append("--force")
    if getattr(args, "out", None):
        argv += ["--out", args.out]
    argv += ["--seed", args.seed]
    return bond_main(argv)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="commodities_strategies", description="期货对冲套利价差与分位扫描")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("doctor", help="检查环境与数据模块")
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("init", help="初始化 SQLite")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("contracts", help="列出当前挂牌合约与持仓")
    sp.add_argument("--product", required=True)
    sp.add_argument("--limit", type=int, default=12)
    sp.set_defaults(func=cmd_contracts)

    sp = sub.add_parser("fetch", help="构建并落库价差序列")
    sp.add_argument("--spread", nargs="*", default=[])
    sp.add_argument("--all", action="store_true")
    sp.add_argument("--start")
    sp.add_argument("--end")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_fetch)

    sp = sub.add_parser("scan", help="计算分位/统计量并生成信号")
    sp.add_argument("--spread", nargs="*", default=[])
    sp.add_argument("--all", action="store_true")
    sp.add_argument("--asof")
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--no-fundamental", action="store_true")
    sp.set_defaults(func=cmd_scan)

    sp = sub.add_parser("report", help="查看最新信号")
    sp.add_argument("--asof")
    sp.add_argument("--csv")
    sp.set_defaults(func=cmd_report)

    sp = sub.add_parser("backtest", help="价差回归回测")
    sp.add_argument("--spread", required=True)
    sp.add_argument("--start")
    sp.add_argument("--end")
    sp.add_argument("--confirm", type=int)
    sp.set_defaults(func=cmd_backtest)

    sp = sub.add_parser("cftc", help="CFTC COT 持仓极值策略（周报/胜率/Q口径）")
    sp.add_argument("--report", action="store_true", help="生成文本/markdown 报告与图（默认）")
    sp.add_argument("--backtest", action="store_true", help="附事件胜率与 Q 五口径稳健性统计")
    sp.add_argument("--push", action="store_true", help="推送到飞书群机器人")
    sp.add_argument("--no-push", action="store_true", help="不推送（默认行为，保留兼容）")
    sp.add_argument("--start-year", type=int, default=None)
    sp.add_argument("--asof", default=None)
    sp.add_argument("--no-fetch", action="store_true", help="只用本地缓存，不联网")
    sp.add_argument("--keys", nargs="*", default=None)
    sp.add_argument("--no-chart", action="store_true")
    sp.set_defaults(func=cmd_cftc)

    sp = sub.add_parser("bond", help="债市分析（四层判定：曲线/信用/存单/资金）")
    sp.add_argument("--json", action="store_true", help="输出 JSON 快照")
    sp.add_argument("--markdown", action="store_true", help="输出 Markdown 报告")
    sp.add_argument("--chart", action="store_true", help="出双栏图")
    sp.add_argument("--status", action="store_true", help="只看数据新鲜度")
    sp.add_argument("--push", action="store_true", help="推飞书")
    sp.add_argument("--text", action="store_true", help="推送用纯文本")
    sp.add_argument("--refresh", action="store_true", help="先刷 DataHub 再算")
    sp.add_argument("--push-webhook", default=None)
    sp.set_defaults(func=cmd_bond)

    sp = sub.add_parser("bond-weekly", help="利率三因子周度预测与 Slope 交易机会（取数经 DataHub）")
    sp.add_argument("--update", action="store_true", help="运行前刷新 DataHub 债券数据")
    sp.add_argument("--force", action="store_true", help="忽略缓存 TTL 强制刷新")
    sp.add_argument("--out", help="报告输出目录")
    sp.add_argument("--seed", default="42", help="XGB 随机种子；none 表示不固定")
    sp.set_defaults(func=cmd_bond_weekly)
    return p



def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    # DataHub 自带 INFO 级 handler，命令行下压到 ERROR，避免刷屏
    logging.getLogger("data_hub").setLevel(logging.ERROR)
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
