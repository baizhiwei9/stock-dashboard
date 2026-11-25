# -*- coding: utf-8 -*-
"""深度诊断：逐日比较 bt_ic_2023.py 与 bt_dxke_tol.py 的完整回测链路
比较信号、pos、权益、手数、每日盈亏，找出第一个差异日
"""
import os
import sys

BASEDIR = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f28eb4508ffeXvGpuDQNknTjQn/tech_consensus_daily"

sys.path.insert(0, BASEDIR)
sys.path.insert(0, os.path.join(BASEDIR, "signals"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import daily_views as DV  # noqa: E402
from signals import dxke_tol  # noqa: E402

# === 复制两个脚本的常量和函数 ===
INIT_CAP = 500_000.0
MULT = 200
MARGIN = 0.12
FEE_RATE = 0.000023
MAINT = 0.5
BT_START = "2023-01-01"


def load_ic():
    return pd.read_csv(
        os.path.join(BASEDIR, "data_ic", "ic_ohlcv_adj.csv"),
        parse_dates=["date"]
    ).set_index("date").sort_index()


def backtest_old(views, close):
    """bt_ic_2023.py 版本的 backtest_futures"""
    v = views.reindex(close.index).fillna(0).astype(int)
    pos = np.zeros(len(v), dtype=int)
    pos[1:] = v.values[:-1]
    eq = INIT_CAP
    equity = np.full(len(v), np.nan)
    segs, seg_pnl = [], []
    n_force = 0
    cur_side, cur_n, cur_entry = 0, 0, None
    n_pos_days = 0
    daily_pnl = np.zeros(len(v))
    px = close.values
    for i in range(len(v)):
        side = int(pos[i])
        p_now = px[i]
        if cur_side != 0 and side != cur_side:
            seg_pnl.append(daily_pnl[cur_entry:i].sum())
            segs.append((cur_entry, i, cur_side))
            eq -= cur_n * p_now * MULT * FEE_RATE
            cur_side, cur_n, cur_entry = 0, 0, None
        if side != 0 and cur_side == 0:
            margin_per = p_now * MULT * MARGIN
            n = max(1, int(eq // margin_per)) if eq > margin_per * MAINT else 0
            if n > 0:
                cur_side, cur_n, cur_entry = side, n, i
                eq -= n * p_now * MULT * FEE_RATE
        if cur_side != 0 and i > 0:
            pnl = cur_n * MULT * cur_side * (px[i] - px[i - 1])
            eq += pnl
            daily_pnl[i] = pnl
            n_pos_days += 1
            margin_used = cur_n * p_now * MULT * MARGIN
            if eq < margin_used * MAINT:
                eq = max(eq, 0.0)
                eq -= cur_n * p_now * MULT * FEE_RATE
                eq = max(eq, 0.0)
                seg_pnl.append(daily_pnl[cur_entry:i + 1].sum())
                segs.append((cur_entry, i + 1, cur_side))
                n_force += 1
                cur_side, cur_n, cur_entry = 0, 0, None
        equity[i] = eq
    if cur_side != 0:
        last = len(v)
        p_end = px[last - 1]
        eq -= cur_n * p_end * MULT * FEE_RATE
        eq = max(eq, 0.0)
        seg_pnl.append(daily_pnl[cur_entry:last].sum())
        segs.append((cur_entry, last, cur_side))
    return equity, pos, daily_pnl, segs, seg_pnl


def backtest_new(views, close, mult=200, margin=0.12):
    """bt_dxke_tol.py 版本的 backtest_futures"""
    v = views.reindex(close.index).fillna(0).astype(int)
    pos = np.zeros(len(v), dtype=int)
    pos[1:] = v.values[:-1]
    eq = INIT_CAP
    equity = np.full(len(v), np.nan)
    segs, seg_pnl = [], []
    n_force = 0
    cur_side, cur_n, cur_entry = 0, 0, None
    n_pos_days = 0
    daily_pnl = np.zeros(len(v))
    px = close.values
    for i in range(len(v)):
        side = int(pos[i])
        p_now = px[i]
        if cur_side != 0 and side != cur_side:
            seg_pnl.append(daily_pnl[cur_entry:i].sum())
            segs.append((cur_entry, i, cur_side))
            eq -= cur_n * p_now * mult * FEE_RATE
            cur_side, cur_n, cur_entry = 0, 0, None
        if side != 0 and cur_side == 0:
            margin_per = p_now * mult * margin
            n = max(1, int(eq // margin_per)) if eq > margin_per * MAINT else 0
            if n > 0:
                cur_side, cur_n, cur_entry = side, n, i
                eq -= n * p_now * mult * FEE_RATE
        if cur_side != 0 and i > 0:
            pnl = cur_n * mult * cur_side * (px[i] - px[i - 1])
            eq += pnl
            daily_pnl[i] = pnl
            n_pos_days += 1
            margin_used = cur_n * p_now * mult * margin
            if eq < margin_used * MAINT:
                eq = max(eq, 0.0)
                eq -= cur_n * p_now * mult * FEE_RATE
                eq = max(eq, 0.0)
                seg_pnl.append(daily_pnl[cur_entry:i + 1].sum())
                segs.append((cur_entry, i + 1, cur_side))
                n_force += 1
                cur_side, cur_n, cur_entry = 0, 0, None
        equity[i] = eq
    if cur_side != 0:
        last = len(v)
        p_end = px[last - 1]
        eq -= cur_n * p_end * mult * FEE_RATE
        eq = max(eq, 0.0)
        seg_pnl.append(daily_pnl[cur_entry:last].sum())
        segs.append((cur_entry, last, cur_side))
    return equity, pos, daily_pnl, segs, seg_pnl


def get_signal_old(df):
    """bt_ic_2023.py 的信号生成方式"""
    d = df.copy()
    d.insert(0, "date", d.index)
    s = DV.dxke_daily(d)
    s = s[s.index >= pd.Timestamp(BT_START)]
    return s


def get_signal_new(df):
    """bt_dxke_tol.py 的信号生成方式"""
    d = df.copy()
    d.insert(0, "date", d.index)
    base_sig = DV.dxke_daily(d)
    close = df["close"][BT_START:]
    base_sig = base_sig.reindex(close.index).fillna(0).astype(int)
    sig = dxke_tol.daily_views(base_sig, n_tol=1)
    sig = sig.reindex(close.index).fillna(0).astype(int)
    return sig, close


def main():
    df = load_ic()

    # === 对比 1: 数据加载 ===
    print("=" * 70)
    print("步骤1: 数据加载对比")
    print("=" * 70)
    close_old = df["close"]["2023"]
    sig_new, close_new = get_signal_new(df)
    print(f"旧版 close 长度: {len(close_old)}, 范围: {close_old.index[0]} ~ {close_old.index[-1]}")
    print(f"新版 close 长度: {len(close_new)}, 范围: {close_new.index[0]} ~ {close_new.index[-1]}")

    # 检查 close 是否相同
    if len(close_old) == len(close_new):
        close_diff = (close_old.values != close_new.values).sum()
        print(f"close 数值差异天数: {close_diff}")
    else:
        print("⚠️ close 长度不同！")
        # 对齐后比较
        merged = pd.merge(
            close_old.rename("old").reset_index(),
            close_new.rename("new").reset_index(),
            on="date", how="outer"
        )
        only_old = merged["old"].notna() & merged["new"].isna()
        only_new = merged["old"].isna() & merged["new"].notna()
        both = merged["old"].notna() & merged["new"].notna()
        val_diff = (merged.loc[both, "old"] != merged.loc[both, "new"]).sum()
        print(f"  仅旧版有: {only_old.sum()}, 仅新版有: {only_new.sum()}, 双方都有但值不同: {val_diff}")

    # === 对比 2: 信号生成 ===
    print()
    print("=" * 70)
    print("步骤2: 信号生成对比")
    print("=" * 70)
    sig_old = get_signal_old(df)
    print(f"旧版信号长度: {len(sig_old)}, 范围: {sig_old.index[0]} ~ {sig_old.index[-1]}")
    print(f"新版信号长度: {len(sig_new)}, 范围: {sig_new.index[0]} ~ {sig_new.index[-1]}")

    # 对齐比较信号
    merged_sig = pd.merge(
        sig_old.rename("old").reset_index(),
        sig_new.rename("new").reset_index(),
        on="date", how="outer"
    )
    both_sig = merged_sig["old"].notna() & merged_sig["new"].notna()
    sig_diff_mask = merged_sig.loc[both_sig, "old"] != merged_sig.loc[both_sig, "new"]
    print(f"信号对齐后重叠天数: {both_sig.sum()}")
    print(f"信号数值差异天数: {sig_diff_mask.sum()}")

    if sig_diff_mask.sum() > 0:
        diff_dates = merged_sig.loc[both_sig & sig_diff_mask, "date"]
        print(f"\n⚠️  信号差异日期（前5个）:")
        for dt in diff_dates.head(5):
            print(f"  {dt.date()}: old={merged_sig.loc[merged_sig['date']==dt, 'old'].values[0]}, new={merged_sig.loc[merged_sig['date']==dt, 'new'].values[0]}")

    # 统一 close 用于回测比较
    # 使用新版 close（因为新版信号已对齐到新版 close）
    common_dates = close_new.index
    sig_old_aligned = sig_old.reindex(common_dates).fillna(0).astype(int)
    sig_new_aligned = sig_new.reindex(common_dates).fillna(0).astype(int)

    # === 对比 3: pos 序列 ===
    print()
    print("=" * 70)
    print("步骤3: pos 序列对比 (T+1 执行)")
    print("=" * 70)
    pos_old = np.zeros(len(common_dates), dtype=int)
    pos_old[1:] = sig_old_aligned.values[:-1]
    pos_new = np.zeros(len(common_dates), dtype=int)
    pos_new[1:] = sig_new_aligned.values[:-1]

    pos_diff = (pos_old != pos_new).sum()
    print(f"pos 差异天数: {pos_diff}")
    if pos_diff > 0:
        diff_idx = np.where(pos_old != pos_new)[0]
        print(f"\n⚠️  pos 差异（前5个）:")
        for idx in diff_idx[:5]:
            dt = common_dates[idx]
            print(f"  {dt.date()}: pos_old={pos_old[idx]}, pos_new={pos_new[idx]}")
            # 打印前后3天上下文
            lo = max(0, idx - 3)
            hi = min(len(common_dates), idx + 4)
            print(f"    日期       旧信号  新信号  旧pos  新pos")
            for j in range(lo, hi):
                marker = " <<<" if j == idx else ""
                print(f"    {str(common_dates[j].date()):>10} {sig_old_aligned.iloc[j]:>6} {sig_new_aligned.iloc[j]:>6} {pos_old[j]:>6} {pos_new[j]:>6}{marker}")

    # === 对比 4: backtest_futures 逐日输出 ===
    print()
    print("=" * 70)
    print("步骤4: backtest_futures 逐日权益对比")
    print("=" * 70)

    eq_old, pos_seq_old, pnl_old, segs_old, seg_pnl_old = backtest_old(sig_old_aligned, close_new)
    eq_new, pos_seq_new, pnl_new, segs_new, seg_pnl_new = backtest_new(sig_new_aligned, close_new)

    eq_old_arr = np.array(eq_old)
    eq_new_arr = np.array(eq_new)

    # 比较权益
    eq_finite = np.isfinite(eq_old_arr) & np.isfinite(eq_new_arr)
    if eq_finite.sum() > 0:
        eq_diff = np.abs(eq_old_arr[eq_finite] - eq_new_arr[eq_finite])
        max_diff = eq_diff.max()
        first_diff_idx = np.where(eq_diff > 1e-6)[0]
        print(f"权益最大差异: {max_diff:.2f}")
        print(f"权益差异>1e-6 的天数: {len(first_diff_idx)}")

        if len(first_diff_idx) > 0:
            idx = first_diff_idx[0]
            dt = common_dates[idx]
            print(f"\n⚠️  第一个权益差异日: index={idx}, {dt.date()}")
            print(f"  旧版权益: {eq_old_arr[idx]:,.2f}, 新版权益: {eq_new_arr[idx]:,.2f}")
            print(f"  旧版pos: {pos_seq_old[idx]}, 新版pos: {pos_seq_new[idx]}")
            print(f"  旧版pnl: {pnl_old[idx]:,.2f}, 新版pnl: {pnl_new[idx]:,.2f}")

            # 打印前后5天的详细状态
            print(f"\n  前后5天详细对比:")
            print(f"  {'日期':>12} {'旧权益':>14} {'新权益':>14} {'旧pos':>6} {'新pos':>6} {'旧pnl':>12} {'新pnl':>12}")
            for j in range(max(0, idx - 5), min(len(common_dates), idx + 6)):
                marker = " <<<" if j == idx else ""
                print(f"  {str(common_dates[j].date()):>12} {eq_old_arr[j]:>14,.2f} {eq_new_arr[j]:>14,.2f} {pos_seq_old[j]:>6} {pos_seq_new[j]:>6} {pnl_old[j]:>12,.2f} {pnl_new[j]:>12,.2f}{marker}")

    # 总收益对比
    total_ret_old = eq_old_arr[-1] / INIT_CAP - 1 if len(eq_old_arr) > 0 else np.nan
    total_ret_new = eq_new_arr[-1] / INIT_CAP - 1 if len(eq_new_arr) > 0 else np.nan
    print(f"\n总收益对比:")
    print(f"  旧版: {total_ret_old * 100:.2f}%")
    print(f"  新版: {total_ret_new * 100:.2f}%")


if __name__ == "__main__":
    main()
