#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
价差回归策略 — 独立运行入口
收盘后或手动运行, 自行获取日线数据, 跑配对表里的全部配对信号计算

用法:
  python -m commodity_spread_scanner.run_spread_monitor              # 打印报告
  python -m commodity_spread_scanner.run_spread_monitor --send       # 发送到飞书
  python -m commodity_spread_scanner.run_spread_monitor --summary    # 仅持仓汇总
"""

import sys
import os
import csv
import json
import argparse
import time
from datetime import datetime
from pathlib import Path

# 确保项目根目录在 sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from commodity_spread_scanner.scanner.pair_table import load_pair_table, get_unique_symbols
from commodity_spread_scanner.scanner.calc_signal import analyze_pair
from commodity_spread_scanner.scanner.report import (
    build_signal_report, build_summary_report, build_full_scan_report,
    compute_board_momentum_rank,
)
from commodity_spread_scanner.config.config import STATE_FILE

_CASE_DIR = Path(__file__).resolve().parents[1]     # …/case/futures_xsec_hedge
_KLINE_FILE = Path(os.environ.get("SPREAD_KLINE_FILE")
                   or _CASE_DIR / "data" / "full" / "all_kline.csv")


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"pair_dirs": {}, "last_calc_date": None}


def save_state(state: dict):
    try:
        os.makedirs(os.path.dirname(STATE_FILE) or ".", exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  保存状态失败: {e}")


def load_bars(symbol: str) -> list:
    """从本 case 自己的行情产物读某个品种的主力连续日线。

    数据源：``case/futures_xsec_hedge/data/full/all_kline.csv``（由同目录 ``data_update.py``
    经 DataHub 期货通道增量落盘），列为 variety / contract / date / close / vol / oi 等。
    同一交易日取持仓量最大的合约视为主力，输出按日期升序的 bar 列表。

    原先这里 ``from data_fetcher import get_futures_daily``，该模块从未随包入库，一跑即
    ImportError；改为只依赖仓库内的数据链路，需要换源时用 SPREAD_KLINE_FILE 指定同结构 CSV。
    """
    if not _KLINE_FILE.is_file():
        raise FileNotFoundError(
            "未找到行情文件 %s；先跑 `python %s` 生成，"
            "或用 SPREAD_KLINE_FILE 指向同结构 CSV。" % (_KLINE_FILE, _CASE_DIR / "data_update.py")
        )
    variety = symbol[:-1] if symbol.endswith("0") else symbol
    best = {}
    with _KLINE_FILE.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if (row.get("variety") or "") != variety:
                continue
            try:
                close = float(row["close"])
            except (KeyError, TypeError, ValueError):
                continue
            try:
                oi = float(row.get("oi") or 0)
            except (TypeError, ValueError):
                oi = 0.0
            d = row.get("date") or ""
            cur = best.get(d)
            if cur is None or oi > cur["oi"]:
                best[d] = {"date": d, "close": close, "oi": oi,
                           "contract": row.get("contract") or ""}
    return [best[d] for d in sorted(best)]


def fetch_daily_data(symbols: set) -> dict:
    """获取所有品种的日线数据"""
    data = {}
    for symbol in sorted(symbols):
        try:
            print(f"  获取 {symbol} 日线数据...")
            bars = load_bars(symbol)
            if bars:
                data[symbol] = bars
                print(f"    {symbol}: {len(bars)} 条")
            else:
                print(f"    {symbol}: 无数据")
            time.sleep(0.3)
        except Exception as e:
            print(f"    {symbol}: 获取失败 - {e}")
    return data


def run_scan(send: bool = False, summary_only: bool = False):
    """执行全量扫描"""
    print(f"\n{'=' * 60}")
    print(f"价差回归扫描器 — 独立运行")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'=' * 60}")

    # 加载配对表
    pairs = load_pair_table()
    symbols = get_unique_symbols(pairs)
    print(f"\n配对: {len(pairs)} 组, 品种: {len(symbols)} 个")

    # 获取数据
    print(f"\n[数据获取]")
    daily_data = fetch_daily_data(symbols)

    # 加载状态
    state = load_state()
    pair_dirs = state.get("pair_dirs", {})
    pair_alerts = state.get("pair_alerts", {})

    # 分析所有配对
    print(f"\n[信号分析]")
    results = []
    for pair in pairs:
        bars_a = daily_data.get(pair.leg_a, [])
        bars_b = daily_data.get(pair.leg_b, [])

        if not bars_a or not bars_b:
            print(f"  {pair.pair_id}: 数据不足")
            continue

        prev_dir = pair_dirs.get(pair.pair_id, 0)
        prev_alert = pair_alerts.get(pair.pair_id, 0)
        result = analyze_pair(pair, bars_a, bars_b, prev_dir, prev_alert)

        if result:
            results.append(result)
            pair_dirs[pair.pair_id] = result["direction"]
            pair_alerts[pair.pair_id] = result["alert"]
            signal = result["signal"]
            z = result["z"]
            stars = "⭐" if "ALERT" in signal else ("⭐⭐⭐⭐⭐" if signal in ("ENTRY_LONG", "ENTRY_SHORT", "HOLD") else "")
            print(f"  {pair.pair_id}: z={z:+.2f} dir={result['direction']} alert={result['alert']} signal={signal} {stars}")

    # 保存状态
    state["pair_dirs"] = pair_dirs
    state["pair_alerts"] = pair_alerts
    state["last_calc_date"] = datetime.now().strftime("%Y-%m-%d")
    save_state(state)

    # 计算板块动量排名
    rankings = compute_board_momentum_rank(results)

    # 生成报告
    print(f"\n[报告]")

    if summary_only:
        report = build_summary_report(results, state, rankings)
        reports = [report] if report else []
    else:
        # 全量扫描报告
        full_report = build_full_scan_report(results, rankings)
        reports = [full_report] if full_report else []

        # 信号变化报告
        state_changes = [r for r in results if r["signal"] in
                        ("ENTRY_LONG", "ENTRY_SHORT", "CLOSE_LONG", "CLOSE_SHORT")]
        if state_changes:
            signal_reports = build_signal_report(state_changes, state, rankings)
            reports.extend(signal_reports)

    if not reports:
        print("  无报告生成")
        return

    for title, content in reports:
        print(f"\n{'=' * 40}")
        print(f"  {title}")
        print(f"{'=' * 40}")
        print(content)

    # 发送飞书
    if send and reports:
        _send_reports(reports)


def _send_reports(reports):
    """经 case/_lib/feishu_creds 的机器人 webhook 发群消息（凭据走 secrets / 环境变量）。

    原实现 ``from notifier import send_to_chat`` 依赖一个从未入库的模块，--send 必炸；
    这里改用仓库内已有的凭据模块，chat_id 不再写死在 config 里（webhook 已绑定目标群）。
    """
    _lib = str(_CASE_DIR.parent / "_lib")
    if _lib not in sys.path:
        sys.path.insert(0, _lib)
    try:
        from feishu_creds import WEBHOOK_URL
    except Exception as e:
        print(f"  发送失败：读不到飞书凭据（{e}）")
        return
    import urllib.request
    for title, content in reports:
        body = json.dumps({"msg_type": "text",
                           "content": {"text": f"{title}\n{content}"}},
                          ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(WEBHOOK_URL, data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                print(f"  已发送：{title}（HTTP {resp.status}）")
        except Exception as e:
            print(f"  发送失败 {title}：{e}")
        time.sleep(2)  # 限流


def main():
    parser = argparse.ArgumentParser(description="商品期货价差回归扫描器")
    parser.add_argument("--send", action="store_true", help="发送到飞书群")
    parser.add_argument("--summary", action="store_true", help="仅输出持仓汇总")
    args = parser.parse_args()

    run_scan(send=args.send, summary_only=args.summary)


if __name__ == "__main__":
    main()
