#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日模拟盘全链路入口（合并进既有套利任务工程）
=========================================================
流程：
  1) 增量抓取新浪行情：只抓最近 14 个月活跃合约，与 all_kline.csv 合并去重
  2) 重建 panel_full.csv + factors_full.csv（全量，约 2.5 分钟）
  3) run_daily 推进模拟盘（幂等：同日重复运行不重复开仓）
  4) 生成日报 markdown + 仓单 CSV + 净值历史
  5) 推送飞书仓单卡片

用法：
  python3 run_sim_daily.py            # 完整链路（拉数据+重建+模拟盘+推送）
  python3 run_sim_daily.py --no-fetch # 跳过行情抓取，直接重建+模拟盘
  python3 run_sim_daily.py --no-push  # 不推送飞书
  python3 run_sim_daily.py --reset    # 重置账户为 20 万（化工10+农产品10）并重新建仓（首日）
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from data_update import incremental_fetch, apply_kline_merge  # noqa: E402


def rebuild_factors(no_fetch: bool):
    """重建 panel 与 factors（工作目录必须在工程根）"""
    t0 = time.time()
    print("==", "重建 panel_full.csv ...")
    subprocess.run([sys.executable, "panel_build.py"], cwd=ROOT, check=True)
    print("==", "重建 factors_full.csv ...")
    subprocess.run([sys.executable, "factors_build_2022.py"], cwd=ROOT, check=True)
    print(f"== 重建完成，耗时 {time.time()-t0:.0f}s")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true", help="跳过行情增量抓取")
    ap.add_argument("--no-push", action="store_true", help="不推送飞书")
    ap.add_argument("--reset", action="store_true", help="重置账户并重新建仓")
    args = ap.parse_args()

    if not args.no_fetch:
        print("==", "增量抓取行情 ...")
        kline = incremental_fetch()
        apply_kline_merge(kline)

    rebuild_factors(args.no_fetch)

    import pandas as pd
    from sim_trade.engine import run_daily
    from sim_trade.config import FACTORS_FILE, PANEL_FILE, STATE_DIR
    from sim_trade.report import build_daily_report
    from sim_trade.feishu_push import build_sim_card
    from sim_trade.oi_hedge import run_oi_daily

    factors = pd.read_csv(FACTORS_FILE, parse_dates=["date"])
    last = factors["date"].max()
    print("==", "最新共同交易日:", last.date())

    # ---- 执行前检查：因子拥挤度红绿灯（不改变既有交易逻辑，仅作前置判断与提示）----
    from sim_trade.crowding import evaluate as crowding_eval, format_lines as crowding_lines
    crowd = crowding_eval(last, factors)
    for _ln in crowding_lines(crowd):
        print("==", _ln)
    print("==", f"拥挤度结论：绿灯 {len(crowd['green'])} ｜ 黄灯 {len(crowd['yellow'])} ｜ "
                f"红灯 {len(crowd['red'])} ｜ "
                f"{'允许开新仓' if crowd['allow'] else '拥挤/失效期，不建议开新仓'}"
                f"（标题标记：{crowd['title_flag'] or '无'}）")

    res = run_daily(last, factors, force_rebalance=args.reset, reset=args.reset)
    print(f"== 模拟盘推进完成：净值 {res['acc']['total_nav']:,.2f}，"
          f"当日盈亏 {res['pnl_day']:+,.2f}，开仓 {len(res['orders']['open'])}，"
          f"平仓 {len(res['orders']['close'])}，减仓 {len(res['orders']['reduce'])}")

    # 持仓量横截面对冲单元（独立账户/独立资金口径，周频调仓）
    panel = pd.read_csv(PANEL_FILE, parse_dates=["date"])
    oi_res = run_oi_daily(last, panel, force_rebalance=args.reset, reset=args.reset)
    print(f"== 持仓量单元推进完成：净值 {oi_res['acc']['total_nav']:,.2f}，"
          f"当日盈亏 {oi_res['pnl_day']:+,.2f}，开仓 {len(oi_res['orders']['open'])}，"
          f"平仓 {len(oi_res['orders']['close'])}，减仓 {len(oi_res['orders']['reduce'])}")

    # 带风控横截面对冲（对照单元，独立核算）
    from sim_trade.xsec_risk import run_daily_risk
    risk_res = run_daily_risk(last, factors, crowd_res=crowd)
    print(f"== 带风控对照单元推进完成：净值 {risk_res['nav']:,.2f}，当日盈亏 "
          f"{risk_res['pnl_day']:+,.2f}，拥挤度档位 {risk_res['switches'].get('拥挤度档位', '—')}"
          + (f"（{risk_res['note']}）" if risk_res.get("note") else ""))

    text = build_daily_report(res, oi_res=oi_res, crowd=crowd, risk_res=risk_res)
    print("== 日报已落盘:", f"sim_trade/output/daily_report_{last.date()}.md")

    if not args.no_push:
        try:
            from sim_trade.feishu_pusher import send_feishu_card
            card = build_sim_card(res, oi_res=oi_res, crowd=crowd, risk_res=risk_res)
            import json as _json
            (ROOT / "sim_trade" / "output" / f"feishu_card_{last.date()}.json").write_text(
                _json.dumps(card, ensure_ascii=False, indent=1), encoding="utf-8")
            ok = send_feishu_card(card)
            print("== 飞书推送:", "成功" if ok else "失败")
        except Exception as e:
            print("== 飞书推送异常:", repr(e)[:200])
    else:
        print("== 跳过飞书推送（--no-push）")

    # 落盘当日运行摘要（供定时任务日志/飞书结果投递）
    summary = {
        "date": str(last.date()), "nav_total": res["acc"]["total_nav"],
        "pnl_day": res["pnl_day"], "open": len(res["orders"]["open"]),
        "close": len(res["orders"]["close"]), "reduce": len(res["orders"]["reduce"]),
        "is_reb": res["is_reb"], "is_first": res["is_first"],
        "oi_nav_total": oi_res["acc"]["total_nav"], "oi_pnl_day": oi_res["pnl_day"],
        "oi_open": len(oi_res["orders"]["open"]), "oi_close": len(oi_res["orders"]["close"]),
        "oi_reduce": len(oi_res["orders"]["reduce"]), "oi_is_reb": oi_res["is_reb"],
    }
    import json
    (ROOT / "sim_trade" / "state" / "last_run.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print("== 完成")


if __name__ == "__main__":
    main()