#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ot_core.py — 最优传输统一核心库
================================
把 Max OT「崩→稳→起」靠岸监测（wasserstein_landing_monitor.py + wasserstein_coupling_matrix.py）
与 OT 日报的 W1 漂移信号合并为一套可复用库，取数统一走 DataHub（datahub-multi-tier/data_hub）。

对外能力
--------
1. 数据层：get_index_df / get_etf_df / get_etf_share_df —— 唯一取数入口，全部经 data_hub
2. 分布层：r_norm（波动归一日收益）、rolling_w1（一维精确 W1）、joint_w1（多维 W1，POT/Sinkhorn）
3. 靠岸层：build_landing_snapshot —— 六指数 W1 分位、六档状态、大小盘分布、回撤与动量
4. 耦合层：build_coupling_snapshot —— 成对 W1、尾部依赖 λ_L/λ_U、谱共振 λmax/N

硬约束（继承 Max OT 设计说明）
------------------------------
- Wasserstein 是状态识别，不是买卖信号；结论须与回撤、成交、基差交叉看。
- r_norm = r / r.rolling(20).std() 必须先做，否则跨指数不可比。
- 参考稳态窗口不得取崩盘段。
- 所有窗口参数集中在顶部常量。
- 取数差异全部收敛在本文件，主逻辑不碰取数细节。
"""

from __future__ import annotations

import os
import sys
import warnings
from datetime import date, timedelta

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ============================================================================
# 路径与依赖
# ============================================================================

# 路径可用环境变量覆盖，便于在任意机器/仓库中复用（不再硬编本地绝对路径）
WS_ROOT = os.environ.get("DUMATE_WS", "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace")
DATAHUB_ROOT = os.environ.get("DATAHUB_ROOT", os.path.join(WS_ROOT, "projects", "datahub-multi-tier"))
PYLIB_ROOT = os.environ.get("PYLIB_ROOT", os.path.join(WS_ROOT, "pylibs"))
for _p in (DATAHUB_ROOT, PYLIB_ROOT):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from scipy.stats import wasserstein_distance
    from scipy.spatial.distance import cdist
except ImportError as e:  # pragma: no cover
    raise SystemExit("缺少 scipy，请安装：pip install scipy") from e

try:
    import ot as _pot
except ImportError:  # pragma: no cover
    _pot = None


# ============================================================================
# 顶部常量（全部可调）
# ============================================================================

# 六指数：ts_code -> (中文名, 简称, 大小盘分类)
INDEX_POOL = {
    "000016.SH": ("上证50", "上证50", "big"),
    "000300.SH": ("沪深300", "沪深300", "big"),
    "000905.SH": ("中证500", "中证500", "mid"),
    "000852.SH": ("中证1000", "中证1000", "small"),
    "399006.SZ": ("创业板指", "创业板", "small"),
    "000688.SH": ("科创50", "科创50", "small"),
}
BIG_CAP = {"000016.SH", "000300.SH"}
SMALL_CAP = {"000852.SH", "399006.SZ", "000688.SH"}

WIN = 15               # 滚动窗口：当前收益分布用最近 WIN 个交易日
ROLL_STD = 20          # 波动归一窗口
PCT_LEVELS = (40, 60, 80)

# 参考稳态模板：正常市（避开暴涨/暴跌段）
# ---------------------------------------------------------------------------
# 【P0-1 修复】原实现把模板硬编成 2026-04-01~06-30，而该窗口实测是「暴涨段」
# （科创50 +70.1%、创业板指 +33.7%，模板 r_norm 均值 0.384 vs 全样本 0.061），
# 参考分布被系统性右移 → W1 无方向、距离被结构放大 → 分位虚高 → 永远不靠岸。
# 现在默认走 STEADY_MODE="auto"：在样本内自动挑一个「涨跌幅接近 0、波动接近全样本」
# 的连续窗口当模板，并对人工指定窗口做合法性校验（不合法直接硬失败）。
STEADY_MODE = "auto"            # "auto"=自动选窗（推荐）｜"manual"=用 STEADY_START/END
STEADY_START = "2026-04-01"     # 仅 STEADY_MODE="manual" 时生效
STEADY_END = "2026-06-30"

# 模板合法性硬阈值（manual 模式与 auto 候选都要过）
STEADY_MIN_DAYS = 30            # 窗口内有效交易日下限，低于此值硬失败（原实现只 warn）
STEADY_MAX_ABS_RET = 0.15       # 窗口区间涨跌幅绝对值上限，超过即判为「暴涨/暴跌段」
STEADY_VOL_RATIO_BAND = (0.6, 1.6)   # 窗口年化波动 / 全样本年化波动 的允许区间
STEADY_CANDIDATE_WIN = 60       # auto 模式候选窗口长度（交易日）
STEADY_CANDIDATE_STEP = 5       # auto 模式候选窗口滑动步长
STEADY_MIN_GAP = 20             # 候选窗口终点距样本末端至少留的交易日（避免用最近数据当模板）

DECLINE_K = 5          # “连续下降”回看天数
CROSS_K = 20           # “穿过 80→40”回看天数
CROSSED_LOW = 60       # 【P0-2 修复】“靠岸中”里“已回落”的判定线。原来直接用 <40 会和“到底”
                       # 完全重叠，导致靠岸中被企稳可起全部吃掉（实测 340 日仅 1 天可达）。
                       # 语义拆开：曾 >80 且现在 <60 = 正在往岸上靠；现在 <40 = 已经到底。
PLATEAU_K = 10         # “不再创新高”平台判定天数

# 【P0-2 修复】状态机由「六指数全部满足」的与门改为「多数票」。
# 原实现要求 6 个指数同时满足同一条件，340 个交易日实测：中性 278 天（82%），
# 🟨靠岸中 / 🟧底部摩擦 两档 0 天（死代码）。改为「N 个指数里至少 MAJORITY 个满足」。
MAJORITY = 4           # 6 个指数里至少 4 个满足才算该档成立
CROSSED_MIN = 3        # 「靠岸中」要求至少 3 个指数完成 80→40 的穿越

# 【P1-5 修复】分位口径：full=全样本后验分位（原实现，含未来函数）；causal=扩张窗分位（无未来函数）
PCT_MODE = "causal"
PCT_MIN_PERIODS = 60   # causal 模式下分位估计的最小样本数

JOINT_OT_MAX_CELLS = 2500
SINKHORN_REG = 1e-2

# 【P1-1/P1-2 修复】谱共振残差化。
# N 个近似等相关序列的 λmax/N = 1/N + (1-1/N)·ρ̄，实测 corr(λmax/N, 平均λ_L)=0.9976、
# 99.5% 方差被平均 λ_L 解释 → 原始 λmax/N 不构成独立证据。这里减掉线性预期，
# 只保留「超出平均尾依赖能解释的部分」作为共振残差。
RESIDUAL_EXCESS_Q = (30, 70)   # 谱共振结构残差的分位带（仅作诊断展示，不参与判色）
PHASE_LL_HIGH_Q = 60           # 红阶段：平均 λ_L 高于自身历史该分位
PHASE_LL_LOW_Q = 20            # 绿阶段：平均 λ_L 低于自身历史该分位
JOINT_LOW_Q = 40               # “联合 W1 在低位”的判定线（企稳可起的前置条件）

# 耦合矩阵参数
TAIL_WIN = 60          # 尾部依赖窗口
TAIL_LEVEL = 0.25      # 尾部依赖分位水平
MA_WIN = 60            # 时间序列平滑

# OT 日报原有的 W1 漂移双窗口（与靠岸 W1 同源不同窗口，分开保留）
DRIFT_WINDOWS = (40, 60)
DRIFT_WEIGHTS = (0.6, 0.4)
DRIFT_QUANTILE = 0.70
MA_PERIOD = 40

# 回撤/位置参照
DD_LONG = 250          # 长回撤参照（约一年）
DD_SHORT = 60

STATE_LABELS = [
    "🟥 共振崩塌",
    "🟧 底部摩擦",
    "🟨 靠岸中",
    "ℹ️ 大盘先稳，小盘滞后",
    "🟩 企稳可起",
    "⚪ 中性",
]


# ============================================================================
# 数据层：全部经 DataHub
# ============================================================================

def _dh():
    import data_hub  # 延迟导入，避免未配置时影响纯计算
    return data_hub


def _norm_dates(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame(columns=["close"])
    out = df.copy()
    dc = "trade_date" if "trade_date" in out.columns else ("date" if "date" in out.columns else None)
    if dc is None:
        return pd.DataFrame(columns=["close"])
    out["date"] = pd.to_datetime(out[dc].astype(str), errors="coerce")
    out = out.dropna(subset=["date"]).drop_duplicates("date").sort_values("date")
    out = out.set_index("date")
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    if "vol" in out.columns:
        out["vol"] = pd.to_numeric(out["vol"], errors="coerce")
    if "amount" in out.columns:
        out["amount"] = pd.to_numeric(out["amount"], errors="coerce")
    elif "vol" in out.columns:
        out["amount"] = out["vol"]        # 指数无 amount，用成交量代填（仅留档）
    else:
        out["amount"] = np.nan            # 数据源既无 amount 也无 vol：补空列，避免 KeyError
    cols = ["close", "amount", "vol"] if "vol" in out.columns else ["close", "amount"]
    return out.dropna(subset=["close"])[cols]


def get_index_df(ts_code: str, start: str, end: str) -> pd.DataFrame:
    """指数日线：DataHub get_index_daily → DataFrame[close, amount]。"""
    dh = _dh()
    df = dh.get_index_daily(ts_code=ts_code, start_date=start.replace("-", ""),
                            end_date=end.replace("-", ""))
    out = _norm_dates(df)
    if out.empty:
        raise RuntimeError(f"DataHub 未返回 {ts_code} 指数日线")
    return out


def get_index_batch(codes, start: str, end: str) -> dict:
    """指数批量取数（DataHub 批量接口，失败逐个回退）。"""
    dh = _dh()
    out = {}
    try:
        raw = dh.get_index_daily(ts_code_list=list(codes), start_date=start.replace("-", ""),
                                 end_date=end.replace("-", ""))
        if isinstance(raw, dict):
            for c, df in raw.items():
                d = _norm_dates(df)
                if not d.empty:
                    out[str(c).upper()] = d
    except Exception:
        pass
    for c in codes:
        key = str(c).upper()
        if key not in out:
            try:
                out[key] = get_index_df(c, start, end)
            except Exception as e:
                print(f"  [取数失败] {c}: {e}")
    return {c: out[c] for c in [str(x).upper() for x in codes] if c in out}


def etf_ts_code(code: str) -> str:
    """sh510300 / 510300 → 510300.SH。"""
    c = str(code).lower()
    if c.startswith("sh"):
        return c[2:] + ".SH"
    if c.startswith("sz"):
        return c[2:] + ".SZ"
    if c.endswith(".sh") or c.endswith(".sz"):
        return c.upper()
    return c + (".SH" if c[0] in "56" else ".SZ")


def get_etf_df(code: str, start: str, end: str) -> pd.DataFrame:
    """ETF 日线：DataHub get_etf_daily → DataFrame[close, amount]。"""
    dh = _dh()
    df = dh.get_etf_daily(ts_code=etf_ts_code(code), start_date=start.replace("-", ""),
                          end_date=end.replace("-", ""))
    out = _norm_dates(df)
    if out.empty:
        raise RuntimeError(f"DataHub 未返回 {code} ETF 日线")
    return out


def get_etf_share_df(code: str, lookback_days: int = 30) -> pd.DataFrame:
    """ETF 份额：DataHub etf_share → Series[fund_share]，失败返回空。"""
    dh = _dh()
    try:
        df = dh.get_data("etf_share", ts_code=etf_ts_code(code), lookback_days=lookback_days)
        if not isinstance(df, pd.DataFrame) or df.empty:
            return pd.DataFrame(columns=["fund_share"])
        out = df.copy()
        out["date"] = pd.to_datetime(out["trade_date"].astype(str), errors="coerce")
        out = out.dropna(subset=["date"]).drop_duplicates("date").sort_values("date").set_index("date")
        out["fund_share"] = pd.to_numeric(out["fund_share"], errors="coerce")
        return out[["fund_share"]].dropna()
    except Exception:
        return pd.DataFrame(columns=["fund_share"])


def get_sector_flow() -> pd.DataFrame:
    """行业板块资金流（DataHub sector_flow，当日快照）。失败返回空。"""
    try:
        df = _dh().get_data("sector_flow", sector_type="industry")
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


# ============================================================================
# 预处理与 Wasserstein
# ============================================================================

def adjust_splits(close: np.ndarray, jump: float = 0.25) -> np.ndarray:
    """前复权：消除 ETF 份额折算造成的假跳空（同 ot_daily_report 口径）。"""
    s = np.asarray(close, dtype=float).copy()
    i = 1
    while i < len(s):
        prev, cur = s[i - 1], s[i]
        if prev > 0:
            r = cur / prev - 1.0
            if abs(r) > jump:
                s[:i] = s[:i] * (cur / prev)
                i = 1
                continue
        i += 1
    return s


def r_norm_series(close: pd.Series, roll_std: int = ROLL_STD) -> pd.Series:
    """波动归一日收益：r = pct_change；r_norm = r / rolling(20).std()（硬约束，必须先做）。"""
    r = close.astype(float).pct_change()
    std = r.rolling(roll_std).std()
    return (r / std).replace([np.inf, -np.inf], np.nan).dropna()


def _annualized_vol(ret: pd.Series) -> float:
    r = ret.dropna()
    return float(r.std() * np.sqrt(242)) if len(r) > 2 else np.nan


def _window_stats(close_wide: pd.DataFrame, start, end) -> dict:
    """窗口体检：区间涨跌幅（各指数均值）、年化波动比、有效交易日数。"""
    seg = close_wide[(close_wide.index >= pd.Timestamp(start)) & (close_wide.index <= pd.Timestamp(end))]
    full_vol, seg_vol, rets = [], [], []
    for c in close_wide.columns:
        r_all = close_wide[c].pct_change()
        r_seg = seg[c].pct_change()
        if len(seg) >= 2 and seg[c].iloc[0] > 0:
            rets.append(seg[c].iloc[-1] / seg[c].iloc[0] - 1.0)
        v_all, v_seg = _annualized_vol(r_all), _annualized_vol(r_seg)
        if np.isfinite(v_all) and v_all > 0 and np.isfinite(v_seg):
            full_vol.append(v_seg / v_all)
    return {
        "start": str(pd.Timestamp(start).date()),
        "end": str(pd.Timestamp(end).date()),
        "days": int(len(seg)),
        "ret": float(np.nanmean(rets)) if rets else np.nan,
        "vol_ratio": float(np.nanmean(full_vol)) if full_vol else np.nan,
    }


def validate_steady_window(close_wide: pd.DataFrame, start, end, strict: bool = True) -> dict:
    """校验稳态模板是否真的是「正常市」。不合法时 strict=True 直接抛错（P0-4）。"""
    st = _window_stats(close_wide, start, end)
    reasons = []
    if st["days"] < STEADY_MIN_DAYS:
        reasons.append(f"有效交易日 {st['days']} < {STEADY_MIN_DAYS}（模板样本不足）")
    if np.isfinite(st["ret"]) and abs(st["ret"]) > STEADY_MAX_ABS_RET:
        kind = "暴涨段" if st["ret"] > 0 else "暴跌段"
        reasons.append(f"窗口区间涨跌 {st['ret']:+.1%} 超过 ±{STEADY_MAX_ABS_RET:.0%}，判定为{kind}")
    lo, hi = STEADY_VOL_RATIO_BAND
    if np.isfinite(st["vol_ratio"]) and not (lo <= st["vol_ratio"] <= hi):
        reasons.append(f"窗口波动/全样本波动 = {st['vol_ratio']:.2f} 落在 [{lo}, {hi}] 之外")
    st["ok"] = not reasons
    st["reasons"] = reasons
    if reasons and strict:
        raise SystemExit(
            "[稳态模板不合法] " + st["start"] + "~" + st["end"] + "：\n  - "
            + "\n  - ".join(reasons)
            + "\n  模板不是「正常市」时，后面所有 W1 分位都带系统偏移，结论不可用。\n"
            "  建议：① 用 STEADY_MODE='auto' 自动选窗；② 或换一段横盘窗口；"
            "③ 确要强制使用请传 strict=False。"
        )
    return st


def resolve_steady_window(close_wide: pd.DataFrame, mode: str = None,
                          start: str = None, end: str = None, strict: bool = True) -> dict:
    """解析稳态模板窗口，返回 {start, end, mode, ret, vol_ratio, days, ok}。

    - manual：用给定 start/end，跑 validate_steady_window（不合法硬失败）
    - auto  ：在样本内滑窗扫描，挑「|区间涨跌| 最小 + 波动比最接近 1」的窗口，
              并要求窗口终点距样本末端 ≥ STEADY_MIN_GAP 个交易日（不用最近数据当模板）
    """
    mode = (mode or STEADY_MODE).lower()
    if mode == "manual":
        st = validate_steady_window(close_wide, start or STEADY_START, end or STEADY_END, strict=strict)
        st["mode"] = "manual"
        return st

    if len(close_wide) < STEADY_CANDIDATE_WIN + STEADY_MIN_GAP:
        raise SystemExit(
            f"[稳态模板] 样本仅 {len(close_wide)} 个交易日，不足以自动选窗"
            f"（需 ≥ {STEADY_CANDIDATE_WIN + STEADY_MIN_GAP}）。请拉长 start 或改用 STEADY_MODE='manual'。"
        )

    idx = close_wide.index
    last_allowed = len(idx) - STEADY_MIN_GAP
    cands = []
    for end_i in range(STEADY_CANDIDATE_WIN, last_allowed + 1, STEADY_CANDIDATE_STEP):
        seg = close_wide.iloc[end_i - STEADY_CANDIDATE_WIN:end_i]
        st = _window_stats(seg, seg.index[0], seg.index[-1])
        if not (np.isfinite(st["ret"]) and np.isfinite(st["vol_ratio"])):
            continue
        lo, hi = STEADY_VOL_RATIO_BAND
        if st["days"] < STEADY_MIN_DAYS or abs(st["ret"]) > STEADY_MAX_ABS_RET:
            continue
        if not (lo <= st["vol_ratio"] <= hi):
            continue
        # 评分：漂移越小越好、波动比越接近 1 越好
        st["score"] = abs(st["ret"]) * 3.0 + abs(st["vol_ratio"] - 1.0)
        cands.append(st)

    if not cands:
        raise SystemExit(
            "[稳态模板] auto 模式在样本内找不到合法窗口（涨跌 ≤ ±"
            f"{STEADY_MAX_ABS_RET:.0%} 且波动比在 {STEADY_VOL_RATIO_BAND} 内）。"
            "样本可能整体处于单边行情，请拉长 start 或改用 STEADY_MODE='manual' 并人工确认。"
        )
    best = min(cands, key=lambda x: x["score"])
    best["mode"] = "auto"
    best["ok"] = True
    best["candidates"] = len(cands)
    return best


def causal_rank(s: pd.Series, min_periods: int = PCT_MIN_PERIODS) -> pd.Series:
    """扩张窗分位（0-100，无未来函数）：t 时刻分位只用 ≤t 的样本估计。"""
    return s.expanding(min_periods=min_periods).apply(
        lambda x: float((x[-1] >= x).mean() * 100.0), raw=True)


def pct_rank(s: pd.Series, mode: str = None, min_periods: int = PCT_MIN_PERIODS) -> pd.Series:
    """按 PCT_MODE 给分位：causal=扩张窗（默认，无未来函数）；full=全样本后验（旧行为）。"""
    mode = (mode or PCT_MODE).lower()
    if mode == "full":
        return s.rank(pct=True) * 100.0
    return causal_rank(s, min_periods=min_periods)


def rolling_w1(v: np.ndarray, ref: np.ndarray, win: int = WIN) -> np.ndarray:
    """一维滚动 W1：当前 [i-win+1, i] 经验分布 vs 稳态模板 ref（scipy 精确解）。"""
    n = len(v)
    out = np.full(n, np.nan)
    ref = np.asarray(ref, dtype=float)
    ref = ref[~np.isnan(ref)]
    if len(ref) == 0:
        return out
    for i in range(win - 1, n):
        seg = v[i - win + 1: i + 1]
        seg = seg[~np.isnan(seg)]
        if len(seg) < 5:
            continue
        out[i] = wasserstein_distance(ref, seg)
    return out


def joint_w1(ref: np.ndarray, win: np.ndarray) -> float:
    """多维联合 W1：POT 精确解 / Sinkhorn 近似；无 POT 时用 numpy Sinkhorn 兜底。"""
    if ref is None or win is None or len(ref) == 0 or len(win) == 0:
        return np.nan
    M = cdist(np.asarray(ref, float), np.asarray(win, float), metric="euclidean")
    n, m = M.shape
    if n == 0 or m == 0:
        return np.nan
    a = np.full(n, 1.0 / n)
    b = np.full(m, 1.0 / m)
    if _pot is not None:
        try:
            if n * m <= JOINT_OT_MAX_CELLS:
                return float(_pot.emd2(a, b, M))
            return float(_pot.sinkhorn2(a, b, M, reg=SINKHORN_REG))
        except Exception:
            pass
    return _sinkhorn2_numpy(a, b, M, SINKHORN_REG)


def _sinkhorn2_numpy(a, b, M, reg, n_iter: int = 200):
    """纯 numpy Sinkhorn（POT 不可用时的兜底）。"""
    K = np.exp(-M / reg)
    u = np.ones_like(a)
    v = np.ones_like(b)
    for _ in range(n_iter):
        u = a / (K @ v)
        v = b / (K.T @ u)
    return float(np.sum(u[:, None] * K * v[None, :] * M))


# ============================================================================
# 靠岸：状态判定与大小盘分布
# ============================================================================

def _declining(v: np.ndarray, t: int, k: int) -> bool:
    if t < k or np.isnan(v[t]) or np.isnan(v[t - k]):
        return False
    return v[t] < v[t - k]


def _not_new_high(v: np.ndarray, t: int, k: int) -> bool:
    if t < 2 * k:
        return False
    recent = v[t - k + 1: t + 1]
    prior = v[t - 2 * k + 1: t - k + 1]
    return np.nanmax(recent) <= np.nanmax(prior) + 1e-12


def classify_state(W: np.ndarray, R: np.ndarray, J: np.ndarray, JR: np.ndarray,
                   t: int, big_idx: list, small_idx: list, codes: list) -> str:
    """六档状态（多数票，先严重后温和）。

    【P0-2 修复】原实现要求「六个指数全部满足」才成立（`np.all`），实测 340 个交易日：
    中性 278 天（82%），🟨靠岸中 / 🟧底部摩擦 两档从未触发（死代码）。
    现改为「有效指数里至少 MAJORITY 个满足」，并把「80→40 穿越」判定改用
    因果分位序列 R（不再对 W 做全样本 nanpercentile，去掉未来函数）。
    """
    C = W.shape[1]
    if t < DECLINE_K:
        return "⚪ 中性"
    r = R[t]
    valid = ~np.isnan(r)
    n = int(valid.sum())
    if n < 2:
        return "⚪ 中性"
    need = min(MAJORITY, n)
    rv = r[valid]
    high = int((rv > 80).sum())
    mid_high = int((rv > 60).sum())
    low = int((rv < 40).sum())
    med = float(np.nanmedian(rv))

    joint_high = bool(not np.isnan(JR[t]) and JR[t] > 80)
    joint_low = bool(not np.isnan(JR[t]) and JR[t] < JOINT_LOW_Q)
    joint_decl = bool((not np.isnan(J[t])) and (not np.isnan(J[t - DECLINE_K]))
                      and J[t] < J[t - DECLINE_K])
    plateau = sum(_not_new_high(W[:, c], t, PLATEAU_K) for c in range(C) if not np.isnan(W[t, c]))
    crossed = sum(
        1 for c in range(C)
        if (not np.isnan(r[c])) and r[c] < CROSSED_LOW
        and np.nanmax(R[max(0, t - CROSS_K): t + 1, c]) > 80
    )

    # 1) 共振崩塌：多数指数高位 + 联合 W1 高位
    if high >= need and joint_high:
        return "🟥 共振崩塌"
    # 2) 底部摩擦：多数指数偏高且不再创新高（跌势钝化）
    if mid_high >= need and plateau >= need:
        return "🟧 底部摩擦"
    # 3) 企稳可起：多数指数低位 + 联合 W1 也在低位且连降
    #    【P0-2 补充】只要求「联合 W1 连降」不够：4/6 个指数低位、但联合分布仍悬在
    #    高位时，只是「少数指数先回来」，不是全市场向稳态收敛。要求 JR < JOINT_LOW_Q。
    if low >= need and joint_low and joint_decl:
        return "🟩 企稳可起"
    # 4) 大盘先稳，小盘滞后：大盘全低位、小盘过半仍偏高
    if big_idx and small_idx:
        b, s = r[big_idx], r[small_idx]
        b_ok = bool(np.isfinite(b).all()) and bool(np.all(b < 40))
        s_bad = int(np.nansum(s > 60))
        if b_ok and s_bad >= max(1, (len(small_idx) + 1) // 2):
            return "ℹ️ 大盘先稳，小盘滞后"
    # 5) 靠岸中：足够多指数完成 80→40 穿越，整体分位不高
    if crossed >= min(CROSSED_MIN, n) and med < 60:
        return "🟨 靠岸中"
    return "⚪ 中性"


def classify_verdict(state: str, big_q: float, small_q: float, dd_from_high: float) -> str:
    """把六档状态翻译成“是否跌到头/跌完”的可读结论（状态识别，不是买卖信号）。"""
    if state == "🟩 企稳可起":
        return "已靠岸（跌到头信号最完整）"
    if state == "🟨 靠岸中":
        return "靠岸中（接近跌到头，尚未完成）"
    if state == "ℹ️ 大盘先稳，小盘滞后":
        return "大盘先靠岸、小盘未跌完"
    if state == "🟧 底部摩擦":
        return "底部摩擦（跌势钝化，但未确认跌完）"
    if state == "🟥 共振崩塌":
        return "未靠岸（仍在共振下跌）"
    if small_q - big_q > 20:
        return "未跌完（小盘明显滞后于大盘）"
    if dd_from_high < -20:
        return "未确认靠岸（回撤仍深）"
    return "中性（无明确靠岸证据）"


def build_landing_snapshot(start: str = None, end: str = None,
                           steady_start: str = STEADY_START, steady_end: str = STEADY_END,
                           codes: list = None, verbose: bool = True,
                           steady_mode: str = None, strict: bool = True,
                           pct_mode: str = None) -> dict:
    """靠岸快照：六指数 W1 + 联合 W1 + 状态 + 大小盘分布 + 回撤/动量。

    steady_mode：None→用顶部 STEADY_MODE；"auto" 自动选窗；"manual" 用 steady_start/end。
    strict     ：manual 窗口不合法时是否硬失败（P0-4）。
    pct_mode   ：None→用顶部 PCT_MODE；"causal" 无未来函数；"full" 旧行为。
    """
    codes = [c for c in (codes or list(INDEX_POOL)) if c in INDEX_POOL]
    end = end or date.today().isoformat()
    start = start or (pd.Timestamp(end) - pd.Timedelta(days=540)).strftime("%Y-%m-%d")

    raw = get_index_batch(codes, start, end)
    if len(raw) < 2:
        raise SystemExit("取到数据的指数 < 2 个，无法做联合诊断，请检查 DataHub 连接。")

    rn, close_map = {}, {}
    for c, df in raw.items():
        close_map[c] = df["close"]
        rn[c] = r_norm_series(df["close"])
    master = pd.DatetimeIndex(sorted(set.intersection(*(set(v.index) for v in rn.values()))))
    rn_wide = pd.DataFrame({c: rn[c].reindex(master) for c in rn}).dropna()
    # 模板体检用原始收盘价（未归一），才能算「区间涨跌幅」和「波动比」
    close_wide = pd.DataFrame({c: close_map[c].reindex(master) for c in rn_wide.columns}).ffill()

    # 【P0-1/P0-4】解析并校验稳态模板：auto 自动选「正常市」窗口，manual 走硬校验
    steady = resolve_steady_window(close_wide, mode=steady_mode,
                                   start=steady_start, end=steady_end, strict=strict)
    steady_start, steady_end = steady["start"], steady["end"]

    steady_mask = (rn_wide.index >= pd.Timestamp(steady_start)) & (rn_wide.index <= pd.Timestamp(steady_end))
    if steady_mask.sum() < STEADY_MIN_DAYS:
        raise SystemExit(
            f"[稳态模板] 窗口 {steady_start}~{steady_end} 内有效交易日仅 {int(steady_mask.sum())} 天"
            f"（下限 {STEADY_MIN_DAYS}），模板不可用，拒绝输出结论。"
        )
    fetched = [c for c in codes if c in rn_wide.columns]
    ref_vec = {c: rn_wide[c][steady_mask].values for c in fetched}
    ref_joint = rn_wide[steady_mask].values

    w_df = pd.DataFrame(index=rn_wide.index)
    for c in fetched:
        w_df[c] = rolling_w1(rn_wide[c].values, ref_vec[c], WIN)

    joint = pd.Series(np.nan, index=rn_wide.index)
    T = rn_wide.values
    for i in range(WIN - 1, len(T)):
        joint.iloc[i] = joint_w1(ref_joint, T[i - WIN + 1: i + 1])

    pct_lines = {c: {lv: float(np.nanpercentile(w_df[c], lv)) for lv in PCT_LEVELS} for c in fetched}
    # 【P1-5】分位口径统一走 pct_rank：causal=扩张窗（默认，无未来函数）
    rank_df = pd.DataFrame({c: pct_rank(w_df[c], mode=pct_mode) for c in fetched}, index=w_df.index)
    joint_rank = pct_rank(joint, mode=pct_mode)

    W, R, J, JR = w_df.values, rank_df.values, joint.values, joint_rank.values
    big_idx = [i for i, c in enumerate(fetched) if c in BIG_CAP]
    small_idx = [i for i, c in enumerate(fetched) if c in SMALL_CAP]
    labels = [classify_state(W, R, J, JR, t, big_idx, small_idx, fetched) for t in range(len(w_df))]
    states = pd.Series(labels, index=w_df.index)

    t_last = len(w_df) - 1
    detail = {}
    for c in fetched:
        close = close_map[c].reindex(master).ffill()
        adj = pd.Series(adjust_splits(close.values), index=close.index)
        ma40 = adj.rolling(MA_PERIOD).mean()
        ma160 = adj.rolling(160).mean()
        high_long = adj.iloc[-DD_LONG:].max() if len(adj) >= 20 else adj.max()
        high_short = adj.iloc[-DD_SHORT:].max() if len(adj) >= 20 else adj.max()
        ret = lambda k: (adj.iloc[-1] / adj.iloc[-1 - k] - 1) * 100 if len(adj) > k else np.nan
        w_last = float(w_df[c].iloc[-1])
        w_5ago = float(w_df[c].iloc[-6]) if len(w_df) > 6 else np.nan
        detail[c] = {
            "name": INDEX_POOL[c][0],
            "short": INDEX_POOL[c][1],
            "cap": INDEX_POOL[c][2],
            "w1": w_last,
            "w1_pct": float(rank_df[c].iloc[-1]),
            "w1_chg5": (w_last - w_5ago) if not np.isnan(w_5ago) else np.nan,
            "dd_long": (adj.iloc[-1] / high_long - 1) * 100,
            "dd_short": (adj.iloc[-1] / high_short - 1) * 100,
            "ret5": ret(5), "ret20": ret(20), "ret80": ret(80),
            "above_ma40": bool(adj.iloc[-1] > ma40.iloc[-1]) if not np.isnan(ma40.iloc[-1]) else None,
            "above_ma160": bool(adj.iloc[-1] > ma160.iloc[-1]) if not np.isnan(ma160.iloc[-1]) else None,
            "close": float(adj.iloc[-1]),
            # 漂移方向：近 15 日 r_norm 均值 - 稳态模板均值。>0 上行偏离、<0 下行偏离。
            # W1 只是“距离”没有方向，必须靠它区分“涨出来的偏离”和“跌出来的偏离”。
            "dir_mean": float(np.nanmean(rn_wide[c].iloc[-WIN:].values) - np.nanmean(ref_vec[c])),
        }

    big_q = float(np.mean([detail[c]["w1_pct"] for c in fetched if c in BIG_CAP])) if big_idx else np.nan
    small_q = float(np.mean([detail[c]["w1_pct"] for c in fetched if c in SMALL_CAP])) if small_idx else np.nan
    mid_q = float(np.mean([detail[c]["w1_pct"] for c in fetched if c == "000905.SH"])) if "000905.SH" in fetched else np.nan
    state_now = states.iloc[-1]
    dd_mean = float(np.mean([detail[c]["dd_long"] for c in fetched]))
    verdict = classify_verdict(state_now, big_q, small_q, dd_mean)
    gap = (small_q - big_q) if not (np.isnan(big_q) or np.isnan(small_q)) else np.nan

    snap = {
        "as_of": str(master[-1].date()),
        "window": {"win": WIN, "roll_std": ROLL_STD, "steady": f"{steady_start}~{steady_end}",
                   "steady_mode": steady.get("mode"), "pct_mode": (pct_mode or PCT_MODE)},
        "steady": steady,
        "codes": fetched,
        "detail": detail,
        "w_df": w_df, "rank_df": rank_df, "joint": joint, "joint_rank": joint_rank,
        "states": states, "pct_lines": pct_lines,
        "state": state_now,
        "state_streak": int((states.iloc[::-1] == state_now).cumprod().sum()),
        "big_pct": big_q, "small_pct": small_q, "mid_pct": mid_q,
        "big_small_gap": gap,
        "joint_w1": float(joint.iloc[-1]) if not np.isnan(joint.iloc[-1]) else np.nan,
        "joint_pct": float(joint_rank.iloc[-1]) if not np.isnan(joint_rank.iloc[-1]) else np.nan,
        "dd_mean": dd_mean,
        "verdict": verdict,
    }
    if verbose:
        print(f"[靠岸] 数据日 {snap['as_of']}  状态 {state_now}  大盘均值分位 {big_q:.0f}  小盘 {small_q:.0f}  差值 {gap:+.0f}")
        print(f"[靠岸] 稳态模板 {steady_start}~{steady_end}（{steady['mode']}，{steady['days']} 日，"
              f"涨跌 {steady['ret']:+.1%}，波动比 {steady['vol_ratio']:.2f}）")
    return snap


# ============================================================================
# 耦合矩阵：成对 W1 / 尾部依赖 / 谱共振
# ============================================================================

def _pseudo_obs(x: np.ndarray) -> np.ndarray:
    from scipy.stats import rankdata
    return (rankdata(x) - 0.5) / len(x)


def _tail_dep(u: np.ndarray, v: np.ndarray, level: float) -> float:
    return float(((u <= level) & (v <= level)).mean() / level)


def build_coupling_snapshot(start: str = None, end: str = None, codes: list = None,
                            win: int = WIN, tail_win: int = TAIL_WIN,
                            level: float = TAIL_LEVEL, verbose: bool = True) -> dict:
    """耦合快照：最新成对 W1 矩阵、λ_L/λ_U 矩阵、谱共振与时间序列。"""
    codes = [c for c in (codes or list(INDEX_POOL)) if c in INDEX_POOL]
    end = end or date.today().isoformat()
    start = start or (pd.Timestamp(end) - pd.Timedelta(days=540)).strftime("%Y-%m-%d")
    raw = get_index_batch(codes, start, end)
    rn = {}
    for c, df in raw.items():
        rn[c] = r_norm_series(df["close"])
    fetched = [c for c in codes if c in rn]
    if len(fetched) < 3:
        raise SystemExit("数据不足 3 个指数，无法做两两耦合。")
    master = pd.DatetimeIndex(sorted(set.intersection(*(set(rn[c].index) for c in fetched))))
    rn_wide = pd.DataFrame({c: rn[c].reindex(master) for c in fetched}).dropna()
    T, N = len(rn_wide), len(fetched)
    import itertools
    pairs = list(itertools.combinations(fetched, 2))
    idx_of = {c: i for i, c in enumerate(fetched)}

    w1d = {p: np.full(T, np.nan) for p in pairs}
    lamL = {p: np.full(T, np.nan) for p in pairs}
    lamU = {p: np.full(T, np.nan) for p in pairs}
    for t in range(T):
        if t >= win - 1:
            w = rn_wide.iloc[t - win + 1: t + 1]
            for p in pairs:
                w1d[p][t] = wasserstein_distance(w[p[0]].values, w[p[1]].values)
        if t >= tail_win - 1:
            w = rn_wide.iloc[t - tail_win + 1: t + 1]
            for p in pairs:
                ua, ub = _pseudo_obs(w[p[0]].values), _pseudo_obs(w[p[1]].values)
                lamL[p][t] = _tail_dep(ua, ub, level)
                lamU[p][t] = _tail_dep(1 - ua, 1 - ub, level)

    mean_w1 = np.nanmean([w1d[p] for p in pairs], axis=0)
    mean_lamL = np.nanmean([lamL[p] for p in pairs], axis=0)
    mean_lamU = np.nanmean([lamU[p] for p in pairs], axis=0)
    resonance = np.full(T, np.nan)
    for t in range(tail_win - 1, T):
        M = np.eye(N)
        for p in pairs:
            v = lamL[p][t]
            if not np.isnan(v):
                i, j = idx_of[p[0]], idx_of[p[1]]
                M[i, j] = M[j, i] = v
        resonance[t] = np.linalg.eigvalsh(M)[-1] / N

    # ------------------------------------------------------------------
    # 【P1-1 修复】谱共振去冗余。
    # 数学上：对等相关矩阵 M=(1-ρ)I+ρJ 有 λmax/N = 1/N + (1-1/N)·ρ。
    # 实测本仓库 340 日样本 corr(λmax/N, 平均λ_L)=0.9976，线性拟合
    #   λmax/N ≈ 0.191 + 0.803·λ_L（理论截距 1/6=0.167、斜率 1-1/6=0.833）
    # 残差 σ=0.0027，而 λmax/N 自身 σ=0.0337 → 99.5% 的方差由平均尾依赖解释。
    # 所以原实现「res>q80 且 lamL>q60」判红，等于用同一个统计量判了两遍，
    # 却对外宣称是「双指标确认」，这是虚假冗余。
    # 修复方式：红/绿阶段只由 λ_L 分位驱动（λ_L 是真正在变、且有区分度的量），
    # λmax/N 降级为展示指标，并把它的「可解释水平项」扣掉，只留结构残差
    # resonance_excess（尾部依赖在各对之间的集中程度），单独报告不再参与判色。
    # 经验验证（2025-01~2026-11，沪深300 前瞻收益，全样本 后20日 +0.11%）：
    #   λ_L>q20  后20日 +0.76%（n=52）    λ_L<q40 后20日 -0.37%
    #   λ_L>q60  后20日 -1.22%（n=112）   λ_L>q80 后20日 -1.15%（n=52）
    # 分位单调性成立，红绿两端的 20 日收益差约 2pp。
    # ------------------------------------------------------------------
    resonance_base = 1.0 / N + (1.0 - 1.0 / N) * mean_lamL
    resonance_excess = resonance - resonance_base

    res_q = {q: float(np.nanpercentile(resonance, q)) for q in PCT_LEVELS}
    exc_q = {q: float(np.nanpercentile(resonance_excess, q)) for q in RESIDUAL_EXCESS_Q}
    ll_q = {q: float(np.nanpercentile(mean_lamL, q)) for q in PCT_LEVELS}
    ll_hi = float(np.nanpercentile(mean_lamL, PHASE_LL_HIGH_Q))
    ll_lo = float(np.nanpercentile(mean_lamL, PHASE_LL_LOW_Q))
    phase = np.full(T, None, dtype=object)
    for t in range(T):
        l = mean_lamL[t]
        if np.isnan(l):
            continue
        if l > ll_hi:
            phase[t] = "red"
        elif l < ll_lo:
            phase[t] = "green"

    t_last = T - 1
    D, LL, LU = np.zeros((N, N)), np.eye(N), np.eye(N)
    for p in pairs:
        i, j = idx_of[p[0]], idx_of[p[1]]
        D[i, j] = D[j, i] = w1d[p][t_last]
        LL[i, j] = LL[j, i] = lamL[p][t_last]
        LU[i, j] = LU[j, i] = lamU[p][t_last]

    cur = phase[t_last]
    streak = 0
    for p in phase[::-1]:
        if p == cur:
            streak += 1
        else:
            break
    snap = {
        "as_of": str(master[-1].date()),
        "codes": fetched,
        "labels": [INDEX_POOL[c][1] for c in fetched],
        "D": D, "LL": LL, "LU": LU,
        "mean_w1": pd.Series(mean_w1, index=master),
        "mean_lamL": pd.Series(mean_lamL, index=master),
        "mean_lamU": pd.Series(mean_lamU, index=master),
        "resonance": pd.Series(resonance, index=master),
        "resonance_base": pd.Series(resonance_base, index=master),
        "resonance_excess": pd.Series(resonance_excess, index=master),
        "phase": pd.Series(phase, index=master),
        "res_q": res_q, "exc_q": exc_q, "ll_q": ll_q,
        "resonance_now": float(resonance[t_last]) if not np.isnan(resonance[t_last]) else np.nan,
        "resonance_excess_now": float(resonance_excess[t_last]) if not np.isnan(resonance_excess[t_last]) else np.nan,
        "lamL_now": float(mean_lamL[t_last]) if not np.isnan(mean_lamL[t_last]) else np.nan,
        "lamU_now": float(mean_lamU[t_last]) if not np.isnan(mean_lamU[t_last]) else np.nan,
        "w1_now": float(mean_w1[t_last]) if not np.isnan(mean_w1[t_last]) else np.nan,
        "phase_now": cur, "phase_streak": streak,
    }
    if verbose:
        print(f"[耦合] λmax/N={snap['resonance_now']:.3f}（残差 {snap['resonance_excess_now']:+.4f}）  "
              f"平均λ_L={snap['lamL_now']:.3f}  平均W1={snap['w1_now']:.3f}  阶段={cur}（连续{streak}日）")
    return snap


# ============================================================================
# OT 漂移信号（原日报口径）：双窗口加权 W1 + 80 日动量趋势优先
# ============================================================================

def drift_w1(close: pd.Series, windows=DRIFT_WINDOWS, weights=DRIFT_WEIGHTS):
    """双窗口加权 W1（40/60，0.6/0.4）与前一周分布的方向。"""
    adj = adjust_splits(close.values)
    ret = pd.Series(adj, index=close.index).pct_change()
    w1_list, dir_list = [], []
    for w in windows:
        n = len(ret)
        w1 = np.full(n, np.nan)
        d = np.full(n, 0.0)
        vals = ret.values
        for i in range(w * 2, n):
            prev = vals[i - w * 2:i - w]
            curr = vals[i - w:i]
            mp, mc = ~np.isnan(prev), ~np.isnan(curr)
            if mp.sum() < 5 or mc.sum() < 5:
                continue
            p, q = np.sort(prev[mp]), np.sort(curr[mc])
            ml = min(len(p), len(q))
            w1[i] = np.mean(np.abs(q[:ml] - p[:ml]))
            d[i] = np.sign(np.mean(q) - np.mean(p))
        w1_list.append(w1)
        dir_list.append(d)
    n = len(w1_list[0])
    combined = np.full(n, np.nan)
    direction = np.full(n, 0.0)
    for i in range(n):
        vals, dirs = [], []
        for j in range(len(w1_list)):
            if not np.isnan(w1_list[j][i]):
                vals.append((w1_list[j][i], weights[j]))
                dirs.append(dir_list[j][i])
        if vals:
            tw = sum(w for _, w in vals)
            if tw > 0:
                combined[i] = sum(v * w / tw for v, w in vals)
                direction[i] = np.sign(sum(dirs))
    return combined, direction


def drift_signal(close: pd.Series) -> dict:
    """原日报单标的信号：W1 + 方向 + 80 日动量 + MA40/160 趋势 + 分位阈值。

    【P1-2 修复】原实现把输出写成「看多 / 看空 / 观望」，直接违反本项目的设计硬约束
    #1（Wasserstein 距离衡量的是「当前收益分布与稳态模板分布的偏离程度」，是状态
    识别量，不是方向预测，更不是买卖建议）。W1 变小只说明「分布更像稳态」，不代表
    未来上涨；同理 dv>0 只说明「偏离在扩大」，不等于要跌。
    现在统一改成描述分布状态的措辞（漂移上行 / 漂移下行 / 漂移中性 / 高漂移），
    去掉一切交易指令性语言；`signal` 键名保留以兼容旧调用方。
    """
    w1, d = drift_w1(close)
    adj = pd.Series(adjust_splits(close.values), index=close.index)
    ma40 = adj.rolling(MA_PERIOD).mean()
    ma160 = adj.rolling(160).mean()
    i = len(close) - 1
    valid = w1[~np.isnan(w1)]
    q30, q70, q90 = (np.percentile(valid, [30, 70, 90]) if len(valid) > 20 else (0, 0, 0))
    w1v, dv = w1[i], d[i]
    mom80 = (adj.iloc[i] / adj.iloc[i - 80] - 1) * 100 if i >= 80 else np.nan
    bear = bool(mom80 < 0) if not np.isnan(mom80) else False
    if not np.isnan(w1v):
        # 措辞统一为分布状态描述，不构成任何交易指令（硬约束 #1）
        base = "漂移上行" if dv > 0 else ("漂移下行" if dv < 0 else "漂移中性")
        if bear:
            sig = base + ("·高漂移" if w1v > q90 else "")
        elif w1v > q90:
            sig = "高漂移·方向未明"
        else:
            sig = base
    else:
        sig = "N/A"
    ret = lambda k: (adj.iloc[i] / adj.iloc[i - k] - 1) * 100 if i >= k else np.nan
    return {"w1": float(w1v) if not np.isnan(w1v) else 0.0, "dir": float(dv),
            "q30": q30, "q70": q70, "q90": q90, "signal": sig,
            "ma40": bool(adj.iloc[i] > ma40.iloc[i]) if not np.isnan(ma40.iloc[i]) else None,
            "ma160": bool(adj.iloc[i] > ma160.iloc[i]) if not np.isnan(ma160.iloc[i]) else None,
            "ret5": ret(5), "ret20": ret(20), "mom80": mom80}


# ============================================================================
# 任意标的分析（指数 / ETF / 个股）
# ============================================================================
# 与六指数靠岸的区别：任意标的没有人工确认的稳态模板，改用
# W1(t) = W1(最近 win 日分布, 再往前 ref_win 日分布)，再对 W1 序列取滚动 q_win 日分位，
# 用「相对自身历史的漂移分位」自校准，无需选模板，适用于任意指数/ETF/个股。

INDEX_CODES = {
    "000001.SH", "000016.SH", "000300.SH", "000688.SH", "000852.SH", "000905.SH",
    "000906.SH", "000985.SH", "399001.SZ", "399005.SZ", "399006.SZ", "399300.SZ",
    "899050.BJ", "932000.CSI",
}

# 常用 ETF 中文名（仅用于报告可读性，取不到就显示代码）
ETF_NAMES = {
    "510300.SH": "沪深300ETF", "510500.SH": "中证500ETF", "512100.SH": "中证1000ETF",
    "588000.SH": "科创50ETF", "510050.SH": "上证50ETF", "159915.SZ": "创业板ETF",
    "512480.SH": "半导体ETF", "512800.SH": "银行ETF", "512000.SH": "券商ETF",
    "512690.SH": "酒ETF", "512660.SH": "军工ETF", "515790.SH": "光伏ETF",
    "159992.SZ": "创新药ETF", "512010.SH": "医药ETF", "515050.SH": "5GETF",
    "515030.SH": "新能源车ETF",
}


def normalize_code(code: str, kind: str = None) -> tuple:
    """返回 (ts_code, kind)。kind=index/etf/stock；未指定时按代码特征推断。"""
    c = str(code).strip().upper()
    if c.lower().startswith("sh") or c.lower().startswith("sz"):
        c = c[2:] + ("." + c[:2].upper())
    if "." not in c:
        six = c
        if six.startswith(("51", "56", "58", "15", "16", "159")):
            c = six + ".SH" if six.startswith(("51", "56", "58")) else six + ".SZ"
            kind = kind or "etf"
        elif six.startswith(("000", "399", "899", "932")):
            c = six + (".SH" if six.startswith("000") else ".SZ")
            kind = kind or "index"
        else:
            c = six + (".SH" if six[0] in "6035" else ".SZ")
            kind = kind or "stock"
    if kind is None:
        if c in INDEX_CODES or c.startswith("399"):
            kind = "index"
        elif c[:6].startswith(("51", "56", "58", "15", "16")):
            kind = "etf"
        else:
            kind = "stock"
    return c, kind


def load_symbol(code: str, start: str, end: str, kind: str = None) -> tuple:
    """载入任意标的日线：返回 (name, df[close, vol], kind)。"""
    ts_code, kind = normalize_code(code, kind)
    dh = _dh()
    df = None
    if kind == "index":
        df = dh.get_index_daily(ts_code=ts_code, start_date=start.replace("-", ""), end_date=end.replace("-", ""))
    elif kind == "etf":
        df = dh.get_etf_daily(ts_code=ts_code, start_date=start.replace("-", ""), end_date=end.replace("-", ""))
    elif kind == "stock":
        df = dh.get_stock_daily(ts_code=ts_code, start_date=start.replace("-", ""), end_date=end.replace("-", ""), adjust="qfq")
    if df is None or not isinstance(df, pd.DataFrame) or len(df) < 60:
        # 类型推断失败时按「推断类型优先」逐个回退，避免把个股误当指数（指数接口对个股也会返回数据）
        for k in [kind] + [x for x in ("stock", "etf", "index") if x != kind]:
            if k == kind:
                continue
            try:
                if k == "index":
                    d = dh.get_index_daily(ts_code=ts_code, start_date=start.replace("-", ""), end_date=end.replace("-", ""))
                elif k == "etf":
                    d = dh.get_etf_daily(ts_code=ts_code, start_date=start.replace("-", ""), end_date=end.replace("-", ""))
                else:
                    d = dh.get_stock_daily(ts_code=ts_code, start_date=start.replace("-", ""), end_date=end.replace("-", ""))
                if isinstance(d, pd.DataFrame) and len(d) >= 60:
                    df, kind = d, k
                    break
            except Exception:
                continue
    if df is None or len(df) < 60:
        raise RuntimeError(f"DataHub 未能取到 {ts_code}（{kind}）足够日线数据")
    out = _norm_dates(df)
    out = out.dropna(subset=["close"])
    name = ts_code
    if kind == "index" and ts_code in INDEX_POOL:
        name = INDEX_POOL[ts_code][0]
    elif kind == "etf":
        name = ETF_NAMES.get(ts_code, ts_code)
    return name, out, kind


def rolling_w1_self(close: pd.Series, win: int = WIN, ref_win: int = 250, q_win: int = 252):
    """W1(t)=W1(最近 win 日分布, 前 ref_win 日分布)；分位为 W1 序列的滚动 q_win 日分位。"""
    r = r_norm_series(close)
    v = r.values
    n = len(v)
    w1 = np.full(n, np.nan)
    need = win + ref_win
    for i in range(need - 1, n):
        cur = v[i - win + 1: i + 1]
        ref = v[i - need + 1: i - win + 1]
        if np.isnan(cur).any() or np.isnan(ref).any():
            continue
        w1[i] = wasserstein_distance(ref, cur)
    w = pd.Series(w1, index=r.index)
    pct = w.rolling(q_win, min_periods=60).apply(lambda s: float((s.iloc[-1] >= s).mean() * 100), raw=False)
    return w, pct


def analyze_symbol(code: str, start: str = None, end: str = None, kind: str = None,
                   bench: str = "000300.SH", verbose: bool = True) -> dict:
    """任意标的的靠岸/漂移分析：W1 自校准分位 + 回撤 + 均线 + 动量 + 与基准的耦合。"""
    end = end or date.today().isoformat()
    start = start or (pd.Timestamp(end) - pd.Timedelta(days=900)).strftime("%Y-%m-%d")
    ts_code, _kind_guess = normalize_code(code, kind)
    name, df, kind = load_symbol(code, start, end, kind)
    close = df["close"]
    w, q = rolling_w1_self(close)
    adj = pd.Series(adjust_splits(close.values), index=close.index)
    ma40 = adj.rolling(MA_PERIOD).mean()
    ma160 = adj.rolling(160).mean()
    i = len(adj) - 1
    q_now = float(q.iloc[-1]) if not np.isnan(q.iloc[-1]) else np.nan
    w_now = float(w.iloc[-1]) if not np.isnan(w.iloc[-1]) else np.nan
    w_5 = float(w.iloc[-6]) if len(w) > 6 and not np.isnan(w.iloc[-6]) else np.nan
    dw = (w_now - w_5) if not (np.isnan(w_now) or np.isnan(w_5)) else np.nan
    high_l = adj.iloc[-DD_LONG:].max()
    low_l = adj.iloc[-DD_LONG:].min()
    ret = lambda k: (adj.iloc[i] / adj.iloc[i - k] - 1) * 100 if i >= k else np.nan
    if np.isnan(q_now):
        state = "数据不足"
    elif q_now < 40:
        state = "已靠岸" if (not np.isnan(dw) and dw <= 0) else "靠岸后重新漂移（警惕二次下跌）"
    elif q_now < 60:
        state = "靠岸中"
    elif q_now < 80:
        state = "未靠岸（仍在漂移）"
    else:
        state = "深度漂移（下跌/异常未结束）"

    # 与基准的耦合：收益分布差异 + 下尾依赖
    bench_info = {}
    try:
        _bcode, _ = normalize_code(bench)
        if _bcode == ts_code:
            raise RuntimeError("标的与基准相同")
        bname, bdf, bkind = load_symbol(bench, start, end)
        merged = pd.concat([df["close"].pct_change().rename("a"), bdf["close"].pct_change().rename("b")], axis=1).dropna()
        if len(merged) >= 60:
            a, b = merged["a"].values, merged["b"].values
            bench_info = {
                "bench": bname,
                "pair_w1": float(wasserstein_distance(a[-WIN:], b[-WIN:])),
                "corr60": float(pd.Series(a[-60:]).corr(pd.Series(b[-60:]))),
                "tail_l": float(_tail_dep(_pseudo_obs(a[-TAIL_WIN:]), _pseudo_obs(b[-TAIL_WIN:]), TAIL_LEVEL)),
                "rel20": float((adj.iloc[-1] / adj.iloc[-21] - bdf["close"].iloc[-1] / bdf["close"].iloc[-21]) * 100) if len(adj) > 21 else np.nan,
            }
    except Exception as e:
        bench_info = {"error": str(e)[:80]}

    snap = {
        "code": code, "ts_code": ts_code, "name": name, "kind": kind,
        "as_of": str(close.index[-1].date()),
        "window": {"win": WIN, "ref_win": 250, "q_win": 252},
        "w1": w_now, "w1_pct": q_now, "w1_chg5": dw,
        "dd_long": (adj.iloc[-1] / high_l - 1) * 100, "pos_low": (adj.iloc[-1] / low_l - 1) * 100,
        "close": float(adj.iloc[-1]),
        "ret5": ret(5), "ret20": ret(20), "ret80": ret(80),
        "ma40": bool(adj.iloc[-1] > ma40.iloc[-1]) if not np.isnan(ma40.iloc[-1]) else None,
        "ma160": bool(adj.iloc[-1] > ma160.iloc[-1]) if not np.isnan(ma160.iloc[-1]) else None,
        "state": state, "bench": bench_info,
        "w1_series": w, "pct_series": q, "close_series": adj,
    }
    if verbose:
        print(f"[标的] {name}（{ts_code}）{snap['as_of']} "
              f"W1={w_now:.4f} 分位={q_now:.0f} 5日ΔW1={dw:+.4f} 回撤={snap['dd_long']:+.1f}% → {state}")
    return snap


# ============================================================================
# 申万一级行业靠岸（31 个行业）
# ============================================================================
# 数据源：DataHub `sw_index_daily`（akshare index_hist_sw 直连申万，含 801xxx 全历史），
# 不是 get_index_daily（后者对 801xxx 返回的是停更/错序数据，实测最新只到 2025-06）。

SW_L1 = [
    ("801010", "农林牧渔"), ("801030", "基础化工"), ("801040", "钢铁"), ("801050", "有色金属"),
    ("801080", "电子"), ("801110", "家用电器"), ("801120", "食品饮料"), ("801130", "纺织服饰"),
    ("801140", "轻工制造"), ("801150", "医药生物"), ("801160", "公用事业"), ("801170", "交通运输"),
    ("801180", "房地产"), ("801200", "商贸零售"), ("801210", "社会服务"), ("801230", "综合"),
    ("801710", "建筑材料"), ("801720", "建筑装饰"), ("801730", "电力设备"), ("801740", "国防军工"),
    ("801750", "计算机"), ("801760", "传媒"), ("801770", "通信"), ("801780", "银行"),
    ("801790", "非银金融"), ("801880", "汽车"), ("801890", "机械设备"), ("801950", "煤炭"),
    ("801960", "石油石化"), ("801970", "环保"), ("801980", "美容护理"),
]


def get_sw_industry(code: str, start: str, end: str = None) -> pd.DataFrame:
    """申万行业指数日线（DataHub sw_index_daily）。"""
    dh = _dh()
    kw = {"code": str(code), "start_date": start.replace("-", "")}
    if end:
        kw["end_date"] = end.replace("-", "")
    df = dh.get_data("sw_index_daily", **kw)
    out = _norm_dates(df)
    if out.empty:
        raise RuntimeError(f"sw_index_daily 未返回 {code}")
    return out


def build_industry_landing(start: str = None, end: str = None, codes: list = None,
                           workers: int = 1, verbose: bool = True,
                           steady_start: str = None, steady_end: str = None,
                           steady: dict = None, pct_mode: str = None) -> dict:
    """31 个申万一级行业的靠岸快照，口径与六指数一致（同一稳态模板 + 同一分位口径）。

    ⚠️ 不用自校准口径（前 250 日参考窗）：在持续下跌市里参考窗本身含大跌段，
    会把"跌久了"误判成"靠岸"（实测 31 个行业会全部落在低分位）。用同一稳态模板才与宽基可比。

    【P1-3 修复】原来这里默认吃 STEADY_START/END 硬编码窗，且分位用全样本 rank
    （含未来函数），与指数层（auto 选窗 + 因果分位）口径分裂——同一个交易日，
    创业板指在指数层是 71 分位、在行业层同口径会算成 77。现在：
    · 传 steady=snap["steady"] 直接复用指数层已经校验过的窗口；
    · 未传时按 STEADY_MODE 走同一套 resolve_steady_window 逻辑（需要指数收盘价）；
    · 分位统一走 pct_rank(mode=pct_mode)，默认与顶部 PCT_MODE 一致（因果）。
    workers 默认 1：akshare index_hist_sw 并发会被限流，串行 31 只约 30-60s。
    """
    from concurrent.futures import ThreadPoolExecutor

    end = end or date.today().isoformat()
    start = start or (pd.Timestamp(end) - pd.Timedelta(days=560)).strftime("%Y-%m-%d")
    # 未显式给窗口/未复用指数层窗口时，用指数收盘价按同一套规则选窗并校验
    if steady is None:
        if steady_start is None or steady_end is None:
            try:
                idx_raw = get_index_batch(list(INDEX_POOL), start, end)
                close_wide = pd.DataFrame(
                    {c: idx_raw[c]["close"] for c in idx_raw if c in INDEX_POOL})
                close_wide.index = pd.to_datetime(close_wide.index)
                steady = resolve_steady_window(close_wide, mode=STEADY_MODE, strict=False)
            except Exception as e:
                print(f"  [行业] 自动选稳态窗口失败（{e}），回退到 STEADY_START/END")
                steady = None
        else:
            steady = {"start": steady_start, "end": steady_end}
    steady_start = (steady or {}).get("start") or steady_start or STEADY_START
    steady_end = (steady or {}).get("end") or steady_end or STEADY_END
    pool = [(c, n) for c, n in SW_L1 if (codes is None or c in codes)]

    def one(item):
        code, name = item
        try:
            df = get_sw_industry(code, start, end)
            close = df["close"]
            if len(close) < 300:
                return {"code": code, "name": name, "ok": False, "err": f"仅{len(close)}行"}
            rn = r_norm_series(close)
            ref = rn[(rn.index >= pd.Timestamp(steady_start)) & (rn.index <= pd.Timestamp(steady_end))]
            if len(ref) < 5:
                return {"code": code, "name": name, "ok": False, "err": "模板窗内无数据"}
            w = pd.Series(rolling_w1(rn.values, ref.values, WIN), index=rn.index).dropna()
            if len(w) < 60:
                return {"code": code, "name": name, "ok": False, "err": "W1 样本不足"}
            pct = pct_rank(w, mode=pct_mode)
            adj = pd.Series(adjust_splits(close.values), index=close.index)
            ma40 = adj.rolling(MA_PERIOD).mean()
            i = len(adj) - 1
            qn = float(pct.iloc[-1])
            wn = float(w.iloc[-1])
            w5 = float(w.iloc[-6]) if len(w) > 6 else np.nan
            return {
                "code": code, "name": name, "ok": True,
                "w1": wn, "pct": qn, "dw5": (wn - w5) if not np.isnan(w5) else np.nan,
                "dd": (adj.iloc[i] / adj.iloc[-DD_LONG:].max() - 1) * 100,
                "pos_low": (adj.iloc[i] / adj.iloc[-DD_LONG:].min() - 1) * 100,
                "ret20": (adj.iloc[i] / adj.iloc[i - 20] - 1) * 100 if i >= 20 else np.nan,
                "above_ma40": bool(adj.iloc[i] > ma40.iloc[i]) if not np.isnan(ma40.iloc[i]) else None,
                "as_of": str(close.index[-1].date()),
                # 上行/下行偏离：>0 涨出来的偏离，<0 跌出来的偏离
                "dir_mean": float(np.nanmean(rn.iloc[-WIN:].values) - np.nanmean(ref.values)),
            }
        except Exception as e:
            return {"code": code, "name": name, "ok": False, "err": str(e)[:60]}

    with ThreadPoolExecutor(max_workers=workers) as ex:
        rows = list(ex.map(one, pool))
    ok = [r for r in rows if r["ok"]]
    ok.sort(key=lambda r: (np.isnan(r["pct"]), r["pct"]))
    n_low = sum(1 for r in ok if r["pct"] < 40)
    n_mid = sum(1 for r in ok if 40 <= r["pct"] < 60)
    n_down = sum(1 for r in ok if r["pct"] >= 60 and (r.get("dir_mean") or 0) <= 0)
    n_up = sum(1 for r in ok if r["pct"] >= 60 and (r.get("dir_mean") or 0) > 0)
    n_high = n_down + n_up
    snap = {
        "as_of": max([r["as_of"] for r in ok]) if ok else end,
        "rows": ok,
        "failed": [r for r in rows if not r["ok"]],
        "n_total": len(rows),
        "n_low": n_low, "n_mid": n_mid, "n_high": n_high,
        "n_down": n_down, "n_up": n_up,
        "median_pct": float(np.nanmedian([r["pct"] for r in ok])) if ok else np.nan,
    }
    if verbose:
        print(f"[行业靠岸] {snap['as_of']} 成功 {len(ok)}/{len(rows)}｜已靠岸 {n_low}｜靠岸中 {n_mid}"
              f"｜上行偏离 {n_up}｜下跌未靠岸 {n_down}｜中位分位 {snap['median_pct']:.0f}")
    return snap


if __name__ == "__main__":
    s = build_landing_snapshot()
    print("\n指数明细：")
    for c, d in s["detail"].items():
        print(f"  {d['name']:<8} W1={d['w1']:.4f} 分位={d['w1_pct']:.0f} "
              f"5日ΔW1={d['w1_chg5']:+.4f} 回撤250={d['dd_long']:+.1f}% "
              f"20日={d['ret20']:+.2f}% MA40{'↑' if d['above_ma40'] else '↓'}")
    print(f"\n结论：{s['verdict']}")
    c = build_coupling_snapshot()