# -*- coding: utf-8 -*-
"""债市分析 · 命令行入口。

用法
----
    # 文字结论（默认）
    python -m commodities_strategies.strategies.bond_analysis.run

    # 机器可读 JSON（落 data/bond_analysis/state_YYYYMMDD.json）
    python -m commodities_strategies.strategies.bond_analysis.run --json

    # Markdown 报告 + 双栏图
    python -m commodities_strategies.strategies.bond_analysis.run --markdown --chart

    # 数据体检（不判定，只看新鲜度）
    python -m commodities_strategies.strategies.bond_analysis.run --status

    # 推飞书（卡片 or 纯文本）
    python -m commodities_strategies.strategies.bond_analysis.run --push
    python -m commodities_strategies.strategies.bond_analysis.run --push --text

    # 先刷 DataHub 再算（默认不刷，用库里已有数据）
    python -m commodities_strategies.strategies.bond_analysis.run --refresh
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from ...config import ROOT, load_settings
from . import engine as E
from . import report as RP
from .data import BondData

OUT = ROOT / "data" / "bond_analysis"


def _run_refresh() -> None:
    """调用 DataHub EDB 刷新债券+派生（不碰宏观月频慢指标）。"""
    from .data import connect
    edb = connect()
    if edb is None:
        print("[refresh] DataHub 不可用，跳过")
        return
    groups = {
        "bond": [f"BOND.CN.GOVT.{t}" for t in (
            "1M", "3M", "6M", "9M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "30Y")] +
        [f"BOND.CN.{seg}.{t}" for seg in ("MTN.AAA", "MTN.AAP", "MTN.AA", "MTN.AAL",
                                          "NCD.AAA", "T2C.AAA", "CDB")
         for t in ("1M", "3M", "6M", "9M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y")] +
        ["BOND.CN.CBINDEX", "BOND.CN.REPO.FR001", "BOND.CN.REPO.FR007",
         "BOND.CN.REPO.FDR001", "BOND.CN.REPO.FDR007",
         "BOND.CN.REPO.R001", "BOND.CN.REPO.R007",
         "BOND.CN.REPO.VOL.TOTAL", "BOND.CN.REPO.VOL.ONSHARE",
         "LIQ.CN.DR001", "LIQ.CN.DR007", "LIQ.CN.OMO.POLICY"],
        "spread": ["BOND.SPREAD.CN.MTN.AAA.3Y", "BOND.SPREAD.CN.MTN.AA.3Y",
                   "BOND.SPREAD.CN.NCD.1Y", "BOND.SPREAD.CN.T2C.5Y",
                   "BOND.SPREAD.CN.GOVT.10Y2Y", "BOND.SPREAD.CN.REPO.R_DR.007",
                   "BOND.SPREAD.CN.GOVT.10Y_POLICY", "BOND.SPREAD.CN.DR007_POLICY",
                   "BOND.SPREAD.CN.NCD1Y_POLICY"],
    }
    ids: List[str] = []
    for g in groups.values():
        for i in g:
            try:
                edb.get_def(i)
            except KeyError:
                continue
            if i not in ids:
                ids.append(i)
    # 基础先刷、派生后刷（派生依赖基础序列）
    base = [i for i in ids if not i.startswith("BOND.SPREAD.")]
    deriv = [i for i in ids if i.startswith("BOND.SPREAD.")]
    ok = fail = 0
    for i in base + deriv:
        r = edb.get_series_result(i, force_refresh=not i.startswith("BOND.SPREAD."))
        if r.get("ok"):
            ok += 1
        else:
            fail += 1
            print(f"  ✗ {i}: {str(r.get('error'))[:90]}")
    print(f"[refresh] 成功 {ok} / 失败 {fail}")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="bond_analysis", description="债市分析（四层判定）")
    ap.add_argument("--json", action="store_true", help="输出 JSON 快照并落盘")
    ap.add_argument("--markdown", action="store_true", help="输出 Markdown 报告并落盘")
    ap.add_argument("--chart", action="store_true", help="出双栏图（曲线 + 利差矩阵）")
    ap.add_argument("--status", action="store_true", help="只看数据新鲜度，不判定")
    ap.add_argument("--push", action="store_true", help="推飞书（默认卡片）")
    ap.add_argument("--text", action="store_true", help="推送用纯文本（配合 --push）")
    ap.add_argument("--push-webhook", default=None)
    ap.add_argument("--refresh", action="store_true", help="先刷 DataHub 再算")
    ap.add_argument("--asof", default=None, help="（预留）按指定日期复盘")
    args = ap.parse_args(argv)

    settings = load_settings()

    if args.refresh:
        _run_refresh()

    try:
        src = BondData(settings)
    except RuntimeError as exc:
        print(f"[错误] {exc}")
        return 2

    if args.status:
        df = src.freshness()
        stale = df[df["stale"] == True]  # noqa: E712
        print(df.to_string(index=False))
        if len(stale):
            print(f"\n⚠️ {len(stale)} 条滞后：{', '.join(stale['series_id'].tolist())}")
        return 0

    S = E.build_state(src)
    OUT.mkdir(parents=True, exist_ok=True)

    if args.chart:
        p = RP.build_chart(S, OUT / f"bond_{S.asof or 'latest'}.png")
        if p:
            print(f"[图] {p}")

    if args.json:
        p = OUT / f"state_{S.asof or 'latest'}.json"
        p.write_text(json.dumps(S.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[JSON] {p}")
        print(json.dumps(S.to_dict(), ensure_ascii=False, indent=1))
        return 0

    if args.markdown:
        p = OUT / f"report_{S.asof or 'latest'}.md"
        p.write_text(RP.build_markdown(S), encoding="utf-8")
        print(f"[Markdown] {p}")
        print(RP.build_markdown(S))
        return 0

    if args.push:
        if args.text:
            ok = RP.push_text_feishu(RP.build_text(S), args.push_webhook)
        else:
            ok = RP.push_feishu(RP.build_card(S), args.push_webhook)
        return 0 if ok else 1

    print(RP.build_text(S))
    return 0


if __name__ == "__main__":
    sys.exit(main())
