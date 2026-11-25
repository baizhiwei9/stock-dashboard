# -*- coding: utf-8 -*-
"""动力学MACD 方向+权重策略回测
方向由 v+S50 决定: v>0&C>S50=多; v<0&C<S50=空
权重由 a 决定: a同向=全仓; a反向=1/3仓
与 bt_ic_2023.py 逐行同构
"""
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "signals"))

from signals import dxke_weight
from bt_ic_2023 import load_ic

INIT_CAP = 500_000.0
MULT = 200
MARGIN = 0.12
FEE_RATE = 0.000023
MAINT = 0.5
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "backtest_out")
os.makedirs(OUT, exist_ok=True)


def backtest_weighted(weights, close):
    """weights: 连续权重序列(-1.0..1.0), t日设计->t+1执行
    手数=floor(权益/单手保证金)*|权重|, 持仓期手数不变, 同向等级切换按差值收手续费
    """
    w = weights.reindex(close.index).fillna(0.0)
    pos = np.zeros(len(w))
    pos[1:] = w.values[:-1]

    eq = INIT_CAP
    equity = np.full(len(w), np.nan)
    segs, seg_pnl = [], []
    n_force = 0
    cur_side, cur_n, cur_n_full, cur_entry = 0, 0, 0, None
    n_pos_days = 0
    daily_pnl = np.zeros(len(w))
    px = close.values

    for i in range(len(w)):
        side = float(pos[i])
        p_now = px[i]
        t_sign = 1 if side > 0 else (-1 if side < 0 else 0)
        t_level = abs(side)

        # ---- 平旧仓: 目标归零或反向 ----
        if cur_side != 0 and (t_sign == 0 or t_sign != cur_side):
            seg_pnl.append(daily_pnl[cur_entry:i].sum())
            segs.append((cur_entry, i, cur_side))
            eq -= cur_n * p_now * MULT * FEE_RATE
            eq = max(eq, 0.0)
            cur_side, cur_n, cur_n_full, cur_entry = 0, 0, 0, None
            t_level_target = 0
        else:
            t_level_target = t_level

        # ---- 开新仓: 目标非0且空仓 ----
        if t_sign != 0 and cur_side == 0:
            margin_per = p_now * MULT * MARGIN
            n_full = int(eq // margin_per) if eq > margin_per * MAINT else 0
            if n_full > 0:
                n = max(1, int(round(n_full * t_level)))
                cur_side, cur_n, cur_n_full, cur_entry = t_sign, n, n_full, i
                eq -= n * p_now * MULT * FEE_RATE
                eq = max(eq, 0.0)

        # ---- 持仓中全仓<->1/3仓等级切换: 按基准全仓手数差值调仓 ----
        elif t_sign != 0 and cur_side != 0 and cur_n_full > 0:
            target_n = max(1, int(round(cur_n_full * t_level)))
            if target_n != cur_n:
                diff = abs(target_n - cur_n)
                eq -= diff * p_now * MULT * FEE_RATE
                eq = max(eq, 0.0)
                cur_n = target_n

        # ---- 盯市 ----
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
                cur_side, cur_n, cur_n_full, cur_entry = 0, 0, 0, None
        equity[i] = eq

    # 期末结算
    if cur_side != 0:
        p_end = px[-1]
        eq -= cur_n * p_end * MULT * FEE_RATE
        eq = max(eq, 0.0)
        seg_pnl.append(daily_pnl[cur_entry:len(w)].sum())
        segs.append((cur_entry, len(w), cur_side))

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
        "日胜率%": (day_win * 100 if not pd.isna(day_win) else np.nan),
        "段胜率%": (seg_win * 100 if not pd.isna(seg_win) else np.nan),
        "交易段数": len(seg_pnl),
        "持仓天数": n_pos_days,
        "强平次数": n_force,
        "期末权益": nav.iloc[-1],
    }, nav, segs


def main():
    df = load_ic()
    d = df.copy()
    d.insert(0, "date", d.index)
    close23 = df["close"]["2023"]

    variants = {
        "原版(方向+v+a)": None,
        "方向v+S50, a同向全仓/反向1/3": 0.33,
        "方向v+S50, a同向全仓/反向1/4": 0.25,
        "方向v+S50, a同向全仓/反向1/5": 0.20,
    }
    rows = []
    for name, flat_w in variants.items():
        if flat_w is None:
            # 原版
            import daily_views as DV
            s = DV.dxke_daily(d)
            s23 = s.reindex(close23.index).fillna(0).astype(int)
            # 用原版回测
            from bt_ic_2023 import backtest_futures
            m, nav, segs = backtest_futures(s23, close23)
        else:
            w = dxke_weight.daily_views(d, flat_weight=flat_w)
            w23 = w.reindex(close23.index).fillna(0.0)
            m, nav, segs = backtest_weighted(w23, close23)
        m["方案"] = name
        rows.append(m)

    dfm = pd.DataFrame(rows)
    dfm = dfm[["方案", "总收益%", "年化%", "最大回撤%", "日胜率%", "段胜率%",
              "交易段数", "持仓天数", "强平次数", "期末权益"]]
    print("===== 2023 IC 动力学MACD 方向+权重对比 (50万本金) =====")
    print(dfm.round(2).to_string(index=False))
    dfm.to_csv(os.path.join(OUT, "dxke_direction_weight.csv"), index=False, encoding="utf-8-sig")

    # 列各方案空头段
    for name, flat_w in variants.items():
        if flat_w is None:
            continue
        w = dxke_weight.daily_views(d, flat_weight=flat_w)
        w23 = w.reindex(close23.index).fillna(0.0)
        m, nav, segs = backtest_weighted(w23, close23)
        short_segs = [(s, e, si) for s, e, si in segs if si == -1]
        print(f"\n===== {name} 空头段明细 =====")
        for start_i, end_i, _ in short_segs:
            entry = close23.iloc[start_i]
            exit = close23.iloc[min(end_i - 1, len(close23) - 1)]
            days = end_i - start_i
            delta = (exit - entry) / entry * 100
            print(f"  空 {str(close23.index[start_i].date())} -> {str(close23.index[min(end_i-1, len(close23)-1)].date())} "
                  f"({days}天) {entry:.0f}->{exit:.0f} ({delta:+.2f}%)")


if __name__ == "__main__":
    main()
