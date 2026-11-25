# -*- coding: utf-8 -*-
"""信号层：COT 分位、方向、episode 计时、结论三档，以及 Q 五口径 + 残差动量。

全部是纯规则计算（rolling / 分位 / 符号），不调用任何大模型，也不联网。

COT 口径
--------
`cot = (mm_net − 过去 N 周最小) / (过去 N 周最大 − 过去 N 周最小)`
  · N 默认 104 周（2 年），窗口只含 **t 及以前**，无前视
  · 这是「净多单在 2 年里排到哪」的 min-max 位置，0 = 两年最少，1 = 两年最多
  · 另给 `cot_rank`（真实秩分位）只做审计对照，结论仍用 cot（与原推送口径一致）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

HI_DEFAULT = 0.90
LO_DEFAULT = 0.10
COT_LOOKBACK = 104
COT_MIN_OBS = 30
WIN_MIN = 4
WIN_MAX = 8

# 历史 episode 平均 4 周收益（bp）与单周胜率，来自 2020-2026 事件统计，用于「肉吃完没」
# crude 为现算值（本模块 episode_stats 去重事件，n=26）：4 周 +213bp / 单周胜率 0.46，
# 原始口径见 config/cftc_cot.yaml 注释；比金属弱很多，属于「本来就该低预期」的品种。
HIST_AVG_BP: Dict[str, float] = {"copper": 429, "gold": 158, "silver": 450, "corn": 570,
                                 "crude": 213}
HIST_WEEKLY_HIT: Dict[str, float] = {"copper": 0.56, "gold": 0.58, "silver": 0.50, "corn": 0.62,
                                     "crude": 0.46}

COLOR_LONG = "#c0392b"
COLOR_SHORT = "#1e8449"
COLOR_EXIT = "#e67e22"
COLOR_FLAT = "#7f8c8d"


# ======================================================================
# 1. COT 分位
# ======================================================================
def cot_index(mm_net: pd.Series, window: int = COT_LOOKBACK,
              min_obs: int = COT_MIN_OBS) -> pd.Series:
    """净持仓的滚动 min-max 位置（0~1），窗口只到 t 为止。"""
    s = pd.to_numeric(pd.Series(mm_net), errors="coerce")
    mn = s.rolling(window, min_periods=min_obs).min()
    mx = s.rolling(window, min_periods=min_obs).max()
    rng = (mx - mn).replace(0, np.nan)
    return ((s - mn) / rng).rename("cot")


def rolling_pct_rank(s: pd.Series, window: int = COT_LOOKBACK,
                     min_obs: int = COT_MIN_OBS) -> pd.Series:
    """真实秩分位（仅审计）：当前值在过去 window 个观测中的百分位。"""
    x = pd.to_numeric(pd.Series(s), errors="coerce")

    def _rank(arr: np.ndarray) -> float:
        v = arr[-1]
        if not np.isfinite(v):
            return np.nan
        k = np.isfinite(arr).sum()
        if k < min_obs:
            return np.nan
        return float((arr <= v).sum() / k)

    return x.rolling(window, min_periods=min_obs).apply(_rank, raw=True).rename("cot_rank")


def add_cot(df: pd.DataFrame, window: int = COT_LOOKBACK, min_obs: int = COT_MIN_OBS,
            hi: float = HI_DEFAULT, lo: float = LO_DEFAULT) -> pd.DataFrame:
    """给面板加 cot / cot_rank / dir 三列（dir ∈ {1,0,-1}）。"""
    out = df.copy()
    out["cot"] = cot_index(out["mm_net"], window=window, min_obs=min_obs)
    out["cot_rank"] = rolling_pct_rank(out["mm_net"], window=window, min_obs=min_obs)
    out["dir"] = direction(out["cot"], hi=hi, lo=lo)
    return out


def direction(cot: pd.Series, hi: float = HI_DEFAULT, lo: float = LO_DEFAULT) -> pd.Series:
    """≥hi → +1（做多），≤lo → −1（做空），其余 0（观望）；NaN 视为 0。"""
    c = pd.to_numeric(pd.Series(cot), errors="coerce")
    d = pd.Series(np.where(c >= hi, 1, np.where(c <= lo, -1, 0)), index=c.index, dtype="int64")
    return d.where(c.notna(), 0).astype(int)


# ======================================================================
# 2. episode（当前极值区间走到第几周）
# ======================================================================
@dataclass
class Episode:
    dir: int
    held: int
    start: Optional[pd.Timestamp]
    cot: float
    price0: float
    price1: float
    pnl_pct: float
    last_date: Optional[pd.Timestamp]

    @property
    def side(self) -> str:
        return {1: "做多", -1: "做空", 0: "观望"}[self.dir]


def episode_info(df: pd.DataFrame, price_col: str = "close") -> Episode:
    """当前 dir 连续区间的起点、已持续周数与区间涨跌幅。

    只从最后一根 K 线往前数，**不使用未来数据**；空 cot / 空面板返回观望。
    """
    if df is None or df.empty or "cot" not in df.columns:
        return Episode(0, 0, None, float("nan"), float("nan"), float("nan"), 0.0, None)

    c = df["cot"].dropna()
    if c.empty:
        return Episode(0, 0, None, float("nan"), float("nan"), float("nan"), 0.0, None)

    idx = c.index
    d = df["dir"].reindex(idx).fillna(0).astype(int)
    last = int(d.iloc[-1])
    start_i = len(d) - 1
    if last != 0:
        while start_i > 0 and int(d.iloc[start_i - 1]) == last:
            start_i -= 1
    else:
        # 观望：一直数到上一次有方向为止
        while start_i > 0 and int(d.iloc[start_i - 1]) == 0:
            start_i -= 1
    held = len(d) - start_i

    seg = df.loc[idx[start_i]:]
    p0 = p1 = float("nan")
    if price_col in seg.columns and len(seg):
        px = pd.to_numeric(seg[price_col], errors="coerce").dropna()
        if len(px) >= 1:
            p0 = float(px.iloc[0])
            p1 = float(px.iloc[-1])
    pnl = float(p1 / p0 - 1) if (len(seg) > 1 and np.isfinite(p0) and p0) else 0.0
    return Episode(dir=last, held=held, start=pd.Timestamp(idx[start_i]), cot=float(c.iloc[-1]),
                   price0=p0, price1=p1, pnl_pct=pnl, last_date=pd.Timestamp(idx[-1]))


# ======================================================================
# 3. 结论三档
# ======================================================================
@dataclass
class Verdict:
    action: str          # 做多 / 做空 / 观望
    stage: str           # entry / hold / exit / flat
    advice: str
    color: str
    remain: int          # 距止盈线还有几周（负数=超期）
    realized_bp: float
    hist_bp: float

    def as_tuple(self) -> Tuple[str, str, str]:
        return self.action, self.advice, self.color


def verdict(key: str, info: Episode, win_min: int = WIN_MIN, win_max: int = WIN_MAX,
            hist_avg_bp: Optional[Dict[str, float]] = None) -> Verdict:
    """三档：<4 周（建仓窗口）/ 4~8 周（持有、可分批止盈）/ >8 周（减仓止盈）。"""
    avg_map = HIST_AVG_BP if hist_avg_bp is None else hist_avg_bp
    avg = float(avg_map.get(key, 400))
    if info.dir == 0:
        return Verdict("观望", "flat", "不开仓", COLOR_FLAT, win_max, 0.0, avg)

    side = "做多" if info.dir > 0 else "做空"
    realized = info.pnl_pct * info.dir * 1e4
    remain = win_max - info.held
    prog = realized / avg if avg else 0.0

    if info.held < win_min:
        return Verdict(side, "entry",
                       f"已第 {info.held} 周，还在建仓窗口，继续持有；"
                       f"已跑 {realized:+.0f}bp / 历史均值 {avg:.0f}bp",
                       COLOR_LONG if info.dir > 0 else COLOR_SHORT, remain, realized, avg)
    if info.held <= win_max:
        if prog >= 1.0:
            return Verdict(side, "hold",
                           f"已第 {info.held} 周，距止盈线还有 {remain} 周；"
                           f"已跑 {realized:+.0f}bp 超均值 {avg:.0f}bp，可分批止盈",
                           COLOR_EXIT, remain, realized, avg)
        return Verdict(side, "hold",
                       f"已第 {info.held} 周，距止盈线还有 {remain} 周；"
                       f"已跑 {realized:+.0f}bp / 均值 {avg:.0f}bp，继续持有",
                       COLOR_LONG if info.dir > 0 else COLOR_SHORT, remain, realized, avg)
    return Verdict(side, "exit",
                   f"已第 {info.held} 周，超止盈线 {abs(remain)} 周；"
                   f"已跑 {realized:+.0f}bp，减仓/止盈",
                   COLOR_EXIT, remain, realized, avg)


# ======================================================================
# 4. Q 五口径 + 残差动量（与原稳健性检验口径一致）
# ======================================================================
def all_q(df: pd.DataFrame) -> pd.DataFrame:
    """五种 Q 口径：

    Q1 = ΔMM净持仓 / OI            （主口径，论文口径）
    Q2 = ΔMM净持仓 / ΔOI           （按增量持仓标准化，OI 剧变时会爆）
    Q3 = ΔMM净持仓 / 4周滚动均值OI （平滑 OI）
    Q4 = Δ多空比                   （(L−S)/(L+S) 的差分，无量纲）
    Q5 = ΔMM总持仓 / OI            （只看参与度，不分方向）
    """
    d = df.copy()
    if "mm_long" not in d.columns or "mm_short" not in d.columns:
        raise KeyError("all_q 需要 mm_long / mm_short 列")
    oi = pd.to_numeric(d["oi"], errors="coerce")
    d_mm = pd.to_numeric(d["d_mm_net"], errors="coerce") if "d_mm_net" in d else d["mm_net"].diff()

    d["Q1"] = d_mm / oi
    d["Q2"] = d_mm / pd.to_numeric(d.get("d_oi", oi.diff()), errors="coerce").replace(0, np.nan)
    d["Q3"] = d_mm / oi.rolling(4, min_periods=2).mean()
    long_, short_ = pd.to_numeric(d["mm_long"], errors="coerce"), pd.to_numeric(d["mm_short"], errors="coerce")
    ratio = (long_ - short_) / (long_ + short_).replace(0, np.nan)
    d["ratio"] = ratio
    d["Q4"] = ratio.diff()
    d["Q5"] = (long_ + short_).diff() / oi
    return d


def residuals(panel: pd.DataFrame, q_col: str = "Q1", roll: int = 52,
              min_obs: int = 26) -> pd.DataFrame:
    """滚动回归 R_t = α + β·Q_t + ε_t，只用到 t 及以前的数据（无前视）。"""
    t = panel.copy()
    q = pd.to_numeric(t[q_col], errors="coerce").to_numpy(float)
    r = pd.to_numeric(t["r"], errors="coerce").to_numpy(float)
    n = len(t)
    resid = np.full(n, np.nan)
    for i in range(n):
        if not (np.isfinite(q[i]) and np.isfinite(r[i])):
            continue
        lo = max(0, i + 1 - roll)
        xs = q[lo:i + 1]
        ys = r[lo:i + 1]
        m = np.isfinite(xs) & np.isfinite(ys)
        xs, ys = xs[m], ys[m]
        if len(xs) < min_obs:
            continue
        vx = np.var(xs, ddof=1)
        if not np.isfinite(vx) or vx <= 0:
            continue
        b = np.cov(xs, ys, ddof=1)[0, 1] / vx
        a = ys.mean() - b * xs.mean()
        if np.isfinite(b):
            resid[i] = r[i] - (a + b * q[i])
    t["resid"] = resid
    return t


def signals_for(panel: pd.DataFrame, q_col: str = "Q1", roll: int = 52,
                min_obs: int = 26) -> pd.DataFrame:
    """残差动量信号：sig_rev = −sign(Q)，sig_mom = sign(ε)，并附 r_fwd0..3。"""
    t = residuals(panel, q_col=q_col, roll=roll, min_obs=min_obs)
    t["sig_rev"] = -np.sign(pd.to_numeric(t[q_col], errors="coerce"))
    t["sig_mom"] = np.sign(t["resid"])
    for lag in (0, 1, 2, 3):
        t[f"r_fwd{lag}"] = t["r"].shift(-(lag + 1))
    return t


def ic_of(df: pd.DataFrame, sig: str, ret: str) -> float:
    x = df.dropna(subset=[sig, ret])
    if len(x) < 5 or x[sig].nunique() < 2:
        return float("nan")
    v = x[sig].corr(x[ret])
    return float(v) if np.isfinite(v) else float("nan")


def nav_stats(df: pd.DataFrame, sig: str, ret: str, cost_bp: float = 0.0) -> Dict[str, float]:
    """净值/年化/Sharpe/最大回撤/胜率；cost_bp = 单边成本，信号变动时双边计费。"""
    x = df.dropna(subset=[sig, ret]).copy()
    keys = dict(n=0, nav=np.nan, ann=np.nan, sharpe=np.nan, mdd=np.nan, hit=np.nan, t=np.nan)
    if len(x) < 5:
        keys["n"] = int(len(x))
        return keys

    pos = pd.to_numeric(x[sig], errors="coerce").fillna(0.0)
    turn = pos.diff().abs().fillna(pos.abs())
    pnl = pos * pd.to_numeric(x[ret], errors="coerce") - turn * cost_bp / 1e4
    nav = (1 + pnl).cumprod()
    last = float(nav.iloc[-1])
    sd = float(pnl.std(ddof=1))
    return dict(
        n=int(len(x)),
        nav=last,
        ann=float(last ** (52 / len(x)) - 1) if last > 0 else np.nan,
        sharpe=float(pnl.mean() / sd * np.sqrt(52)) if sd > 0 else np.nan,
        mdd=float((nav / nav.cummax() - 1).min()),
        hit=float((pnl > 0).mean()),
        t=float(pnl.mean() / (sd / np.sqrt(len(x)))) if sd > 0 else np.nan,
    )


def episode_stats(df: pd.DataFrame, hi: float = HI_DEFAULT, lo: float = LO_DEFAULT,
                  max_horizon: int = 12, price_col: str = "close") -> pd.DataFrame:
    """逐个极值 episode 的前瞻收益：事件去重（同方向连续区间算一次）。

    返回列：start / end / dir / weeks / fwd1..max_horizon（%）、price0 / price1。
    """
    if df is None or df.empty or "cot" not in df.columns:
        return pd.DataFrame()
    c = df["cot"].dropna()
    if c.empty:
        return pd.DataFrame()
    d = direction(c, hi=hi, lo=lo)
    idx = list(c.index)

    rows: List[Dict[str, object]] = []
    i = 0
    px = pd.to_numeric(df[price_col], errors="coerce").reindex(c.index)
    while i < len(idx):
        if int(d.iloc[i]) == 0:
            i += 1
            continue
        side = int(d.iloc[i])
        j = i
        while j + 1 < len(idx) and int(d.iloc[j + 1]) == side:
            j += 1
        p0 = px.iloc[i]
        row: Dict[str, object] = {
            "start": idx[i], "end": idx[j], "dir": side, "weeks": j - i + 1,
            "cot_start": float(c.iloc[i]), "price0": p0,
        }
        # 前瞻收益一律从「入场那个极值周」起算（与原周报事件统计口径一致），
        # 不是从区间结束起算，否则会把区间内涨幅重复计进去。
        for h in range(1, max_horizon + 1):
            k = i + h
            row[f"fwd{h}"] = (float(px.iloc[k] / p0 - 1) * 100
                              if k < len(idx) and np.isfinite(p0) and p0 else np.nan)
        rows.append(row)
        i = j + 1
    return pd.DataFrame(rows)


def hit_summary(eps: pd.DataFrame, horizon: int = 4) -> Dict[str, float]:
    """给定 horizon（周）的事件胜率/均值（bp），按方向取符号。"""
    col = f"fwd{horizon}"
    if eps is None or eps.empty or col not in eps.columns:
        return dict(n=0, hit=float("nan"), mean_bp=float("nan"))
    x = eps.dropna(subset=[col])
    if x.empty:
        return dict(n=0, hit=float("nan"), mean_bp=float("nan"))
    signed = x[col].to_numpy(float) * x["dir"].to_numpy(int)
    return dict(n=int(len(signed)), hit=float((signed > 0).mean()), mean_bp=float(signed.mean() * 100))
