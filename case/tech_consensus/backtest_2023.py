# -*- coding: utf-8 -*-
"""2023 宽基指数股指期货情景回测（六方法统一口径）
统一规则:
  观点头: 多=全仓做多(+1)、空=全仓做空(-1)、震荡=空仓/平仓(0)
  信号 T+1 执行: t 日收盘信号生效 -> t+1 日持仓
  收益口径: 以对应宽基 ETF 日收益作为指数收益代理（名义本金 50 万，无杠杆）
  统计: 累计收益、年化、最大回撤、日胜率、笔胜率(按持仓段)、交易次数
"""
import os
import sys
import json

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "signals"))

import daily_views as DV  # noqa: E402

INIT_CAP = 500_000  # 50 万
BT_START = "2023-01-01"
BT_END = "2023-12-31"

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_out")
os.makedirs(OUT_DIR, exist_ok=True)


def load_etf(code):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", f"{code}.csv")
    df = pd.read_csv(path, parse_dates=["date"]).set_index("date").sort_index()
    return df


def backtest(views, close):
    """views: Series[index=date, values=1/-1/0/NaN]，close: 对应收盘价。
    信号 t 日收盘确定 -> t+1 执行；NaN 视为 0。
    """
    v = views.reindex(close.index).fillna(0).astype(int)
    pos = v.shift(1).fillna(0).astype(int)  # t+1 持仓 = t 日信号
    ret = close.pct_change()
    daily_pnl = pos * ret  # 名义 1 单位回报
    nav = (1 + daily_pnl).cumprod() * INIT_CAP
    dd = nav / nav.cummax() - 1.0

    # ---- 交易段统计（持仓状态变化分段）----
    seg_pnl, seg_start, seg_end, n_pos_days = [], [], [], 0
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
                seg_start.append(close.index[cur_start])
                seg_end.append(close.index[cur_end])
                cur_start = cur_end = None
    if cur_start is not None:
        seg_pnl.append(float((1 + daily_pnl.iloc[cur_start:cur_end + 1]).prod() - 1))
        seg_start.append(close.index[cur_start])
        seg_end.append(close.index[cur_end])

    # 平仓收益累计（若持仓段尚未结束，按当前浮盈计）
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
        "空仓天数": int((pos == 0).sum()),
        "多头天数": int((pos == 1).sum()),
        "空头天数": int((pos == -1).sum()),
        "期末净值": nav.iloc[-1],
    }


METHOD_LABELS = DV.METHODS


def views_for_code(code):
    """按 code 计算各方法逐日观点（回测区间内）"""
    df = load_etf(code)
    out = {}
    for mkey, (fn, label) in DV.METHODS.items():
        s = fn(df)
        s = s[s.index >= pd.Timestamp(BT_START)]
        out[mkey] = s
    return out


def run_one(code, start=None, end=None):
    df = load_etf(code)
    close = df["close"]
    bt = slice(None)
    if start:
        bt = close.index >= pd.Timestamp(start)
    if end:
        bt = bt & (close.index <= pd.Timestamp(end))
    close_bt = close[bt]
    views = views_for_code(code)
    metrics = {}
    for mkey, (fn, label) in DV.METHODS.items():
        metrics[mkey] = backtest(views[mkey], close_bt)
    return metrics


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default="sh510500")
    ap.add_argument("--start", default=BT_START)
    ap.add_argument("--end", default=BT_END)
    args = ap.parse_args()

    metrics = run_one(args.code, args.start, args.end)
    rows = []
    for mkey, (fn, label) in DV.METHODS.items():
        r = dict(metrics[mkey])
        r["方法"] = label
        rows.append(r)
    dfm = pd.DataFrame(rows).set_index("方法")
    dfm = dfm[["总收益%", "年化%", "最大回撤%", "日胜率%", "段胜率%", "交易段数",
               "多头天数", "空头天数", "空仓天数", "期末净值"]]
    print(f"\n===== {args.code}  {args.start} ~ {args.end}  初始 {INIT_CAP:,} =====")
    print(dfm.round(2).to_string())
    dfm.to_csv(os.path.join(OUT_DIR, f"bt_{args.code}_{args.start}_{args.end}.csv"))
    print(f"\n已保存: {OUT_DIR}/bt_{args.code}_{args.start}_{args.end}.csv")


if __name__ == "__main__":
    main()