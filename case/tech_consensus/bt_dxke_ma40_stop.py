# -*- coding: utf-8 -*-
"""动力学MACD MA40方向 + 三项强平（引擎层 v3: 实际成交口径）
成交口径（用户 2026-09 明确: 3%从入场价起算, 入场价=实际成交价）:
  T 日收盘出信号 -> T+1 日以 T+1 收盘价实际成交(开仓当日无浮动)
  T+2 起逐日结算 (px[i]-px[i-1])
止损（T 日收盘触发 -> T+1 执行平仓, 平仓段含 T+1 当日已结算盈亏）:
  ① 2倍ATR14: 多头收盘<entry-2*ATR(成交日), 空头收盘>entry+2*ATR
  ② 3%回撤:   多头收盘<entry*0.97, 空头收盘>entry*1.03
  ③ MA40突破: 多头收盘<MA40当日, 空头收盘>MA40当日
止损执行日不再重开; use_stop=False 时＝纯实际成交口径(旧口径+剔除信号日跳空归属)
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "signals"))

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


def ma40_views(df):
    """MA40 方向 + 动能/势能象限信号（不含止损）"""
    c = df["close"]
    e12 = c.ewm(span=12, adjust=False).mean()
    e26 = c.ewm(span=26, adjust=False).mean()
    dif = e12 - e26
    dea = dif.ewm(span=9, adjust=False).mean()
    hist = dif - dea
    h, l = df["high"], df["low"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    atr = tr.ewm(span=14, adjust=False).mean()
    ma40 = c.rolling(40).mean()

    v = dif / atr
    a = hist / atr
    views = np.full(len(df), 0)
    for i in range(len(df)):
        if np.isnan(v.iloc[i]) or np.isnan(a.iloc[i]) or np.isnan(ma40.iloc[i]):
            views[i] = 0
        elif c.iloc[i] > ma40.iloc[i] and v.iloc[i] > 0 and a.iloc[i] > 0:
            views[i] = 1
        elif c.iloc[i] < ma40.iloc[i] and v.iloc[i] < 0 and a.iloc[i] < 0:
            views[i] = -1
    return pd.Series(views, index=df.index), pd.DataFrame(
        {"open": df["open"], "high": df["high"], "low": df["low"],
         "close": c, "ma40": ma40, "atr14": atr}, index=df.index)


def backtest_futures(views, df, mult, margin, use_stop=True, stop_same_day=False, stop_intraday=False):
    """v3 实际成交口径 + 三项强平。df 含 close/ma40/atr14/high/low/open
    stop_same_day=False: T 日收盘触发止损 -> T+1 执行平仓（历史口径）
    stop_same_day=True : T 日收盘触发止损 -> 当日收盘即强平
    stop_intraday=True : 盘中触及止损位即平（回补/跳空开盘越过止损位时按开盘价成交）
    """
    close = df["close"]
    if "high" in df.columns and "low" in df.columns:
        high, low = df["high"], df["low"]
        op = df["open"] if "open" in df.columns else close
    else:  # 兜底: 用 close 近似（仅当信号封装未带OHLC时）
        high = low = op = close
    ma40 = df["ma40"]
    atr = df["atr14"]
    px = close.values
    hi, lo, op = high.values, low.values, op.values
    v = views.reindex(close.index).fillna(0).astype(int)

    pos = np.zeros(len(v), dtype=int)
    pos[1:] = v.values[:-1]  # T 日信号 -> T+1 持仓日

    eq = INIT_CAP
    equity = np.full(len(v), np.nan)
    segs, seg_pnl, seg_reason = [], [], []
    n_stop = {"2atr": 0, "3pct": 0, "ma40": 0, "margin": 0}
    stop_reason = [""] * len(v)
    cur_side, cur_n, cur_entry = 0, 0, None
    entry_px = None
    stop_2atr = stop_3pct = None
    daily_pnl = np.zeros(len(v))
    prev_stop = None

    def _close_seg(i, cause, charge=True):
        """段结算：记录段盈亏、平仓原因；charge=True 时收平仓手续费"""
        nonlocal eq
        seg_pnl.append(daily_pnl[cur_entry:(i + 1)].sum())
        segs.append((cur_entry, i + 1, cur_side))
        seg_reason.append(cause)
        if charge:
            eq -= cur_n * p_now * FEE_RATE * mult
            eq = max(eq, 0.0)
        if cause in n_stop:
            n_stop[cause] += 1
        stop_reason[i] = cause

    for i in range(len(v)):
        side = int(pos[i])
        p_now = px[i]

        # ---- 1) 结算当日盈亏（成交日 T+1 无浮动, T+2 起）----
        if cur_side != 0 and i > 0 and (cur_entry is None or i > cur_entry):
            pnl = cur_n * cur_side * (px[i] - px[i - 1]) * mult
            eq += pnl
            daily_pnl[i] = pnl

        # ---- 2) 当日止损判定（stop_same_day=True / stop_intraday=True）----
        hit_stop = None
        fill_px = None
        if cur_side != 0 and use_stop and entry_px is not None and (
                (stop_same_day or stop_intraday) and i > (cur_entry or -1)):
            if stop_intraday:
                if cur_side == 1:
                    floor3 = max(stop_2atr, stop_3pct)
                    if not pd.isna(floor3) and lo[i] <= floor3:
                        hit_stop = "3pct" if stop_3pct >= stop_2atr else "2atr"
                        fill_px = op[i] if op[i] <= floor3 else floor3
                    elif lo[i] <= ma40.iloc[i]:
                        hit_stop = "ma40"
                        fill_px = op[i] if op[i] <= ma40.iloc[i] else ma40.iloc[i]
                else:
                    ceil3 = min(stop_2atr, stop_3pct)
                    if not pd.isna(ceil3) and hi[i] >= ceil3:
                        hit_stop = "3pct" if stop_3pct <= stop_2atr else "2atr"
                        fill_px = op[i] if op[i] >= ceil3 else ceil3
                    elif hi[i] >= ma40.iloc[i]:
                        hit_stop = "ma40"
                        fill_px = op[i] if op[i] >= ma40.iloc[i] else ma40.iloc[i]
            else:
                if cur_side == 1:
                    if p_now < stop_2atr:
                        hit_stop = "2atr"
                    elif p_now < stop_3pct:
                        hit_stop = "3pct"
                    elif p_now < ma40.iloc[i]:
                        hit_stop = "ma40"
                else:
                    if p_now > stop_2atr:
                        hit_stop = "2atr"
                    elif p_now > stop_3pct:
                        hit_stop = "3pct"
                    elif p_now > ma40.iloc[i]:
                        hit_stop = "ma40"

        # ---- 3) 平仓: 信号翻转 / 昨日止损红灯 / 当日止损强平 ----
        if cur_side != 0 and (side != cur_side or prev_stop or hit_stop):
            cause = hit_stop or prev_stop or "signal"
            _close_seg(i, cause)
            cur_side, cur_n, cur_entry = 0, 0, None
            entry_px = stop_2atr = stop_3pct = None

        # ---- 4) 开新仓: 以当日(T+1)收盘实际成交; 止损执行日不重开 ----
        if side != 0 and cur_side == 0 and prev_stop is None:
            margin_per = p_now * mult * margin
            n = max(1, int(eq // margin_per)) if eq > margin_per * MAINT else 0
            if n > 0:
                cur_side, cur_n, cur_entry = side, n, i
                entry_px = p_now                                   # 实际成交价
                stop_2atr = entry_px + (-2 if side == 1 else 2) * atr.iloc[i]
                stop_3pct = entry_px * (0.97 if side == 1 else 1.03)
                eq -= n * p_now * FEE_RATE * mult

        # ---- 5) 保证金强平 ----
        if cur_side != 0:
            margin_used = cur_n * p_now * mult * margin
            if eq < margin_used * MAINT:
                eq = max(eq, 0.0)
                _close_seg(i, "margin")
                cur_side, cur_n, cur_entry = 0, 0, None
                entry_px = stop_2atr = stop_3pct = None
        equity[i] = eq

        # ---- 6) 次日止损判定（T 日收盘触发, T+1 执行）----
        prev_stop = None
        if cur_side != 0 and use_stop and not stop_same_day and i > 0 and entry_px is not None:
            if cur_side == 1:
                if p_now < stop_2atr:
                    prev_stop = "2atr"
                elif p_now < stop_3pct:
                    prev_stop = "3pct"
                elif p_now < ma40.iloc[i]:
                    prev_stop = "ma40"
            else:
                if p_now > stop_2atr:
                    prev_stop = "2atr"
                elif p_now > stop_3pct:
                    prev_stop = "3pct"
                elif p_now > ma40.iloc[i]:
                    prev_stop = "ma40"

    # 期末持仓结算
    if cur_side != 0:
        last = len(v)
        eq -= cur_n * px[last - 1] * FEE_RATE * mult
        eq = max(eq, 0.0)
        seg_pnl.append(daily_pnl[cur_entry:last].sum())
        segs.append((cur_entry, last, cur_side))
        seg_reason.append("end")

    nav = pd.Series(equity, index=close.index)
    dd = nav / nav.cummax() - 1.0
    total_ret = float(nav.iloc[-1] / INIT_CAP - 1)
    n_days = len(nav)
    ann = (nav.iloc[-1] / INIT_CAP) ** (252 / n_days) - 1 if n_days > 0 else np.nan
    wins = sum(1 for s in seg_pnl if s > 0)
    seg_win = wins / len(seg_pnl) if seg_pnl else np.nan
    exposed = daily_pnl != 0
    day_win = float((daily_pnl[exposed] > 0).mean()) if exposed.sum() else np.nan

    metrics = {
        "总收益%": total_ret * 100,
        "年化%": ann * 100,
        "最大回撤%": float(dd.min()) * 100,
        "日胜率%": (day_win if not pd.isna(day_win) else np.nan) * 100,
        "段胜率%": (seg_win if not pd.isna(seg_win) else np.nan) * 100,
        "交易段数": len(seg_pnl),
        "多头天数": int((pos == 1).sum()),
        "空头天数": int((pos == -1).sum()),
        "空仓天数": int((pos == 0).sum()),
        "2ATR触发": n_stop["2atr"],
        "3%触发": n_stop["3pct"],
        "MA40触发": n_stop["ma40"],
        "保证金强平": n_stop["margin"],
        "期末权益": nav.iloc[-1],
    }
    return metrics, nav, segs, seg_reason

def bidirect_ma40_views(df):
    """MA40 方向 + 双向入场严格/持仓宽松（dxke_bidirect 换 MA40，历史最优 EMA50 版 -2.76% 的对照）"""
    c = df["close"]
    e12 = c.ewm(span=12, adjust=False).mean()
    e26 = c.ewm(span=26, adjust=False).mean()
    dif = e12 - e26
    dea = dif.ewm(span=9, adjust=False).mean()
    hist = dif - dea
    h, l = df["high"], df["low"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    atr = tr.ewm(span=14, adjust=False).mean()
    ma40 = c.rolling(40).mean()
    v = dif / atr
    a = hist / atr
    vv, aa, cc, mm = v.values, a.values, c.values, ma40.values
    views = np.full(len(df), 0)
    in_long = in_short = False
    for i in range(1, len(df)):
        if np.isnan(vv[i]) or np.isnan(aa[i]) or np.isnan(mm[i]):
            views[i] = 0; in_long = in_short = False; continue
        if cc[i] > mm[i] and vv[i] > 0 and aa[i] > 0:
            if in_long:
                views[i] = 1; continue
            if vv[i] > aa[i] and not np.isnan(aa[i - 1]) and aa[i] >= aa[i - 1]:
                in_long = True; views[i] = 1; continue
        elif cc[i] < mm[i] and vv[i] < 0 and aa[i] < 0:
            if in_short:
                views[i] = -1; continue
            if aa[i] < vv[i] and not np.isnan(aa[i - 1]) and aa[i] < aa[i - 1]:
                in_short = True; views[i] = -1; continue
        views[i] = 0; in_long = in_short = False
    return pd.Series(views, index=df.index), pd.DataFrame(
        {"open": df["open"], "high": df["high"], "low": df["low"],
         "close": c, "ma40": ma40, "atr14": atr}, index=df.index)


def ema50_views_ma40_stop(df):
    """EMA50 开仓方向 + MA40 平仓/止损基准
    信号: 复用 dxke_bidirect (EMA50 方向, 双向入场严格/持仓宽松)
    raw: 含 MA40(用于期货止损/平仓基准)、ATR14、OHLC
    """
    import dxke_bidirect
    views = dxke_bidirect.daily_views(df)
    c = df["close"]
    e12 = c.ewm(span=12, adjust=False).mean()
    e26 = c.ewm(span=26, adjust=False).mean()
    dif = e12 - e26
    dea = dif.ewm(span=9, adjust=False).mean()
    hist = dif - dea
    h, l = df["high"], df["low"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    atr = tr.ewm(span=14, adjust=False).mean()
    ma40 = c.rolling(40).mean()
    return views, pd.DataFrame(
        {"open": df["open"], "high": df["high"], "low": df["low"],
         "close": c, "ma40": ma40, "atr14": atr}, index=df.index)


def backtest_etf_long_only(views, df):
    """ETF 只做多回测引擎（无杠杆、无保证金、无强平）
    信号=1 满仓做多, 信号=0/-1 空仓
    T 日收盘信号 -> T+1 收盘成交, T+1 当日无浮动, T+2 起逐日结算
    """
    close = df["close"]
    px = close.values
    v = views.reindex(close.index).fillna(0).astype(int)

    pos = np.zeros(len(v), dtype=int)
    pos[1:] = v.values[:-1]
    # 只做多：把 -1 改为 0
    pos = np.where(pos > 0, 1, 0)

    eq = INIT_CAP
    equity = np.full(len(v), np.nan)
    segs, seg_pnl, seg_reason = [], [], []
    cur_side, cur_entry = 0, None
    daily_pnl = np.zeros(len(v))

    for i in range(len(v)):
        side = int(pos[i])  # 0 或 1
        p_now = px[i]

        # 结算当日盈亏（成交日 T+1 无浮动, T+2 起）
        if cur_side != 0 and i > 0 and (cur_entry is None or i > cur_entry):
            pnl = cur_side * (px[i] - px[i - 1]) / px[i - 1] * eq
            eq += pnl
            daily_pnl[i] = pnl

        # 平仓: 信号翻空/转0
        if cur_side != 0 and side != cur_side:
            seg_pnl.append(daily_pnl[cur_entry:(i + 1)].sum())
            segs.append((cur_entry, i + 1, cur_side))
            seg_reason.append("signal")
            cur_side, cur_entry = 0, None

        # 开仓: 信号=1 且当前空仓
        if side == 1 and cur_side == 0:
            cur_side, cur_entry = 1, i

        equity[i] = eq

    # 期末持仓结算
    if cur_side != 0:
        last = len(v)
        seg_pnl.append(daily_pnl[cur_entry:last].sum())
        segs.append((cur_entry, last, cur_side))
        seg_reason.append("end")

    nav = pd.Series(equity, index=close.index)
    dd = nav / nav.cummax() - 1.0
    total_ret = float(nav.iloc[-1] / INIT_CAP - 1)
    n_days = len(nav)
    ann = (nav.iloc[-1] / INIT_CAP) ** (252 / n_days) - 1 if n_days > 0 else np.nan
    wins = sum(1 for s in seg_pnl if s > 0)
    seg_win = wins / len(seg_pnl) if seg_pnl else np.nan
    exposed = daily_pnl != 0
    day_win = float((daily_pnl[exposed] > 0).mean()) if exposed.sum() else np.nan

    metrics = {
        "总收益%": total_ret * 100,
        "年化%": ann * 100,
        "最大回撤%": float(dd.min()) * 100,
        "日胜率%": (day_win if not pd.isna(day_win) else np.nan) * 100,
        "段胜率%": (seg_win if not pd.isna(seg_win) else np.nan) * 100,
        "交易段数": len(seg_pnl),
        "多头天数": int((pos == 1).sum()),
        "空仓天数": int((pos == 0).sum()),
        "期末权益": nav.iloc[-1],
    }
    return metrics, nav, segs, seg_reason
