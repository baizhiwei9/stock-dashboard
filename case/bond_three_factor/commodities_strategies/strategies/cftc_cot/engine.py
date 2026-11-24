# -*- coding: utf-8 -*-
"""引擎：组装面板 → 算 COT / episode / 结论 → 落 SQLite → 跑事件与胜率统计。

对外只有两个入口：
  · `build_state(...)`  ：当前状态快照（给报告/推送用）
  · `run_backtest(...)` ：事件胜率 + Q 五口径 IC/净值统计（给 --backtest 用）

全部为规则计算；数据取不到时降级为 warning，不抛异常中断整批。
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
import yaml

from ...config import ROOT, Settings, load_settings
from ...storage.sqlite_store import SQLiteStore
from . import cot as COT
from . import prices as PX
from . import signals as SG
from .universe import Commodity, select

warnings.filterwarnings("ignore", category=FutureWarning)

CONFIG_PATH = ROOT / "config" / "cftc_cot.yaml"
DEFAULT_OUT = ROOT / "data" / "cftc_cot"


# ======================================================================
# 配置
# ======================================================================
def load_config(path: Optional[Path] = None) -> dict:
    p = Path(path) if path else CONFIG_PATH
    with p.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@dataclass
class CftcConfig:
    keys: List[str] = field(default_factory=list)
    lookback_weeks: int = SG.COT_LOOKBACK
    min_obs: int = SG.COT_MIN_OBS
    hi: float = SG.HI_DEFAULT
    lo: float = SG.LO_DEFAULT
    win_min: int = SG.WIN_MIN
    win_max: int = SG.WIN_MAX
    hist_avg_bp: Dict[str, float] = field(default_factory=lambda: dict(SG.HIST_AVG_BP))
    hist_weekly_hit: Dict[str, float] = field(default_factory=lambda: dict(SG.HIST_WEEKLY_HIT))
    start_year: int = 2019
    foreign_align: str = "fri"
    domestic_align: str = "mon"
    roll_weeks: int = 52
    min_obs_reg: int = 26
    out_dir: Path = DEFAULT_OUT
    fig_dir: Path = DEFAULT_OUT / "fig"

    @classmethod
    def from_dict(cls, raw: dict) -> "CftcConfig":
        u = raw.get("universe") or {}
        s = raw.get("signal") or {}
        r = raw.get("regression") or {}
        o = raw.get("output") or {}
        out_dir = ROOT / o.get("dir", "data/cftc_cot")
        return cls(
            keys=list(u.get("commodities") or []),
            lookback_weeks=int(s.get("lookback_weeks", SG.COT_LOOKBACK)),
            min_obs=int(s.get("min_observations", SG.COT_MIN_OBS)),
            hi=float(s.get("entry_high", SG.HI_DEFAULT)),
            lo=float(s.get("entry_low", SG.LO_DEFAULT)),
            win_min=int(s.get("window_min_weeks", SG.WIN_MIN)),
            win_max=int(s.get("window_max_weeks", SG.WIN_MAX)),
            hist_avg_bp={k: float(v) for k, v in (s.get("hist_avg_bp") or SG.HIST_AVG_BP).items()},
            hist_weekly_hit={k: float(v) for k, v in
                             (s.get("hist_weekly_hit") or SG.HIST_WEEKLY_HIT).items()},
            start_year=int(raw.get("start_year", 2019)),
            foreign_align=str(raw.get("foreign_align", "fri")),
            domestic_align=str(raw.get("domestic_align", "mon")),
            roll_weeks=int(r.get("roll_weeks", 52)),
            min_obs_reg=int(r.get("min_observations", 26)),
            out_dir=out_dir,
            fig_dir=ROOT / o.get("fig_dir", "data/cftc_cot/fig"),
        )


# ======================================================================
# 单品种面板
# ======================================================================
@dataclass
class CommodityState:
    commodity: Commodity
    panel: pd.DataFrame            # 索引 = 周（W-FRI），含 cot/dir/close
    episode: SG.Episode
    verdict: SG.Verdict
    price_source: str = "none"
    warning: str = ""

    @property
    def key(self) -> str:
        return self.commodity.key


def build_panel(commodity: Commodity, cfg: CftcConfig, settings: Optional[Settings] = None,
                start_year: Optional[int] = None, end_year: Optional[int] = None,
                force: bool = False, no_fetch: bool = False,
                verbose: bool = True) -> Tuple[pd.DataFrame, str]:
    """COT 面板 + 外盘价格 → 周度合并面板（含 cot/dir）。

    返回 (panel, warning)。panel 为空表示该品种不可用。
    """
    sy = int(start_year or cfg.start_year)
    ey = int(end_year or pd.Timestamp.today().year)
    years = COT.years_for(sy, ey)
    warn: List[str] = []

    try:
        cpanel = COT.weekly_panel(commodity, years, force=force and not no_fetch,
                                  verbose=verbose, offline=no_fetch)
    except Exception as exc:                             # noqa: BLE001
        return pd.DataFrame(), f"COT 取数失败: {exc}"

    try:
        fx = PX.foreign_weekly(commodity, align=cfg.foreign_align)
        px_src = f"akshare.futures_foreign_hist({commodity.akshare_symbol})"
    except Exception as exc:                             # noqa: BLE001
        warn.append(f"外盘价格失败({exc})，只用 COT 无价格")
        fx, px_src = None, "none"
    if no_fetch and fx is None:
        warn.append("离线模式无外盘价格缓存")

    if fx is not None and len(fx):
        df = pd.DataFrame({"close": fx}).join(cpanel, how="inner")
    else:
        df = cpanel.copy()
        df["close"] = np.nan
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df["r"] = df["close"].pct_change()
    df = SG.add_cot(df, window=cfg.lookback_weeks, min_obs=cfg.min_obs, hi=cfg.hi, lo=cfg.lo)
    df = df[df["mm_net"].notna()]
    df.attrs["price_source"] = "akshare_foreign" if (fx is not None and len(fx)) else "none"
    df.attrs["price_label"] = px_src
    return df, "; ".join(warn)


def build_state(cfg: Optional[CftcConfig] = None, settings: Optional[Settings] = None,
                keys: Optional[Sequence[str]] = None, start_year: Optional[int] = None,
                asof: Optional[str] = None, no_fetch: bool = False,
                force: bool = False, store: Optional[SQLiteStore] = None,
                verbose: bool = True) -> List[CommodityState]:
    """当前状态快照：配置里各品种（默认 5 个）的 COT / dir / episode / 结论，并落库。"""
    cfg = cfg or CftcConfig.from_dict(load_config())
    settings = settings or load_settings()
    store = store or SQLiteStore(settings.data.db_path)

    states: List[CommodityState] = []
    for c in select(list(keys) if keys else cfg.keys):
        panel, warn = build_panel(c, cfg, settings=settings, start_year=start_year,
                                  force=force, no_fetch=no_fetch, verbose=verbose)
        if panel.empty:
            states.append(CommodityState(c, panel, SG.Episode(0, 0, None, np.nan, np.nan, np.nan, 0.0, None),
                                         SG.Verdict("观望", "flat", warn or "数据不可用", SG.COLOR_FLAT,
                                                    cfg.win_max, 0.0, 0.0), "none", warn))
            continue
        if asof:
            panel = panel.loc[panel.index <= pd.Timestamp(asof)]
            if panel.empty:
                continue
        ep = SG.episode_info(panel)
        vd = SG.verdict(c.key, ep, win_min=cfg.win_min, win_max=cfg.win_max,
                        hist_avg_bp=cfg.hist_avg_bp)
        states.append(CommodityState(c, panel, ep, vd,
                                     price_source=str(panel.attrs.get("price_source", "none")),
                                     warning=warn))

    if store is not None and states:
        _persist(store, states)
    return states


def _persist(store: SQLiteStore, states: List[CommodityState]) -> None:
    rows: List[dict] = []
    eps: List[dict] = []
    for st in states:
        p = st.panel
        if p is None or p.empty:
            continue
        for wk, r in p.iterrows():
            rows.append({
                "commodity": st.key, "name_cn": st.commodity.name_cn,
                "week": pd.Timestamp(wk).strftime("%Y-%m-%d"),
                "report_date": pd.to_datetime(r["date"]).strftime("%Y-%m-%d") if "date" in r and pd.notna(r["date"]) else "",
                "oi": r.get("oi"), "mm_long": r.get("mm_long"), "mm_short": r.get("mm_short"),
                "mm_net": r.get("mm_net"), "d_mm_net": r.get("d_mm_net"),
                "cot": r.get("cot"), "dir": int(r.get("dir") or 0), "close": r.get("close"),
            })
        ep, vd = st.episode, st.verdict
        eps.append({
            "commodity": st.key,
            "asof_week": pd.Timestamp(p.index[-1]).strftime("%Y-%m-%d"),
            "start_week": pd.Timestamp(ep.start).strftime("%Y-%m-%d") if ep.start is not None else "",
            "dir": ep.dir, "held_weeks": ep.held, "cot": ep.cot, "pnl_pct": ep.pnl_pct,
            "action": vd.action, "stage": vd.stage, "advice": vd.advice,
        })
    try:
        store.upsert_cftc_cot(rows)
        store.upsert_cftc_episode(eps)
        store.log("cftc_cot", "state", "ok", len(rows), "cftc")
    except Exception as exc:                             # noqa: BLE001
        store.log("cftc_cot", "state", "error", 0, "cftc", str(exc))


# ======================================================================
# 回测 / 统计
# ======================================================================
def episode_table(states: Sequence[CommodityState], cfg: CftcConfig,
                  horizon: int = 12) -> pd.DataFrame:
    """所有品种的极值 episode 明细（事件去重）。"""
    frames = []
    for st in states:
        if st.panel is None or st.panel.empty:
            continue
        e = SG.episode_stats(st.panel, hi=cfg.hi, lo=cfg.lo, max_horizon=horizon)
        if e.empty:
            continue
        e = e.assign(commodity=st.key, name_cn=st.commodity.name_cn)
        frames.append(e)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def run_backtest(states: Sequence[CommodityState], cfg: CftcConfig,
                 horizons: Sequence[int] = (1, 2, 4, 8),
                 settings: Optional[Settings] = None,
                 with_domestic: bool = False) -> dict:
    """事件胜率（分品种、分 horizon）+ Q 五口径 IC/净值 + 国内传导核对。"""
    eps = episode_table(states, cfg)
    out: dict = {"episodes": eps, "hit": {}, "q": {}, "pooled": {}}

    for st in states:
        if eps.empty or st.key not in set(eps["commodity"]):
            continue
        sub = eps[eps["commodity"] == st.key]
        out["hit"][st.key] = {h: SG.hit_summary(sub, horizon=h) for h in horizons}

    # Q 五口径：先补 mm_long/mm_short（COT 面板本来就有），再算 Q1..Q5
    for st in states:
        p = st.panel
        if p is None or p.empty or "r" not in p.columns or p["r"].notna().sum() < 30:
            continue
        q = SG.all_q(p)
        row: Dict[str, dict] = {}
        for qc in ("Q1", "Q2", "Q3", "Q4", "Q5"):
            sg = SG.signals_for(q, q_col=qc, roll=cfg.roll_weeks, min_obs=cfg.min_obs_reg)
            row[qc] = {
                "ic_rev": SG.ic_of(sg, "sig_rev", "r_fwd0"),
                "ic_mom": SG.ic_of(sg, "sig_mom", "r_fwd0"),
                "rev": SG.nav_stats(sg, "sig_rev", "r_fwd0"),
                "mom": SG.nav_stats(sg, "sig_mom", "r_fwd0"),
            }
        out["q"][st.key] = row

    # COT 分位本身的方向预测（不使用 Q）：高分位做多，低分位做空
    pooled = []
    for st in states:
        p = st.panel
        if p is None or p.empty or "r" not in p.columns:
            continue
        t = p.copy()
        t["sig_cot"] = t["dir"].astype(float)
        t["r_fwd0"] = t["r"].shift(-1)
        t["commodity"] = st.key
        pooled.append(t[["sig_cot", "r_fwd0", "commodity"]])
        out["pooled"][st.key] = {
            "ic": SG.ic_of(t, "sig_cot", "r_fwd0"),
            "nav": SG.nav_stats(t[t["sig_cot"] != 0], "sig_cot", "r_fwd0"),
            "n": int(t["sig_cot"].ne(0).sum()),
        }
    if pooled:
        allp = pd.concat(pooled).dropna(subset=["sig_cot", "r_fwd0"])
        # ⚠️ 不能把各品种序列简单 concat 后串净值（跨品种串行会虚高）；
        # 组合口径 = 每周取「有信号品种」的等权平均收益，再算净值。
        wide = allp.pivot_table(index=allp.index, columns="commodity",
                                values="r_fwd0", aggfunc="first")
        pos = allp.pivot_table(index=allp.index, columns="commodity",
                               values="sig_cot", aggfunc="first").reindex(wide.index)
        w = pos.notna() & wide.notna() & (pos != 0)
        combo = (pos * wide).where(w).sum(axis=1) / w.sum(axis=1).replace(0, np.nan)
        port = pd.DataFrame({"ret": combo}).dropna()
        port["sig"] = 1.0
        out["pooled_all"] = {
            "ic": SG.ic_of(allp, "sig_cot", "r_fwd0"),
            "nav": SG.nav_stats(port, "sig", "ret"),
            "n": int(len(port)),
            "note": "等权组合（每周取有信号品种平均）",
        }

    if with_domestic:
        out["domestic"] = _domestic_link(states, cfg, settings)
    return out


def _domestic_link(states: Sequence[CommodityState], cfg: CftcConfig,
                   settings: Optional[Settings]) -> Dict[str, dict]:
    """国内传导核对：同周收益相关性 + COT 方向在国内腿上的 IC/净值。

    这就是「国内只对贵金属有效」的定量依据（相关性 <0.3 的品种不可套）。
    """
    out: Dict[str, dict] = {}
    for st in states:
        p = st.panel
        if p is None or p.empty or "r" not in p.columns:
            continue
        try:
            dom, src = PX.domestic_weekly(st.commodity, align=cfg.domestic_align,
                                          settings=settings)
        except Exception as exc:                         # noqa: BLE001
            out[st.key] = {"source": "none", "error": str(exc)}
            continue
        if dom is None or dom.empty:
            out[st.key] = {"source": src, "error": "国内价格不可用"}
            continue
        t = pd.DataFrame({"close_dom": dom}).join(
            p[["dir", "r"]].rename(columns={"r": "r_foreign"}), how="inner")
        t = t[~t.index.duplicated(keep="last")].sort_index()
        t["r_dom"] = t["close_dom"].pct_change()
        t["r_dom_next"] = t["r_dom"].shift(-1)
        t["sig"] = t["dir"].astype(float)
        t["r_fwd_fx"] = t["r_foreign"].shift(-1)
        m = t.dropna(subset=["r_dom_next", "r_fwd_fx"])
        corr = float(m["r_dom_next"].corr(m["r_fwd_fx"])) if len(m) > 8 else float("nan")

        # 国内腿多周期胜率（1/4/8 周）——「国内只对贵金属有效」必须用这个核，
        # 不能只看单周 IC（单周本来就接近 50%）。
        hits: Dict[str, dict] = {}
        for h in (1, 4, 8):
            fwd = t["close_dom"].shift(-h) / t["close_dom"] - 1
            x = pd.DataFrame({"dir": t["sig"], "fwd": fwd}).dropna()
            x = x[x["dir"] != 0]
            if len(x) < 5:
                hits[str(h)] = {"n": int(len(x)), "hit": float("nan"), "mean_bp": float("nan")}
                continue
            signed = x["fwd"] * x["dir"]
            hits[str(h)] = {"n": int(len(x)), "hit": float((signed > 0).mean()),
                            "mean_bp": float(signed.mean() * 1e4)}

        # 多/空腿分开看：国内腿做空往往不成立，混在一起会互相抵消掩盖问题
        by_dir: Dict[str, Dict[str, dict]] = {}
        for d in (1, -1):
            sub: Dict[str, dict] = {}
            for h in (1, 4, 8):
                fwd = t["close_dom"].shift(-h) / t["close_dom"] - 1
                x = pd.DataFrame({"dir": t["sig"], "fwd": fwd}).dropna()
                x = x[x["dir"] == d]
                if len(x) < 5:
                    sub[str(h)] = {"n": int(len(x)), "hit": float("nan"),
                                   "mean_bp": float("nan")}
                    continue
                signed = x["fwd"] * d
                sub[str(h)] = {"n": int(len(x)), "hit": float((signed > 0).mean()),
                               "mean_bp": float(signed.mean() * 1e4)}
            by_dir[str(d)] = sub

        out[st.key] = {
            "source": src,
            "n": int(len(t)),
            "link_corr": corr,
            "ic": SG.ic_of(t, "sig", "r_dom_next"),
            "nav": SG.nav_stats(t[t["sig"] != 0], "sig", "r_dom_next"),
            "hit": hits,
            "hit_by_dir": by_dir,
            "weak_link": bool(np.isfinite(corr) and corr < 0.30),
        }
    return out
