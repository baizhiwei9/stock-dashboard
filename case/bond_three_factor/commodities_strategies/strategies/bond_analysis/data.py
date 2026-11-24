# -*- coding: utf-8 -*-
"""债市分析 · 数据层（只调 DataHub EDB，不直连任何接口）。

口径约定
--------
- 所有债券/宏观指标一律走 `data_hub.edb`（DataHub 三级灾备统一数据层），
  业务侧只认 `series_id`，不关心底层是 AKShare / Tushare / 中国货币网 / FRED。
- 本模块只负责「定位 DataHub → 取序列 → 统一成 DataFrame/标量」，
  不做任何抓取实现，也不写解析逻辑——那是 DataHub 的职责。

为什么单独抽一层
----------------
`strategies/` 下的策略只应该关心信号与回测；数据来源换源、加降级、补缓存
都不该动策略代码。这与 `futures_cross_section/data.py` 的做法保持一致。

Series 命名（DataHub EDB 侧）
-----------------------------
    国债曲线      BOND.CN.GOVT.{1M,3M,6M,9M,1Y,2Y,3Y,5Y,7Y,10Y,30Y}
    中短票        BOND.CN.MTN.{AAA,AAP,AA,AAL}.{1Y,2Y,3Y,5Y,7Y,10Y}
    二级资本债    BOND.CN.T2C.AAA.{1Y,3Y,5Y}
    国开债        BOND.CN.CDB.{1Y,3Y,5Y,10Y}
    同业存单      BOND.CN.NCD.AAA.{1M,3M,6M,9M,1Y}
    中债综合指数  BOND.CN.CBINDEX
    回购定盘      BOND.CN.REPO.{FR001,FR007,FR14,FDR001,FDR007}
    质押回购      BOND.CN.REPO.{R001,R007,R014,VOL.TOTAL,VOL.ONSHARE}
    派生利差      BOND.SPREAD.CN.{MTN.AAA.3Y,MTN.AA.3Y,NCD.1Y,T2C.5Y,GOVT.10Y2Y,REPO.R_DR.007}
                  对政策利率 BOND.SPREAD.CN.{GOVT.10Y_POLICY,DR007_POLICY,NCD1Y_POLICY}
                  （减项统一为 LIQ.CN.OMO.POLICY = 隔夜逆回购操作利率）
    中国宏观      CN.M1.YOY / CN.M2.YOY / CN.M1M2.SCISSOR / CN.TSF.* / CN.PMI.* / CN.CPI.YOY
    中国流动性    LIQ.CN.{SHIBOR.*,LPR.*,OMO.*,MLF,DR001,DR007}
    美国利率信用  RATE.US.* / CREDIT.US.OAS.* / CREDIT.US.SPREAD.* / CREDIT.US.MOVE
    美国流动性    LIQ.US.{EFFR,SOFR,CP.3M,SPREAD.CP_EFFR,RRP,TGA,RESERVES}
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from ...config import ROOT, Settings

# 国债曲线期限（顺序即展示顺序）
GOVT_TENORS = ("1M", "3M", "6M", "9M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "30Y")
# 信用债评级档（展示顺序：高 → 低）
CREDIT_GRADES = (("AAA", "MTN.AAA"), ("AA+", "MTN.AAP"),
                 ("AA", "MTN.AA"), ("AA-", "MTN.AAL"))
# 信用利差矩阵用的期限
MATRIX_TENORS = ("1Y", "2Y", "3Y", "5Y", "7Y", "10Y")
# 同业存单期限
NCD_TENORS = ("1M", "3M", "6M", "9M", "1Y")

# 对政策利率利差（policy_rate = 隔夜逆回购操作利率 LIQ.CN.OMO.POLICY）
# (展示名, 派生 series_id, 减项基础序列; 派生取不到时用它自己算)
POLICY_SPREADS = (
    ("10Y-政策利率", "BOND.SPREAD.CN.GOVT.10Y_POLICY", "BOND.CN.GOVT.10Y"),
    ("DR007-政策利率", "BOND.SPREAD.CN.DR007_POLICY", "LIQ.CN.DR007"),
    ("NCD1Y-政策利率", "BOND.SPREAD.CN.NCD1Y_POLICY", "BOND.CN.NCD.AAA.1Y"),
)
POLICY_RATE_ID = "LIQ.CN.OMO.POLICY"


# ---------------------------------------------------------------- DataHub 定位

def _candidate_roots(settings: Optional[Settings] = None) -> List[Path]:
    """找出含 `data_hub/edb` 的仓库根，按可信度排序。

    与 `Futures/datahub.py` 同一套探测习惯，只是额外要求 `data_hub/edb`
    （EDB 才是债券/宏观数据的入口）。
    """
    cands: List[Path] = []
    for env in ("FUTURES_DATAHUB_ROOT", "DATAHUB_ROOT", "BOND_DATAHUB_ROOT"):
        raw = os.environ.get(env)
        if raw:
            cands.append(Path(raw).expanduser())
    if settings is not None and getattr(settings.data, "datahub_root", ""):
        cands.append(Path(settings.data.datahub_root).expanduser())
    # 同工作区唯一定位：projects/datahub-multi-tier
    # （原 vendor/datahub、optstack、datahub 目录、/root/.openclaw 快照等副本已废弃）
    workspace = ROOT.parent
    cands.append(workspace / "datahub-multi-tier")

    seen, out = set(), []
    for p in cands:
        try:
            key = str(p.resolve())
        except OSError:
            key = str(p)
        if key in seen:
            continue
        seen.add(key)
        if (p / "data_hub" / "edb").is_dir():
            out.append(p)
    return out


def find_datahub_root(settings: Optional[Settings] = None) -> Optional[Path]:
    """返回 DataHub 仓库根；找不到返回 None。"""
    cands = _candidate_roots(settings)
    return cands[0] if cands else None


def connect(settings: Optional[Settings] = None) -> Optional[object]:
    """导入并返回 `data_hub.edb` 模块；不可用返回 None。"""
    root = find_datahub_root(settings)
    if root is None:
        return None
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from data_hub import edb
        return edb
    except Exception:
        return None


def available(settings: Optional[Settings] = None) -> bool:
    return connect(settings) is not None


# ---------------------------------------------------------------- 取数封装

class BondData:
    """债市/宏观取数器（全部转调 DataHub EDB，含进程内小缓存）。"""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings
        self.edb = connect(settings)
        if self.edb is None:
            raise RuntimeError(
                "未找到可用 DataHub（需含 data_hub/edb）。"
                "请克隆 github.com/baizhiwei9/datahub-multi-tier 并设置 DATAHUB_ROOT")

    # ---- 基础取数 ----

    def series(self, series_id: str) -> pd.DataFrame:
        """→ DataFrame[date, value]；取不到时返回空表（不抛异常）。"""
        try:
            df = self.edb.get_series(series_id)
        except Exception:
            return pd.DataFrame(columns=["date", "value"])
        if df is None or not len(df):
            return pd.DataFrame(columns=["date", "value"])
        return df

    def last(self, series_id: str) -> Tuple[Optional[float], Optional[str]]:
        """→ (最新值, 最新日期)；取不到时 (None, None)。"""
        df = self.series(series_id)
        if not len(df):
            return None, None
        return float(df["value"].iloc[-1]), str(df["date"].iloc[-1])[:10]

    def value_at(self, series_id: str, days_ago: int) -> Optional[float]:
        """N 个自然日前的值（用于周/月变化）。"""
        df = self.series(series_id)
        if not len(df):
            return None
        import datetime as dt
        last_day = dt.date.fromisoformat(str(df["date"].iloc[-1])[:10])
        target = (last_day - dt.timedelta(days=days_ago)).isoformat()
        prev = df[df["date"].astype(str).str.slice(0, 10) <= target]
        return float(prev["value"].iloc[-1]) if len(prev) else None

    def pct_rank(self, series_id: str, window: Optional[int] = None) -> Optional[float]:
        """分位（%）。window=None 用全样本（建库以来），否则用最近 N 个点。"""
        df = self.series(series_id)
        if len(df) < 10:
            return None
        vals = [float(v) for v in (df["value"].tail(window) if window else df["value"])]
        cur = vals[-1]
        return round(100.0 * sum(1 for v in vals if v <= cur) / len(vals), 1)

    def spread(self, hi_id: str, lo_id: str) -> pd.DataFrame:
        """两条序列相减 → DataFrame[date, value]，单位 bp（差值 ×100）。"""
        a, b = self.series(hi_id), self.series(lo_id)
        if not len(a) or not len(b):
            return pd.DataFrame(columns=["date", "value"])
        da = dict(zip(a["date"].astype(str).str.slice(0, 10), a["value"]))
        db = dict(zip(b["date"].astype(str).str.slice(0, 10), b["value"]))
        common = sorted(set(da) & set(db))
        if not common:
            return pd.DataFrame(columns=["date", "value"])
        return pd.DataFrame({"date": common,
                             "value": [round((float(da[d]) - float(db[d])) * 100, 2) for d in common]})

    def spread_last(self, hi_id: str, lo_id: str) -> Tuple[Optional[float], Optional[float]]:
        """→ (现值 bp, 分位 %)。"""
        df = self.spread(hi_id, lo_id)
        if not len(df):
            return None, None
        vals = [float(v) for v in df["value"]]
        cur = vals[-1]
        p = round(100.0 * sum(1 for v in vals if v <= cur) / len(vals), 1)
        return cur, p

    # ---- 组合视图 ----

    def govt_curve(self) -> List[dict]:
        """国债曲线（含分位与一周变化）。"""
        assert self.edb is not None
        out = []
        for t in GOVT_TENORS:
            sid = f"BOND.CN.GOVT.{t}"
            try:
                self.edb.get_def(sid)
            except KeyError:
                continue
            v, d = self.last(sid)
            if v is None:
                continue
            w = self.value_at(sid, 7)
            out.append({"tenor": t, "value": round(v, 4), "date": d,
                        "pct": self.pct_rank(sid),
                        "chg_1w_bp": round((v - w) * 100, 1) if w is not None else None})
        return out

    def govt_spreads(self) -> List[dict]:
        """国债期限利差（优先用派生 series，缺失则现算）。"""
        out = []
        for name, sid in (("10Y-2Y", "BOND.SPREAD.CN.GOVT.10Y2Y"),):
            v, _ = self.last(sid)
            if v is None:
                v, p = self.spread_last("BOND.CN.GOVT.10Y", "BOND.CN.GOVT.2Y")
            else:
                p = self.pct_rank(sid)
            if v is not None:
                out.append({"name": name, "value_bp": round(v, 1), "pct": p})
        for a, b in (("30Y", "10Y"), ("10Y", "1Y"), ("30Y", "2Y")):
            v, p = self.spread_last(f"BOND.CN.GOVT.{a}", f"BOND.CN.GOVT.{b}")
            if v is not None:
                out.append({"name": f"{a}-{b}", "value_bp": v, "pct": p})
        return out

    def credit_spreads(self, tenor: str = "3Y") -> List[dict]:
        """3Y 各评级中短票对国债利差。"""
        out = []
        for tag, seg in CREDIT_GRADES:
            v, p = self.spread_last(f"BOND.CN.{seg}.{tenor}", f"BOND.CN.GOVT.{tenor}")
            if v is None:
                continue
            cur, _ = self.last(f"BOND.CN.{seg}.{tenor}")
            govt, _ = self.last(f"BOND.CN.GOVT.{tenor}")
            prev_g = self.value_at(f"BOND.CN.{seg}.{tenor}", 7)
            prev_b = self.value_at(f"BOND.CN.GOVT.{tenor}", 7)
            chg = round(((cur - govt) - (prev_g - prev_b)) * 100, 1) \
                if None not in (cur, govt, prev_g, prev_b) else None
            out.append({"grade": tag, "spread_bp": round(v, 1), "pct": p, "chg_1w_bp": chg})
        return out

    def credit_matrix(self) -> List[dict]:
        """期限 × 评级 的信用利差矩阵（bp）。"""
        rows = []
        for t in MATRIX_TENORS:
            govt, _ = self.last(f"BOND.CN.GOVT.{t}")
            if govt is None:
                continue
            row = {"tenor": t, "govt": round(govt, 3)}
            for _, seg in CREDIT_GRADES:
                v, _ = self.last(f"BOND.CN.{seg}.{t}")
                row[seg] = round((v - govt) * 100) if v is not None else None
            rows.append(row)
        return rows

    def ncd(self) -> List[dict]:
        """同业存单 AAA 各期限 + 对国债利差。"""
        out = []
        for t in NCD_TENORS:
            v, _ = self.last(f"BOND.CN.NCD.AAA.{t}")
            if v is None:
                continue
            g, _ = self.last(f"BOND.CN.GOVT.{t}")
            out.append({"tenor": t, "value": round(v, 4),
                        "vs_govt_bp": round((v - g) * 100, 1) if g is not None else None})
        return out

    def funding(self) -> Dict[str, dict]:
        """资金结构：隔夜占比 / 成交额 / R007 / DR007 / 非银分层。"""
        out: Dict[str, dict] = {}
        for label, sid, unit in (("隔夜回购占比", "BOND.CN.REPO.VOL.ONSHARE", "%"),
                                 ("回购成交额", "BOND.CN.REPO.VOL.TOTAL", "亿元"),
                                 ("R007", "BOND.CN.REPO.R007", "%"),
                                 ("DR007", "LIQ.CN.DR007", "%")):
            v, d = self.last(sid)
            if v is None:
                continue
            out[label] = {"value": round(v, 2), "unit": unit, "date": d,
                          "pct": self.pct_rank(sid)}
        v, d = self.last("BOND.SPREAD.CN.REPO.R_DR.007")
        if v is not None:
            out["R007-DR007"] = {"value": round(v, 1), "unit": "bp", "date": d,
                                 "pct": self.pct_rank("BOND.SPREAD.CN.REPO.R_DR.007")}
        return out

    # ---- 政策利率（2026-11-02 新框架：隔夜逆回购“固定利率、数量招标”）----
    # 当前值 1.25% 仅作注释备忘，**代码一律从 LIQ.CN.OMO.POLICY 读**，不写死。

    def policy_rate(self) -> Dict[str, Optional[float]]:
        """政策利率当前值 → ``{"value": <最新公告利率>, "date": <公告日>}``。

        口径：隔夜逆回购**操作利率**（DataHub ``LIQ.CN.OMO.POLICY``），
        由人民银行每日交易公告正文解析，只在操作日有观测点、不 forward-fill。
        取不到时返回 ``{"value": None, "date": None}``——不拿常量兜底。
        """
        v, d = self.last(POLICY_RATE_ID)
        return {"value": v, "date": d}

    @staticmethod
    def _chg_1w(df: pd.DataFrame) -> Optional[float]:
        """序列现值相对 7 个自然日前的变化（绝对值，利差序列单位本就是 bp）。"""
        if len(df) < 2:
            return None
        import datetime as dt
        last_day = dt.date.fromisoformat(str(df["date"].iloc[-1])[:10])
        target = (last_day - dt.timedelta(days=7)).isoformat()
        prev = df[df["date"].astype(str).str.slice(0, 10) <= target]
        if not len(prev):
            return None
        return round(float(df["value"].iloc[-1]) - float(prev["value"].iloc[-1]), 1)

    @staticmethod
    def _pct_of(df: pd.DataFrame) -> Optional[float]:
        """与 ``spread_last`` 完全一致的分位算法（全样本，≤ 当前值占比）。"""
        if len(df) < 10:
            return None
        vals = [float(v) for v in df["value"]]
        cur = vals[-1]
        return round(100.0 * sum(1 for v in vals if v <= cur) / len(vals), 1)

    def _spread_frame(self, sid: str, base: Optional[str]) -> pd.DataFrame:
        """派生利差序列；派生取不到时用「基础序列 − 政策利率」现算（同口径）。"""
        df = self.series(sid)
        if len(df) or not base:
            return df
        return self.spread(base, POLICY_RATE_ID)

    def policy_spreads(self) -> List[dict]:
        """三条「对政策利率利差」（bp）+ 各自分位 + 一周变化。

        优先读 DataHub 已算好的派生序列（口径统一，免得业务侧自己减一遍
        出现「前端 36.2bp、后台 36bp」两套数）；派生序列缺失时才回落到
        ``基础序列 − LIQ.CN.OMO.POLICY`` 现算（同口径、同一分位算法）。

        分位口径与文件里其他利差一致，都是**全样本内 ≤ 当前值的占比**
        （即 ``spread_last`` / ``pct_rank`` 那一套），不另立第二套口径。

        返回：``[{"name","value_bp","pct","chg_1w_bp","date","n_obs"}, ...]``
        """
        out: List[dict] = []
        for name, sid, base in POLICY_SPREADS:
            df = self._spread_frame(sid, base)
            if not len(df):
                continue
            # 分位与文件里其他利差同一算法（全样本 ≤ 当前值占比）；
            # 不论走派生还是现算，都只算一次，不搞两套口径。
            out.append({"name": name, "value_bp": round(float(df["value"].iloc[-1]), 1),
                        "pct": self._pct_of(df), "chg_1w_bp": self._chg_1w(df),
                        "date": str(df["date"].iloc[-1])[:10], "n_obs": int(len(df))})
        return out

    def cbond_index(self) -> Optional[dict]:
        """中债新综合指数（理财/债基基准）：现值、MA20、偏离、MA20 斜率。"""
        df = self.series("BOND.CN.CBINDEX")
        if not len(df):
            return None
        vals = [float(v) for v in df["value"]]
        ma20 = sum(vals[-20:]) / 20 if len(vals) >= 20 else None
        ma20_prev = sum(vals[-25:-5]) / 20 if len(vals) >= 25 else None
        return {
            "value": round(vals[-1], 4),
            "date": str(df["date"].iloc[-1])[:10],
            "ma20": round(ma20, 4) if ma20 else None,
            "gap_to_ma20_pct": round((vals[-1] / ma20 - 1) * 100, 3) if ma20 else None,
            "ma20_slope_5d_pct": round((ma20 / ma20_prev - 1) * 100, 4)
            if (ma20 and ma20_prev) else None,
        }

    def macro(self) -> Dict[str, dict]:
        """中国宏观货币信用 + 美国利率信用（只取报告要用的那些）。"""
        out: Dict[str, dict] = {}
        for label, sid in (("M1同比", "CN.M1.YOY"), ("M2同比", "CN.M2.YOY"),
                           ("M1-M2剪刀差", "CN.M1M2.SCISSOR"),
                           ("社融存量", "CN.TSF.STOCK"), ("社融增量", "CN.TSF.INC"),
                           ("制造业PMI", "CN.PMI.MFG"), ("非制造业PMI", "CN.PMI.NMFG"),
                           ("CPI同比", "CN.CPI.YOY"), ("PPI同比", "CN.PPI.YOY"),
                           ("LPR1Y", "LIQ.CN.LPR.1Y"), ("LPR5Y", "LIQ.CN.LPR.5Y"),
                           ("SHIBOR1W", "LIQ.CN.SHIBOR.1W"),
                           ("存款准备金率", "CN.RRR"),
                           ("央行OMO投放", "LIQ.CN.OMO.NET"),
                           ("买断式逆回购余额", "LIQ.CN.OMO.OUTRIGHT.BAL"),
                           ("美债10Y", "RATE.US.10Y"), ("美债2Y", "RATE.US.2Y"),
                           ("HY OAS", "CREDIT.US.OAS.HY"),
                           ("IG OAS", "CREDIT.US.OAS.IG"),
                           ("CCC OAS", "CREDIT.US.OAS.CCC"),
                           ("MOVE", "CREDIT.US.MOVE"),
                           ("CP-EFFR", "LIQ.US.SPREAD.CP_EFFR"),
                           ("中美10Y利差", "RATE.SPREAD.CN10Y_US10Y")):
            v, d = self.last(sid)
            if v is None:
                continue
            out[label] = {"value": round(v, 4), "date": d, "pct": self.pct_rank(sid)}
        return out

    def freshness(self, series_ids: Optional[List[str]] = None) -> pd.DataFrame:
        """覆盖度体检：每条的最新日期 / 行数 / 是否滞后 / 来源。"""
        self.edb  # noqa: B018 - 确认可用
        ids = series_ids or self._default_ids()
        rows = []
        for sid in ids:
            try:
                r = self.edb.get_series_result(sid)
            except KeyError:
                # 未登记的 id（如 AA+/AA- 只挂了 1Y/3Y/5Y）直接跳过，
                # 否则一次体检就整页崩掉（2026-11-14 发现的老问题）
                continue
            rows.append({"series_id": sid, "latest_date": r.get("latest_date"),
                         "latest_value": r.get("latest_value"), "count": r.get("count"),
                         "source": r.get("source"), "stale": bool(r.get("is_stale"))})
        return pd.DataFrame(rows)

    @staticmethod
    def _default_ids() -> List[str]:
        ids = [f"BOND.CN.GOVT.{t}" for t in GOVT_TENORS]
        ids += [f"BOND.CN.{seg}.{t}" for _, seg in CREDIT_GRADES for t in MATRIX_TENORS]
        ids += [f"BOND.CN.NCD.AAA.{t}" for t in NCD_TENORS]
        ids += ["BOND.CN.CBINDEX", "BOND.SPREAD.CN.MTN.AAA.3Y",
                "BOND.SPREAD.CN.NCD.1Y", "BOND.SPREAD.CN.GOVT.10Y2Y",
                "BOND.CN.REPO.R007", "BOND.CN.REPO.VOL.ONSHARE",
                "BOND.SPREAD.CN.REPO.R_DR.007", "LIQ.CN.DR007"]
        ids += [POLICY_RATE_ID] + [x[1] for x in POLICY_SPREADS]
        return ids
