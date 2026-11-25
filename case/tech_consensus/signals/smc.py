# -*- coding: utf-8 -*-
"""SMC（Smart Money Concepts）日线/周线双向观点信号 —— 只出观点，不改变模拟交易方向
Top-Down 判定（忠实于 SMC 多时间框架手册）：
  周线定 bias（大周期方向）：周线结构 bull/bear/flat
  日线出信号（小周期触发）：日线收盘突破最近已确认摆动高点 -> 做多 CHoCH/BOS；
                            日线收盘跌破最近已确认摆动低点 -> 做空 CHoCH/BOS
  bias 过滤：周线 bear 时不判多（逆大周期不做）；周线 bull 时不判空
  其余 -> 震荡（观望）
输出 (view, detail)：view ∈ {多, 空, 震荡}；detail 给出可复现的数值判定依据。
"""
import numpy as np
import pandas as pd

METHOD_NAME = "SMC日周线"

# 摆动点邻域半径（唯一极值识别，k 越大越平滑）
K_DAY = 2        # 日线摆动点
K_WEEK = 1       # 周线摆动点
MIN_WEEK_BARS = 24   # 周线最少 K 线根数，不足则降级用日线 bias


def _swings(high, low, k):
    """识别摆动高/低点（i 为邻域 [i-k, i+k] 内唯一极值）。
    返回 (sh_px, sh_i, sl_px, sl_i)，均按时间升序。
    """
    hi = np.asarray(high, dtype=float)
    lo = np.asarray(low, dtype=float)
    n = len(hi)
    sh_px, sh_i, sl_px, sl_i = [], [], [], []
    for i in range(k, n - k):
        w_hi = hi[i - k:i + k + 1]
        if hi[i] == w_hi.max() and int((w_hi == hi[i]).sum()) == 1:
            sh_px.append(hi[i]); sh_i.append(i)
        w_lo = lo[i - k:i + k + 1]
        if lo[i] == w_lo.min() and int((w_lo == lo[i]).sum()) == 1:
            sl_px.append(lo[i]); sl_i.append(i)
    return sh_px, sh_i, sl_px, sl_i


def _regime(close, sh_px, sl_px):
    """结构方向：bull / bear / flat（bull 优先，与既有 SMC 引擎一致）
    chain_up / chain_dn 用最近两个摆动点递进关系；另以收盘价对最近摆动点做补充确认。
    """
    chain_up = (len(sh_px) >= 2 and sh_px[-1] > sh_px[-2]) or \
               (len(sl_px) >= 2 and sl_px[-1] > sl_px[-2])
    chain_dn = (len(sh_px) >= 2 and sh_px[-1] < sh_px[-2]) or \
               (len(sl_px) >= 2 and sl_px[-1] < sl_px[-2])
    if chain_up or (sh_px and close > sh_px[-1]):
        return "bull"
    if chain_dn or (sl_px and close < sl_px[-1]):
        return "bear"
    return "flat"


def _weekly(df):
    """由日线聚合成周线（周五收盘周）；返回周线 DataFrame（date/open/high/low/close/volume）"""
    d = df.copy()
    d["date"] = pd.to_datetime(d["date"])
    d = d.set_index("date").resample("W-FRI").agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }).dropna(subset=["close"])
    return d.reset_index()


def _daily_signals(close_last, sh_px, sh_i, sl_px, sl_i, k):
    """日线触发：
    long_sig  = 收盘突破最近【已确认】摆动高点（结构转换/延续向上）
    short_sig = 收盘跌破最近【已确认】摆动低点（结构转换/延续向下）
    返回 (long_sig, short_sig, hi_px, lo_px, hi_gap, lo_gap)
    """
    last_i = len(close_last) if not hasattr(close_last, "__len__") else 0  # 占位，不使用
    hi_px = sh_px[-1] if sh_px else np.nan
    lo_px = sl_px[-1] if sl_px else np.nan
    long_sig = bool(sh_px and sh_i[-1] + k <= len(close_last) - 1 and float(close_last.iloc[-1]) > hi_px)
    short_sig = bool(sl_px and sl_i[-1] + k <= len(close_last) - 1 and float(close_last.iloc[-1]) < lo_px)
    return long_sig, short_sig, hi_px, lo_px


def signal(df):
    """df: 标准日线 DataFrame（date/open/high/low/close/volume）
    返回 (view, detail)，view ∈ {多, 空, 震荡}
    """
    if df is None or len(df) < 40:
        return "震荡", "数据不足"
    c = df["close"]
    close_now = float(c.iloc[-1])

    # ---- 周线 bias ----
    wk = _weekly(df)
    if len(wk) >= MIN_WEEK_BARS:
        wsh_px, wsh_i, wsl_px, wsl_i = _swings(wk["high"], wk["low"], K_WEEK)
        wk_bias = _regime(float(wk["close"].iloc[-1]), wsh_px, wsl_px)
        wk_desc = f"周线{ {'bull':'偏多','bear':'偏空','flat':'震荡'}[wk_bias] }"
    else:
        dsh_px, dsh_i, dsl_px, dsl_i = _swings(df["high"], df["low"], K_DAY)
        wk_bias = _regime(close_now, dsh_px, dsl_px)
        wk_desc = f"周线样本不足，降级日线bias={wk_bias}"

    # ---- 日线触发 ----
    dsh_px, dsh_i, dsl_px, dsl_i = _swings(df["high"], df["low"], K_DAY)
    long_sig, short_sig, hi_px, lo_px = _daily_signals(c, dsh_px, dsh_i, dsl_px, dsl_i, K_DAY)

    # ---- bias 过滤 + 观点 ----
    note_long, note_short = [], []
    if long_sig and wk_bias == "bear":
        note_long.append(f"日线破高{hi_px:.4f}但逆周线偏空，不判多")
        long_sig = False
    if short_sig and wk_bias == "bull":
        note_short.append(f"日线破低{lo_px:.4f}但逆周线偏多，不判空")
        short_sig = False

    if long_sig:
        return "多", (f"{wk_desc}；日线收盘{close_now:.4f}突破最近摆动高点{hi_px:.4f}"
                      f"（做多CHoCH/BOS确认）{'；'.join(note_long)}")
    if short_sig:
        return "空", (f"{wk_desc}；日线收盘{close_now:.4f}跌破最近摆动低点{lo_px:.4f}"
                      f"（做空CHoCH/BOS确认）{'；'.join(note_short)}")

    # 震荡：给出结构位置信息
    pos = []
    if not np.isnan(hi_px):
        pos.append(f"上沿{hi_px:.4f}")
    if not np.isnan(lo_px):
        pos.append(f"下沿{lo_px:.4f}")
    pos_s = " / ".join(pos) if pos else "无有效摆动点"
    extra = "；".join(note_long + note_short)
    note = f"；{extra}" if extra else ""
    return "震荡", f"{wk_desc}；日线结构化区间 {pos_s}，现价{close_now:.4f}未触发突破{note}"