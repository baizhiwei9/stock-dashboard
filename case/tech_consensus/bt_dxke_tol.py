# -*- coding: utf-8 -*-
"""动力学MACD 加速度容忍窗口对比: N=1(原版) / N=2 / N=3
覆盖 2023 IC 中证500 与 2026 AG 白银期货
回测引擎与 bt_ic_2023.backtest_futures 逐行同构
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "signals"))

import daily_views as DV
from signals import dxke_tol

INIT_CAP = 500_000.0
FEE_RATE = 0.000023
MAINT = 0.5
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "backtest_out")
os.makedirs(OUT, exist_ok=True)


def load_ic():
    return pd.read_csv(os.path.join(HERE, "data_ic", "ic_ohlcv_adj.csv"),
                       parse_dates=["date"]).set_index("date").sort_index()


def load_ag():
    return pd.read_csv(os.path.join(HERE, "data_ag", "ag_ohlcv_adj.csv"),
                       parse_dates=["date"]).set_index("date").sort_index()


def backtest_futures(views, close, mult, margin):
    """与 bt_ic_2023.backtest_futures 逐行同构, 支持任意 mult/margin"""
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
        # ---- 平旧仓: 目标0或反向 ----
        if cur_side != 0 and side != cur_side:
            seg_pnl.append(daily_pnl[cur_entry:i].sum())
            segs.append((cur_entry, i, cur_side))
            eq -= cur_n * p_now * mult * FEE_RATE
            cur_side, cur_n, cur_entry = 0, 0, None
        # ---- 开新仓: 目标非0且空仓 ----
        if side != 0 and cur_side == 0:
            margin_per = p_now * mult * margin
            n = max(1, int(eq // margin_per)) if eq > margin_per * MAINT else 0
            if n > 0:
                cur_side, cur_n, cur_entry = side, n, i
                eq -= n * p_now * mult * FEE_RATE
        # ---- 盯市盈亏 ----
        if cur_side != 0 and i > 0:
            pnl = cur_n * mult * cur_side * (px[i] - px[i - 1])
            eq += pnl
            daily_pnl[i] = pnl
            n_pos_days += 1
            # 强平检查
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

    # 期末结算
    if cur_side != 0:
        last = len(v)
        p_end = px[last - 1]
        eq -= cur_n * p_end * mult * FEE_RATE
        eq = max(eq, 0.0)
        seg_pnl.append(daily_pnl[cur_entry:last].sum())
        segs.append((cur_entry, last, cur_side))

    nav = pd.Series(equity, index=close.index)
    dd = nav / nav.cummax() - 1.0
    total_ret = float(nav.iloc[-1] / INIT_CAP - 1)
    n_days = len(nav)
    ann = (nav.iloc[-1] / INIT_CAP) ** (252 / n_days) - 1 if n_days > 0 else np.nan
    wins = sum(1 for s in seg_pnl if s > 0)
    seg_win = wins / len(seg_pnl) if seg_pnl else np.nan
    exposed = daily_pnl != 0
    day_win = float((daily_pnl[exposed] > 0).mean()) if exposed.sum() else np.nan

    return {
        "总收益%": total_ret * 100,
        "年化%": ann * 100,
        "最大回撤%": float(dd.min()) * 100,
        "日胜率%": (day_win if not pd.isna(day_win) else np.nan) * 100,
        "段胜率%": (seg_win if not pd.isna(seg_win) else np.nan) * 100,
        "交易段数": len(seg_pnl),
        "持仓天数": n_pos_days,
        "多头天数": int((pos == 1).sum()),
        "空头天数": int((pos == -1).sum()),
        "空仓天数": int((pos == 0).sum()),
        "强平次数": n_force,
        "期末权益": nav.iloc[-1],
    }, nav, segs


def run_comparison(df, bt_start, bt_end, mult, margin, label):
    d = df.copy()
    d.insert(0, "date", d.index)
    base_sig = DV.dxke_daily(d)
    close = df["close"][bt_start:bt_end]
    base_sig = base_sig.reindex(close.index).fillna(0).astype(int)

    rows, navs, all_segs = [], {}, {}
    for n in [1, 2, 3]:
        sig = dxke_tol.daily_views(base_sig, n_tol=n)
        sig = sig.reindex(close.index).fillna(0).astype(int)
        m, nav, segs = backtest_futures(sig, close, mult, margin)
        m["N_tol"] = n
        rows.append(m)
        navs[n] = nav
        all_segs[n] = segs

    dfm = pd.DataFrame(rows)
    dfm = dfm[["N_tol", "总收益%", "年化%", "最大回撤%", "日胜率%", "段胜率%",
               "交易段数", "持仓天数", "多头天数", "空头天数", "空仓天数", "强平次数", "期末权益"]]
    print(f"\n===== {label} 动力学MACD 加速度容忍对比 (50万本金) =====")
    print(dfm.round(2).to_string(index=False))
    return dfm, navs, all_segs, close


def main():
    # ---- IC 2023 ----
    df_ic = load_ic()
    dfm_ic, navs_ic, segs_ic, close_ic = run_comparison(
        df_ic, "2023-01-01", "2023-12-31", mult=200, margin=0.12, label="2023 IC"
    )
    dfm_ic.to_csv(os.path.join(OUT, "dxke_tol_ic2023.csv"), index=False, encoding="utf-8-sig")

    # ---- AG 2026 ----
    df_ag = load_ag()
    dfm_ag, navs_ag, segs_ag, close_ag = run_comparison(
        df_ag, "2026-01-01", "2026-12-31", mult=15, margin=0.12, label="2026 AG"
    )
    dfm_ag.to_csv(os.path.join(OUT, "dxke_tol_ag2026.csv"), index=False, encoding="utf-8-sig")

    # ---- 汇总 ----
    print("\n\n===== 跨品种对比汇总 =====")
    print("\n--- 2023 IC (中证500期货, 乘数200, 基准全年-6.4%) ---")
    print(dfm_ic.round(2).to_string(index=False))
    print("\n--- 2026 AG (白银期货, 乘数15, 基准全年-1.7%) ---")
    print(dfm_ag.round(2).to_string(index=False))

    # 段明细打印
    print("\n\n===== IC2023 空头段明细 (N=1 vs N=3) =====")
    for n in [1, 3]:
        print(f"\n--- N={n} ---")
        segs = segs_ic[n]
        for start, end, side in segs:
            if side == -1:
                entry = close_ic.iloc[start]
                exit = close_ic.iloc[end - 1] if end <= len(close_ic) else close_ic.iloc[-1]
                delta = (exit - entry) / entry * 100
                days = end - start
                print(f"  空 {str(close_ic.index[start].date())} -> {str(close_ic.index[min(end-1, len(close_ic)-1)].date())} "
                      f"({days}天)  entry={entry:.2f} exit={exit:.2f} delta={delta:+.2f}%")

    print("\n\n===== AG2026 空头段明细 (N=1 vs N=3) =====")
    for n in [1, 3]:
        print(f"\n--- N={n} ---")
        segs = segs_ag[n]
        for start, end, side in segs:
            if side == -1:
                entry = close_ag.iloc[start]
                exit = close_ag.iloc[end - 1] if end <= len(close_ag) else close_ag.iloc[-1]
                delta = (exit - entry) / exit * 100
                days = end - start
                print(f"  空 {str(close_ag.index[start].date())} -> {str(close_ag.index[min(end-1, len(close_ag)-1)].date())} "
                      f"({days}天)  entry={entry:.2f} exit={exit:.2f} delta={delta:+.2f}%")


if __name__ == "__main__":
    main()
