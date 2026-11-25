"""bond_strategies 命令行入口。

    python -m commodities_strategies.bond_strategies.run            # 只读 EDB 出周报
    python -m commodities_strategies.bond_strategies.run --update   # 先增量刷新 EDB 债券通道
    python -m commodities_strategies.bond_strategies.run --push     # 出周报并按条件推送飞书/邮件
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from ..config import ROOT, load_settings
from . import dataset, edb, factors, forecast, notify, report, slope


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="bond_strategies",
                                 description="利率三因子周度预测 + Slope 交易机会（数据经 DataHub EDB）")
    ap.add_argument("--update", action="store_true", help="运行前刷新 EDB 债券通道")
    ap.add_argument("--force", action="store_true", help="忽略 EDB 通道 TTL 强制刷新")
    ap.add_argument("--out", default=str(ROOT / "reports"), help="报告输出目录")
    ap.add_argument("--no-write", action="store_true", help="只打印不落盘")
    ap.add_argument("--push", action="store_true",
                    help="按旧版规则推送：Slope 机会或明确买卖方向时推飞书，Slope 机会时另发邮件告警")
    ap.add_argument("--push-dry", action="store_true", help="只打印待推送卡片 JSON，不实际发送")
    ap.add_argument("--seed", default="42", help="XGB 随机种子；传 none 复现旧版无种子行为")
    a = ap.parse_args(argv)

    settings = load_settings()
    mod, kind = edb.load(settings)
    if mod is None:
        print("[fail] 未找到可用的 DataHub EDB；请设置 DATAHUB_ROOT 或克隆 datahub-multi-tier")
        return 1
    print(f"[edb] {getattr(mod, '__file__', kind)} ({kind})")

    if a.update:
        for ch, st in edb.update(settings, force=a.force).items():
            print(f"[edb] {ch}: {st}")

    y = dataset.load_yields(settings)
    mx, yw = dataset.build_weekly(settings)
    print(f"[ok] 周频样本 {len(yw)} 周 ({yw.index.min().date()} ~ {yw.index.max().date()})")

    F, L, MU = factors.rolling_pca(yw, dataset.TERMS)
    seed = None if str(a.seed).lower() in ("none", "null", "") else int(a.seed)
    pred_f, y_next = forecast.predict_next(F, L, MU, mx, seed=seed)
    latest = yw[dataset.FOCUS].iloc[-1]
    pred = pd.Series(y_next, index=dataset.TERMS)[dataset.FOCUS]
    times = forecast.actions(latest, pred)

    _, row = slope.compute(dataset.load_yields(settings, filled=True), dataset.load_dr007(settings))
    signal, note = slope.judge(row)

    date_str = y.index.max().strftime("%Y-%m-%d")
    next_week = (yw.index.max() + pd.Timedelta(days=7)).strftime("%Y-%m-%d")
    near = slope.near_zone(row)
    md = report.gen(date_str, latest, pred, next_week, forecast.HIT,
                    [F.iloc[-1].values, pred_f], row, signal, note, near)

    print(f"[Slope] {note}")
    for t, v in times.items():
        print(f"[pred] {t}: {float(latest[t]):.4f}% -> {float(pred[t]):.4f}% ({v['bp']:+.1f}bp) {v['action']}")

    if a.no_write:
        print(md)
        return 0

    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"利率三因子周度预测和Slope交易周报_{date_str}.md"
    path.write_text(md, encoding="utf-8")
    actives = {t: v["action"] for t, v in times.items()}
    should_push = bool(signal) or any(v != "观望" for v in actives.values())
    state = {
        "date": date_str, "next_week": next_week, "source": "datahub_edb",
        "slope_signal": signal, "slope_note": note,
        "slope_SPREAD210": float(row["SPREAD210"]),
        "slope_SPREAD_PCT": float(row["SPREAD_PCT"]),
        "slope_DR007_DIR": float(row["DR007_DIR"]),
        "slope_RESID_PCT": float(row["RESID_PCT"]),
        "yields_cur": {t: round(float(latest[t]), 4) for t in dataset.FOCUS},
        "yields_next": {t: round(float(pred[t]), 4) for t in dataset.FOCUS},
        "pred_actions": actives,
        "hit": forecast.HIT, "near": near, "should_push": should_push,
        "report_path": str(path),
    }
    (out_dir / "slope_scan_state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ok] 报告已生成: {path}")
    print(f"[push] {'需要推送' if should_push else '无需推送（无 Slope 机会且预测均观望）'}")

    if a.push_dry:
        print(notify.push_card_json(state))
    elif a.push and should_push:
        for ch, msg in notify.push_all(str(path), state).items():
            print(f"[push] {ch}: {msg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())