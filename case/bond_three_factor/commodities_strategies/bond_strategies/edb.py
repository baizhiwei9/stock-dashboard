"""DataHub EDB 桥接层，债券策略的唯一数据入口。

优先使用本地 datahub（edb.py，含中国域债券通道）；本地缺失时回退远程
data_hub.edb（datahub-multi-tier 仓库）。策略侧只调用 series/update，
不直连任何外部数据源。
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd

from ..config import ROOT, Settings

WS = ROOT.parent.parent
BOND_CHANNELS = ["bond_cgb", "bond_funding", "bond_macro", "bond_ext"]


def candidate_roots(settings: Optional[Settings] = None) -> List[Path]:
    cands: List[Path] = []
    if settings is not None and settings.data.datahub_root:
        cands.append(Path(settings.data.datahub_root).expanduser())
    for env in ("BOND_DATAHUB_ROOT", "DATAHUB_ROOT"):
        if os.environ.get(env):
            cands.append(Path(os.environ[env]).expanduser())
    cands += [
        # datahub-multi-tier 是 DataHub 唯一在维护的仓库（含 bond 子包与 EDB 注册表）；
        # 原 workspace/datahub、vendor/datahub 副本已废弃，不再作为候选，
        # 避免策略拿到的序列号与最新注册表不一致。
        WS / "projects" / "datahub-multi-tier",
    ]
    seen, out = set(), []
    for p in cands:
        if str(p) in seen:
            continue
        seen.add(str(p))
        if (p / "edb.py").is_file() or (p / "data_hub").is_dir():
            out.append(p)
    return out


def _ensure_path(roots: List[Path]) -> None:
    for p in roots + [WS / "pylibs", WS / "py_deps", WS / ".venv_pkgs"]:
        if p.is_dir() and str(p) not in sys.path:
            sys.path.insert(0, str(p))


def load(settings: Optional[Settings] = None) -> Tuple[object, str]:
    """返回 (edb 模块, 'local'|'remote')；不可用时 (None, '')。"""
    roots = candidate_roots(settings)
    _ensure_path(roots)
    for root in roots:
        if (root / "edb.py").is_file():
            try:
                mod = importlib.import_module("edb")
                if Path(getattr(mod, "__file__", "")).parent == root:
                    return mod, "local"
            except Exception:
                pass
        if (root / "data_hub").is_dir():
            try:
                from data_hub import edb as remote  # noqa: WPS433
                return remote, "remote"
            except Exception:
                pass
    return None, ""


def available(settings: Optional[Settings] = None) -> bool:
    return load(settings)[0] is not None


def update(settings: Optional[Settings] = None, channels: Optional[List[str]] = None,
           force: bool = False) -> dict:
    mod, _ = load(settings)
    if mod is None:
        raise RuntimeError("DataHub EDB 不可用，无法更新债券通道")
    if not hasattr(mod, "update"):
        raise RuntimeError("当前 EDB 实现不支持 update()")
    try:
        return mod.update(only=list(channels or BOND_CHANNELS), force=force)
    except TypeError:
        return mod.update(only=list(channels or BOND_CHANNELS))


def series(settings: Optional[Settings] = None, code: str = "",
           start: Optional[str] = None, end: Optional[str] = None,
           force: bool = False) -> pd.Series:
    """取单条序列；start/end 用于回补历史（EDB 会把抓到的全量落库，只裁剪返回视图）。"""
    mod, _ = load(settings)
    if mod is None:
        raise RuntimeError("DataHub EDB 不可用")
    if hasattr(mod, "series"):
        s = mod.series(code)
    elif hasattr(mod, "get_series"):
        try:
            s = mod.get_series(code, start=start, end=end, force_refresh=force)
        except TypeError:
            s = mod.get_series(code)
        if isinstance(s, pd.DataFrame):
            cols = {c.lower(): c for c in s.columns}
            dcol = cols.get("date") or cols.get("trade_date")
            vcol = cols.get("value") or cols.get("close")
            s = pd.Series(s[vcol].values, index=pd.to_datetime(s[dcol]))
    else:
        raise RuntimeError("EDB 缺少 series/get_series 接口")
    if s is None or len(s) == 0:
        return pd.Series(dtype=float)
    s.index = pd.to_datetime(s.index, errors="coerce")
    s = s[~s.index.isna()]
    return s.astype(float).sort_index()


def latest(settings: Optional[Settings] = None, code: str = "") -> Tuple[Optional[str], Optional[float]]:
    s = series(settings, code)
    if s.empty:
        return None, None
    return s.index[-1].strftime("%Y-%m-%d"), float(s.iloc[-1])


def coverage(settings: Optional[Settings] = None, codes: Optional[List[str]] = None) -> dict:
    out = {}
    for code in codes or []:
        s = series(settings, code)
        out[code] = {"n": int(len(s)),
                     "first": s.index[0].strftime("%Y-%m-%d") if len(s) else None,
                     "last": s.index[-1].strftime("%Y-%m-%d") if len(s) else None}
    return out