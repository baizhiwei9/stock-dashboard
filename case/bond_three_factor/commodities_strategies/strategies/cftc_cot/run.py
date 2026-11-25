# -*- coding: utf-8 -*-
"""CFTC COT 极值策略入口。

用法
----
    python -m commodities_strategies.strategies.cftc_cot.run --report
    python -m commodities_strategies.strategies.cftc_cot.run --report --backtest
    python -m commodities_strategies.strategies.cftc_cot.run --report --no-fetch --asof 2026-11-14
    python -m commodities_strategies.strategies.cftc_cot.run --report --push      # 可选推送

--no-fetch：只用本地 CFTC zip 缓存（默认先看 data/cache/cftc_cot，再回退旧工作区缓存）；
--push    ：把文字版推到飞书 Webhook（TOOLS.md 里的群机器人）。推送失败不影响报告。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd

from ...config import ROOT, load_settings
from ...storage.sqlite_store import SQLiteStore
from . import engine as E
from . import report as RP
from .report import _cwidth


def _fmt_val(v, nd=2) -> str:
    try:
        v = float(v)
        return "—" if not np.isfinite(v) else f"{v:.{nd}f}"
    except (TypeError, ValueError):
        return "—"


def _print_backtest(bt: dict) -> None:
    """控制台回测摘要：事件胜率 + Q 五口径 IC。"""
    hit = bt.get("hit") or {}
    if not hit:
        print("[回测] 无事件样本")
        return
    hs = sorted({h for d in hit.values() for h in d})
    print("事件胜率（极值 episode 去重）")
    head = f"{'品种':<12}{'事件数':>6}" + "".join(f"{str(h)+'周胜率':>10}{'均值':>10}" for h in hs)
    print(head)
    print("-" * _cwidth(head))
    for k, d in hit.items():
        n = max((d[h]["n"] for h in hs), default=0)
        cells = ""
        for h in hs:
            v = d.get(h, {})
            hv, mv = v.get("hit", float("nan")), v.get("mean_bp", float("nan"))
            cells += f"{'—' if not np.isfinite(hv) else f'{hv:.0%}':>10}"
            cells += f"{'—' if not np.isfinite(mv) else f'{mv:+.0f}bp':>10}"
        print(f"{k:<12}{n:>6}{cells}")

    q = bt.get("q") or {}
    if q:
        print("\nQ 五口径稳健性（周频，IC / 净值）")
        print(f"{'品种':<10}{'口径':<6}{'IC_rev':>9}{'IC_mom':>9}{'反转净值':>10}{'动量净值':>10}")
        for k, d in q.items():
            for qc, v in d.items():
                print(f"{k:<10}{qc:<6}{v['ic_rev']:>9.3f}{v['ic_mom']:>9.3f}"
                      f"{v['rev']['nav']:>10.2f}{v['mom']['nav']:>10.2f}")
    pooled = bt.get("pooled_all")
    if pooled:
        nv = pooled["nav"]
        print(f"\nCOT 分位方向（{pooled.get('note', '等权组合')}）：N={pooled['n']}  "
              f"IC={pooled['ic']:+.3f}  净值={nv['nav']:.2f}  Sharpe={nv['sharpe']:.2f}  "
              f"胜率={nv['hit']:.1%}")
    dom = bt.get("domestic") or {}
    if dom:
        print("\n国内传导核对（相关性 <0.30 判为联动弱，不可套）")
        print(f"{'品种':<10}{'数据源':<10}{'周数':>6}{'相关性':>8}"
              f"{'4周(多/空)':>13}{'8周(多/空)':>13}  判定")
        for k, d in dom.items():
            if "error" in d:
                print(f"{k:<10}{d.get('source','—'):<10}{'—':>6}{'—':>8}"
                      f"{'—':>13}{'—':>13}  {d['error']}")
                continue
            corr = d.get("link_corr", float("nan"))
            bd = d.get("hit_by_dir") or {}

            def _h(h):
                v = (bd.get("1") or {}).get(str(h), {}).get("hit", float("nan"))
                w = (bd.get("-1") or {}).get(str(h), {}).get("hit", float("nan"))
                f = lambda x: "—" if not np.isfinite(x) else f"{x:.0%}"   # noqa: E731
                return f"{f(v)}/{f(w)}"

            print(f"{k:<10}{d.get('source','—'):<10}{d.get('n',0):>6}"
                  f"{('—' if not np.isfinite(corr) else f'{corr:+.2f}'):>8}"
                  f"{_h(4):>13}{_h(8):>13}"
                  f"  {'联动弱，别套' if d.get('weak_link') else '可参考'}")


def push_feishu(text: str, webhook: Optional[str] = None) -> bool:
    """推飞书群机器人；webhook 缺省读环境变量或 TOOLS.md 里的地址。"""
    import os
    import requests

    hook = webhook or os.environ.get("CFTC_FEISHU_WEBHOOK") or \
        "https://open.feishu.cn/open-apis/bot/v2/hook/6d34534a-af18-48ba-be77-a30c524734a8"
    try:
        r = requests.post(hook, json={"msg_type": "text",
                                      "content": {"text": text + "\n\n📎 来自 commodities_strategies"}},
                          timeout=30)
        ok = r.status_code == 200 and r.json().get("code", 0) == 0
        print(f"[推送] 飞书 {'成功' if ok else '失败: ' + r.text[:160]}")
        return ok
    except Exception as exc:                             # noqa: BLE001
        print(f"[推送] 飞书异常: {exc}")
        return False


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="cftc_cot", description="CFTC COT 持仓极值策略")
    ap.add_argument("--report", action="store_true", help="生成文本/markdown 报告与持仓状态图")
    ap.add_argument("--backtest", action="store_true", help="附事件胜率与 Q 五口径稳健性统计")
    ap.add_argument("--push", action="store_true", help="推送到飞书群机器人（可选）")
    ap.add_argument("--no-push", action="store_true", help="不推送（默认行为，保留兼容）")
    ap.add_argument("--push-webhook", default=None)
    ap.add_argument("--start-year", type=int, default=None)
    ap.add_argument("--asof", default=None, help="截止周（YYYY-MM-DD），用于复盘")
    ap.add_argument("--no-fetch", action="store_true", help="只用本地缓存，不联网")
    ap.add_argument("--no-chart", action="store_true", help="不画图")
    ap.add_argument("--keys", nargs="*", default=None, help="只跑指定品种（copper/gold/silver/corn/crude）")
    ap.add_argument("--force", action="store_true", help="强制重下当年 CFTC 包")
    ap.add_argument("--json", action="store_true", help="额外打印机器可读 JSON")
    args = ap.parse_args(argv)

    if not args.report and not args.backtest:
        args.report = True

    cfg = E.CftcConfig.from_dict(E.load_config())
    settings = load_settings()
    store = SQLiteStore(settings.data.db_path)

    print(f"== CFTC COT 持仓极值 ==  start_year={args.start_year or cfg.start_year}  "
          f"lookback={cfg.lookback_weeks}周  阈值={cfg.hi:.2f}/{cfg.lo:.2f}  "
          f"窗口={cfg.win_min}~{cfg.win_max}周  "
          f"{'离线缓存' if args.no_fetch else '在线'}")

    states = E.build_state(cfg, settings=settings, keys=args.keys,
                           start_year=args.start_year, asof=args.asof,
                           no_fetch=args.no_fetch, force=args.force, store=store)

    if not states:
        print("[ERR] 无可用品种，检查数据源")
        return 1

    rows = []
    for st in states:
        ep, vd = st.episode, st.verdict
        rows.append({
            "key": st.key, "name": st.commodity.name_cn,
            "cot": None if not np.isfinite(ep.cot) else round(float(ep.cot), 4),
            "dir": ep.dir, "action": vd.action, "held_weeks": ep.held,
            "start": None if ep.start is None else ep.start.strftime("%Y-%m-%d"),
            "pnl_pct": round(float(ep.pnl_pct), 4) if np.isfinite(ep.pnl_pct) else None,
            "last_week": None if ep.last_date is None else ep.last_date.strftime("%Y-%m-%d"),
            "advice": vd.advice, "price_source": st.price_source, "warning": st.warning,
        })

    bt = None
    if args.backtest:
        bt = E.run_backtest(states, cfg, settings=settings, with_domestic=True)

    print()
    print(RP.build_text(states, cfg))

    outs = {}
    if args.report:
        tag = pd.Timestamp(args.asof).strftime("%Y%m%d") if args.asof else pd.Timestamp.now().strftime("%Y%m%d")
        outs = RP.write_reports(states, cfg, bt=bt, asof_tag=tag, chart=not args.no_chart)
        print()
        for k, p in outs.items():
            print(f"[输出] {k:<14} {p}")

    if bt:
        print()
        _print_backtest(bt)
    payload = {"asof": rows[0]["last_week"] if rows else None, "rows": rows}
    if args.json:
        p = cfg.out_dir / "latest_state.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[输出] json           {p}")
        print(json.dumps(payload, ensure_ascii=False))

    if args.push:
        txt = RP.build_text(states, cfg)
        if outs.get("figure"):
            txt += f"\n\n图: {outs['figure']}"
        push_feishu(txt, args.push_webhook)

    return 0


if __name__ == "__main__":
    sys.exit(main())
