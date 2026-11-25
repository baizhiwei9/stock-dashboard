# -*- coding: utf-8 -*-
"""dxke_bidirect 新旧开仓逻辑对比回测
1. 科创50 ETF 2026 只做多（etf_views vs old daily_views 只做多）
2. IC 2023 期货多空双向（futures_views vs old daily_views）
3. AG 2026 期货多空双向（futures_views vs old daily_views）
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "signals"))

from signals import dxke_bidirect as DX

INIT_CAP = 500_000.0
FEE_RATE = 0.000023
MAINT = 0.5
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "backtest_out")
os.makedirs(OUT, exist_ok=True)


# ============== 数据加载 ==============

def load_etf(code):
    path = os.path.join(HERE, "data", f"{code}.csv")
    df = pd.read_csv(path, parse_dates=["date"]).set_index("date").sort_index()
    return df


def load_ic():
    return pd.read_csv(os.path.join(HERE, "data_ic", "ic_ohlcv_adj.csv"),
                       parse_dates=["date"]).set_index("date").sort_index()


def load_ag():
    return pd.read_csv(os.path.join(HERE, "data_ag", "ag_ohlcv_adj.csv"),
                       parse_dates=["date"]).set_index("date").sort_index()


# ============== 回测引擎 ==============

def backtest_etf(views, close):
    """ETF无杠杆回测: T+1执行, 50万本金, 无手续费"""
    v = views.reindex(close.index).fillna(0).astype(int)
    pos = v.shift(1).fillna(0).astype(int)
    ret = close.pct_change()
    daily_pnl = pos * ret
    nav = (1 + daily_pnl).cumprod() * INIT_CAP
    dd = nav / nav.cummax() - 1.0

    seg_pnl, n_pos_days = [], 0
    cur_start, cur_end = None, None
    exposed = pos != 0
    for i in range(len(pos)):
        p = int(pos.iloc[i])
        if p != 0:
            n_pos_days += 1
            if cur_start is None:
                cur_start = i
                cur_end = i
            else:
                cur_end = i
        else:
            if cur_start is not None:
                seg_pnl.append(float((1 + daily_pnl.iloc[cur_start:cur_end + 1]).prod() - 1))
                cur_start = cur_end = None
    if cur_start is not None:
        seg_pnl.append(float((1 + daily_pnl.iloc[cur_start:cur_end + 1]).prod() - 1))

    wins = sum(1 for s in seg_pnl if s > 0)
    seg_win_rate = wins / len(seg_pnl) if seg_pnl else np.nan
    daily_win_rate = float((daily_pnl[exposed] > 0).mean()) if exposed.sum() else np.nan

    total_ret = float(nav.iloc[-1] / INIT_CAP - 1)
    n_days = len(nav)
    ann_ret = (nav.iloc[-1] / INIT_CAP) ** (252 / n_days) - 1 if n_days > 0 else np.nan
    max_dd = float(dd.min())

    return {
        "总收益%": total_ret * 100,
        "年化%": ann_ret * 100,
        "最大回撤%": max_dd * 100,
        "日胜率%": (daily_win_rate if not pd.isna(daily_win_rate) else np.nan) * 100,
        "段胜率%": (seg_win_rate if not pd.isna(seg_win_rate) else np.nan) * 100,
        "交易段数": len(seg_pnl),
        "持仓天数": n_pos_days,
        "多头天数": int((pos == 1).sum()),
        "空头天数": int((pos == -1).sum()),
        "空仓天数": int((pos == 0).sum()),
        "期末净值": nav.iloc[-1],
    }


def backtest_futures(views, close, mult, margin):
    """期货回测: 与 bt_ic_2023.backtest_futures 同构"""
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
    }


# ============== 运行对比 ==============

def run():
    results = []

    # ---------- 1. 科创50 ETF 2026 只做多 ----------
    print("\n===== 科创50 ETF 2026 只做多 =====")
    df_kcs = load_etf("sh588000")
    df_kcs_26 = df_kcs.loc["2026-01-01":"2026-12-31"].copy()
    close_26 = df_kcs_26["close"]

    # 旧版: 只做多（-1信号归零）
    old_views = DX.daily_views(df_kcs_26)
    old_views_long = old_views.copy()
    old_views_long[old_views_long == -1] = 0
    m_old = backtest_etf(old_views_long, close_26)
    m_old["方案"] = "旧版bidirect(只取多头)"
    m_old["标的"] = "科创50ETF"
    m_old["区间"] = "2026"
    results.append(m_old)

    # 新版: etf_views
    new_views = DX.etf_views(df_kcs_26)
    m_new = backtest_etf(new_views, close_26)
    m_new["方案"] = "新版etf_views(只做多)"
    m_new["标的"] = "科创50ETF"
    m_new["区间"] = "2026"
    results.append(m_new)

    # ---------- 2. IC 2023 期货多空双向 ----------
    print("\n===== IC 2023 期货多空双向 =====")
    df_ic = load_ic()
    close_ic23 = df_ic["close"]["2023"]

    # 旧版
    old_views_ic = DX.daily_views(df_ic)
    m_old_ic = backtest_futures(old_views_ic, close_ic23, mult=200, margin=0.12)
    m_old_ic["方案"] = "旧版bidirect(双向)"
    m_old_ic["标的"] = "IC"
    m_old_ic["区间"] = "2023"
    results.append(m_old_ic)

    # 新版
    new_views_ic = DX.futures_views(df_ic)
    m_new_ic = backtest_futures(new_views_ic, close_ic23, mult=200, margin=0.12)
    m_new_ic["方案"] = "新版futures_views(双向)"
    m_new_ic["标的"] = "IC"
    m_new_ic["区间"] = "2023"
    results.append(m_new_ic)

    # ---------- 3. AG 2026 期货多空双向 ----------
    print("\n===== AG 2026 期货多空双向 =====")
    df_ag = load_ag()
    close_ag26 = df_ag["close"]["2026"]

    # 旧版
    old_views_ag = DX.daily_views(df_ag)
    m_old_ag = backtest_futures(old_views_ag, close_ag26, mult=15, margin=0.12)
    m_old_ag["方案"] = "旧版bidirect(双向)"
    m_old_ag["标的"] = "AG"
    m_old_ag["区间"] = "2026"
    results.append(m_old_ag)

    # 新版
    new_views_ag = DX.futures_views(df_ag)
    m_new_ag = backtest_futures(new_views_ag, close_ag26, mult=15, margin=0.12)
    m_new_ag["方案"] = "新版futures_views(双向)"
    m_new_ag["标的"] = "AG"
    m_new_ag["区间"] = "2026"
    results.append(m_new_ag)

    # ---------- 汇总输出 ----------
    cols = ["方案", "标的", "区间", "总收益%", "年化%", "最大回撤%", "交易段数",
            "持仓天数", "多头天数", "空头天数", "空仓天数", "日胜率%", "段胜率%",
            "强平次数", "期末净值"]
    dfm = pd.DataFrame(results)[cols]
    print("\n\n===== 新旧开仓逻辑对比汇总 =====")
    print(dfm.round(2).to_string(index=False))
    dfm.to_csv(os.path.join(OUT, "dxke_bidirect_comparison.csv"), index=False, encoding="utf-8-sig")
    print(f"\n已保存: {OUT}/dxke_bidirect_comparison.csv")

    return dfm


if __name__ == "__main__":
    run()
