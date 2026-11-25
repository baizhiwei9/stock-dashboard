# -*- coding: utf-8 -*-
"""诊断脚本：比较 DV.dxke_daily() 原始信号 vs dxke_tol.daily_views(base_sig, n_tol=1) 处理后信号
直接输出到 stdout，用于定位回测数值差异根因。
"""
import os
import sys

BASEDIR = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f28eb4508ffeXvGpuDQNknTjQn/tech_consensus_daily"

sys.path.insert(0, BASEDIR)
sys.path.insert(0, os.path.join(BASEDIR, "signals"))

import pandas as pd  # noqa: E402
import numpy as np   # noqa: E402
import daily_views as DV  # noqa: E402
from signals import dxke_tol  # noqa: E402


def main():
    df = pd.read_csv(
        os.path.join(BASEDIR, "data_ic", "ic_ohlcv_adj.csv"),
        parse_dates=["date"]
    ).set_index("date").sort_index()

    # 构造 daily_views 需要的格式
    d = df.copy()
    d.insert(0, "date", d.index)

    # 原始信号（2023年全段）
    base_sig = DV.dxke_daily(d)

    # 截取 2023 年交易日（与回测口径一致）
    bt_start = "2023-01-01"
    close = df["close"][bt_start:]
    base_sig = base_sig.reindex(close.index).fillna(0).astype(int)

    # 处理后信号（n_tol=1，理论上应与原版等价）
    processed_sig = dxke_tol.daily_views(base_sig, n_tol=1)
    processed_sig = processed_sig.reindex(close.index).fillna(0).astype(int)

    # 逐日对比
    diff_mask = base_sig != processed_sig
    diff_dates = base_sig.index[diff_mask]

    print(f"=" * 70)
    print(f"IC 2023 信号对比诊断")
    print(f"=" * 70)
    print(f"总交易日: {len(base_sig)}")
    print(f"原始信号非零天数: {(base_sig != 0).sum()}")
    print(f"处理后信号非零天数: {(processed_sig != 0).sum()}")
    print(f"差异天数: {diff_mask.sum()}")
    print()

    if diff_mask.sum() == 0:
        print("✅ 原始信号与处理后信号完全一致（n_tol=1 等价于原版）。")
        print("   差异不在 dxke_tol.daily_views，请检查 backtest_futures 中的其他逻辑。")
        return

    print(f"⚠️  发现 {diff_mask.sum()} 处差异，逐日打印上下文（前后各3天）：\n")

    for dt in diff_dates:
        idx = base_sig.index.get_loc(dt)
        lo = max(0, idx - 3)
        hi = min(len(base_sig), idx + 4)

        print(f"{'─' * 70}")
        print(f"差异日: {dt.date()}  (index={idx})")
        print(f"  原始信号:      {base_sig.iloc[idx]}")
        print(f"  处理后信号:    {processed_sig.iloc[idx]}")
        print()
        print(f"{'日期':>12} {'原始':>6} {'处理后':>6}")
        for j in range(lo, hi):
            marker = "  <<<" if j == idx else ""
            print(f"{str(base_sig.index[j].date()):>12} {base_sig.iloc[j]:>6} {processed_sig.iloc[j]:>6}{marker}")
        print()

    # 汇总统计
    print(f"{'=' * 70}")
    print("差异统计:")
    print(f"{'=' * 70}")
    raw_nonzero = base_sig[base_sig != 0]
    proc_nonzero = processed_sig[processed_sig != 0]
    print(f"原始信号:   多头 {(raw_nonzero == 1).sum()} 天, 空头 {(raw_nonzero == -1).sum()} 天")
    print(f"处理后信号: 多头 {(proc_nonzero == 1).sum()} 天, 空头 {(proc_nonzero == -1).sum()} 天")

    # 进一步：找出信号变 0 或从 0 变非 0 的模式
    changed_to_flat = diff_mask & (processed_sig == 0) & (base_sig != 0)
    changed_from_flat = diff_mask & (base_sig == 0) & (processed_sig != 0)
    print(f"原始非0 -> 处理后 0 : {changed_to_flat.sum()} 天")
    print(f"原始 0   -> 处理后非0: {changed_from_flat.sum()} 天")
    print(f"其他（双方均非0但不同）: {(diff_mask & (~changed_to_flat) & (~changed_from_flat)).sum()} 天")


if __name__ == "__main__":
    main()
