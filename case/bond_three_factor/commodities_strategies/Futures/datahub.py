# -*- coding: utf-8 -*-
"""DataHub 适配层。

优先级：远程/本地 DataHub 的 futures_contract_daily 采集器 > 原生 AKShare > TuShare > 新浪 Web。
DataHub 内部本身已实现「AKShare(新浪) > TuShare > Coze Gateway > Web」四级降级，
本层只负责定位仓库、导入采集器、统一输出格式，并在 DataHub 不可用时兜底。
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd

from ..config import ROOT, Settings

_COLUMNS = ["trade_date", "open", "high", "low", "close", "settle", "volume", "oi"]


def _candidate_roots(settings: Settings) -> List[Path]:
    cands: List[Path] = []
    if settings.data.datahub_root:
        cands.append(Path(settings.data.datahub_root).expanduser())
    env_root = os.environ.get("FUTURES_DATAHUB_ROOT") or os.environ.get("DATAHUB_ROOT")
    if env_root:
        cands.append(Path(env_root).expanduser())
    # 统一指向 DataHub 唯一仓库 projects/datahub-multi-tier；
    # 原 vendor/datahub、workspace/datahub、会话快照 _dh 等副本已废弃，不再作为候选。
    workspace = ROOT.parent.parent
    cands.append(workspace / "projects" / "datahub-multi-tier")
    # 去重且要求含 data_hub 包
    seen, out = set(), []
    for p in cands:
        key = str(p.resolve()) if p.exists() else str(p)
        if key in seen:
            continue
        seen.add(key)
        if (p / "data_hub").is_dir():
            out.append(p)
    return out


def find_datahub_root(settings: Settings) -> Optional[Path]:
    cands = _candidate_roots(settings)
    return cands[0] if cands else None


def _ensure_path(settings: Settings, root: Path) -> None:
    for p in (root, ROOT.parent.parent / "py_deps", ROOT.parent.parent / "pylibs", ROOT.parent.parent / ".venv_pkgs"):
        if p.is_dir() and str(p) not in sys.path:
            sys.path.insert(0, str(p))


def available(settings: Settings) -> bool:
    root = find_datahub_root(settings)
    if not root:
        return False
    _ensure_path(settings, root)
    try:
        importlib.import_module("data_hub")
        importlib.import_module("data_hub.collectors")
        return True
    except Exception:
        return False


def fetch_contract(settings: Settings, contract: str, start: str, end: str
                   ) -> Tuple[Optional[pd.DataFrame], str]:
    """经 DataHub 采集器取单个合约日线，返回 (df, source)；失败返回 (None, 原因)。"""
    root = find_datahub_root(settings)
    if not root:
        return None, "datahub_not_found"
    _ensure_path(settings, root)
    try:
        mod = importlib.import_module("data_hub.collectors")
        collector = mod.FuturesContractDailyCollector()
        result = collector.collect(contract=contract, start_date=start, end_date=end)
        if not getattr(result, "success", False) or result.data is None or len(result.data) == 0:
            return None, f"datahub_empty:{getattr(result, 'error', '')}"
        df = _normalize(result.data)
        source = f"datahub:{getattr(result, 'source', 'unknown')}"
        return df, source
    except Exception as exc:  # 导入失败、接口变更、网络异常均降级
        return None, f"datahub_error:{type(exc).__name__}:{exc}"


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy()
    if "date" in work.columns and "trade_date" not in work.columns:
        work = work.rename(columns={"date": "trade_date"})
    work["trade_date"] = pd.to_datetime(work["trade_date"], errors="coerce")
    for col in _COLUMNS:
        if col not in work.columns:
            work[col] = None
        if col != "trade_date":
            work[col] = pd.to_numeric(work[col], errors="coerce")
    work = work.dropna(subset=["trade_date", "close"]).sort_values("trade_date")
    return work[_COLUMNS].drop_duplicates("trade_date", keep="last").reset_index(drop=True)