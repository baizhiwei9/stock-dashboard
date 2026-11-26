# -*- coding: utf-8 -*-
"""把 options_utility 的产出搬成 dashboard 指标点（不做二次计算）。

口径原则：**同源、只搬运**。缠论/IV/偏度/PCR/基差这些数由 options_utility 算，
这里只按文件取值，取不到就跳过（不补数、不用旧值冒充当日）。

数据来源两类：
1. 原生产物：`outputs/<YYYYMMDD>_basis_daily.json` / `_oi_abnormal.json` /
   `_midday_option.json` / `_close_option.json`（这些任务本来就在写）；
2. 补充产物：`outputs/<YYYYMMDD>_oi_pcr.json` / `_pcr_macd.json` / `_skew10.json`
   （由 options_utility 的 `case_summary.write_summary()` 落盘）。

返回 list[dict]，字段就是 `publish.normalize()` 认的简写：
`{"key": 指标键, "value_num": 数值, "obs_date": 日期}`。
"""
from __future__ import annotations

import glob
import json
import os
import sys
from typing import Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))


def _options_root() -> str:
    if _HERE not in sys.path:
        sys.path.insert(0, _HERE)
    try:
        import options_daily_runner as R           # noqa: PLC0415
        return R.options_root() or ""
    except Exception:                              # noqa: BLE001
        return os.environ.get("OPTIONS_UTILITY_ROOT") or ""


def _newest(root: str, slug: str) -> str:
    """取 `<slug>` 最新的一份 json（按文件名里的日期倒序）。"""
    if not root:
        return ""
    files = glob.glob(os.path.join(root, "outputs", "*%s.json" % slug))
    files = [f for f in files if os.path.basename(f)[:8].isdigit()]
    return sorted(files)[-1] if files else ""


def _load(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _dash(yyyymmdd: str) -> str:
    """YYYYMMDD → YYYY-MM-DD（框架的 fresh 校验只认带横杠的日期）。"""
    s = str(yyyymmdd or "").replace("-", "")[:8]
    if len(s) == 8 and s.isdigit():
        return "%s-%s-%s" % (s[:4], s[4:6], s[6:8])
    return str(yyyymmdd or "")


def _date_of(path: str, fallback: str = "") -> str:
    base = os.path.basename(path)
    return _dash(base[:8]) if base[:8].isdigit() else fallback


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _p(key: str, val, obs: str) -> Optional[Dict]:
    n = _num(val)
    if n is None:
        return None
    return {"key": key, "value_num": round(n, 6), "obs_date": _dash(obs)}


# ── 各任务的搬运口径 ────────────────────────────────────────────────────
def _basis_pct(root: str) -> List[Dict]:
    """期货基差日报：各品种基差率（%）。"""
    f = _newest(root, "basis_daily")
    d = _load(f) or {}
    obs = _date_of(f)
    out = []
    for sym, info in (d.items() if isinstance(d, dict) else []):
        if not isinstance(info, dict) or info.get("error"):
            continue
        out.append(_p("opt.basis.pct.%s" % sym, info.get("basis_pct"), obs))
    return [x for x in out if x]


def _oi_basis(root: str) -> List[Dict]:
    """期权OI日报：各品种绝对基差（股指=点数，国债=收益率差%）。

    `oi_abnormal.json` 只有期权持仓，没有基差；基差是 oi_daily.py 现算的，
    由 `case_summary.write_summary('oi_basis', ...)` 单独落一份汇总。
    """
    f = _newest(root, "oi_basis")
    d = _load(f) or {}
    obs = (d.get("date") if isinstance(d, dict) else "") or _date_of(f)
    out = []
    for sym, val in ((d.get("items") or {}).items() if isinstance(d, dict) else []):
        out.append(_p("opt.oi.basis_abs.%s" % sym, val, obs))
    return [x for x in out if x]


def _close_skew(root: str) -> List[Dict]:
    """收盘期权监控：各标的 SKEW 指数（百分点）。"""
    f = _newest(root, "close_option")
    d = _load(f) or {}
    obs = _date_of(f)
    out = []
    for name, info in ((d.get("skew") or {}).items() if isinstance(d, dict) else []):
        if not isinstance(info, dict):
            continue
        out.append(_p("opt.skew.close.%s" % name, info.get("skew_pct"), obs))
    return [x for x in out if x]


def _midday_iv(root: str) -> List[Dict]:
    """午盘期权：各标的 ATM 隐含波动率与 IV 偏度（百分点）。"""
    f = _newest(root, "midday_option")
    d = _load(f) or {}
    obs = _date_of(f)
    out = []
    for name, info in ((d.get("skew") or {}).items() if isinstance(d, dict) else []):
        if not isinstance(info, dict):
            continue
        out.append(_p("opt.mid.iv.%s" % name, info.get("atm_iv"), obs))
        out.append(_p("opt.mid.ivskew.%s" % name, info.get("iv_skew_pct"), obs))
    return [x for x in out if x]


def _pcr_close(root: str) -> List[Dict]:
    """持仓量 PCR（收盘确认）：全合约 Put OI / Call OI。"""
    f = _newest(root, "oi_pcr")
    d = _load(f) or {}
    obs = (d.get("date") if isinstance(d, dict) else "") or _date_of(f)
    out = []
    items = (d.get("items") or {}) if isinstance(d, dict) else {}
    for name, val in items.items():
        out.append(_p("opt.pcr.close.%s" % name, val, obs))
    return [x for x in out if x]


def _pcr_macd(root: str) -> List[Dict]:
    """PCR-MACD：各标的 PCR 与 MACD 柱。"""
    f = _newest(root, "pcr_macd")
    d = _load(f) or {}
    obs = (d.get("date") if isinstance(d, dict) else "") or _date_of(f)
    out = []
    for name, info in ((d.get("items") or {}).items() if isinstance(d, dict) else []):
        if not isinstance(info, dict):
            continue
        out.append(_p("opt.pcrmacd.pcr.%s" % name, info.get("pcr"), obs))
        out.append(_p("opt.pcrmacd.hist.%s" % name, info.get("histogram"), obs))
    return [x for x in out if x]


def _skew10(root: str) -> List[Dict]:
    """0.1Δ 偏度预警：各标的 0.1Δ 偏度（%）。"""
    f = _newest(root, "skew10")
    d = _load(f) or {}
    obs = (d.get("date") if isinstance(d, dict) else "") or _date_of(f)
    out = []
    for key, val in ((d.get("items") or {}).items() if isinstance(d, dict) else []):
        out.append(_p("opt.skew10.%s" % key, val, obs))
    return [x for x in out if x]


_PARSERS = {
    "options_basis_daily": _basis_pct,
    "options_oi_daily": _oi_basis,
    "options_close_monitor": _close_skew,
    "options_midday": _midday_iv,
    "options_pcr_close": _pcr_close,
    "options_pcr_macd": _pcr_macd,
    "options_skew10": _skew10,
}


def points_for(task_key: str) -> List[Dict]:
    """按任务键取点；没装 options_utility 或今天没产物 → 返回空列表（不报错）。"""
    fn = _PARSERS.get(task_key)
    if not fn:
        return []
    root = _options_root()
    if not root:
        return []
    try:
        return fn(root)
    except Exception:                              # noqa: BLE001
        return []
