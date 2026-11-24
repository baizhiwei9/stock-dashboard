# -*- coding: utf-8 -*-
"""品种目录：合约代码生成、当前挂牌合约、主力合约识别。"""
from __future__ import annotations

import re
import sys
from functools import lru_cache
from typing import Dict, List, Optional

import pandas as pd

from ..config import ROOT, Product, load_products


@lru_cache(maxsize=1)
def registry() -> Dict[str, Product]:
    return load_products()


def product(code: str) -> Product:
    code = code.upper().strip()
    if code not in registry():
        raise KeyError(f"品种 {code} 未在 config/products.yaml 注册")
    return registry()[code]


def contract_code(code: str, year: int, month: int) -> str:
    return product(code).contract(int(year), int(month))


def contracts_for_years(code: str, years: List[int], months: Optional[List[int]] = None) -> List[str]:
    p = product(code)
    ms = months or p.months
    out: List[str] = []
    for y in years:
        for m in ms:
            out.append(f"{p.code}{y % 100:02d}{m:02d}")
    return out


def _ensure_paths() -> None:
    for path in (ROOT.parent.parent / "py_deps", ROOT.parent.parent / "pylibs", ROOT.parent.parent / ".venv_pkgs"):
        if path.is_dir() and str(path) not in sys.path:
            sys.path.insert(0, str(path))


def current_contracts(code: str) -> pd.DataFrame:
    """通过 AKShare 实时快照列出某品种当前挂牌合约及持仓量（用于观察主力）。"""
    _ensure_paths()
    import akshare as ak

    p = product(code)
    df = ak.futures_zh_realtime(symbol=p.name_cn)
    if df is None or df.empty:
        return pd.DataFrame(columns=["symbol", "name", "close", "position", "volume"])
    pat = re.compile(rf"^{p.code}\d{{4}}$", re.I)
    df = df[df["symbol"].astype(str).str.upper().str.match(pat)].copy()
    df["position"] = pd.to_numeric(df.get("position"), errors="coerce")
    df["volume"] = pd.to_numeric(df.get("volume"), errors="coerce")
    df = df.sort_values("position", ascending=False)
    return df[["symbol", "name", "close", "position", "volume"]].reset_index(drop=True)


def dominant_contract(code: str) -> Optional[str]:
    df = current_contracts(code)
    return None if df.empty else str(df.iloc[0]["symbol"]).upper()


def parse_contract(contract: str) -> Dict[str, object]:
    m = re.match(r"^([A-Za-z]+)(\d{2})(\d{2})$", contract.strip())
    if not m:
        raise ValueError(f"无法解析合约代码: {contract}")
    prod, yy, mm = m.groups()
    return {"product": prod.upper(), "year": 2000 + int(yy), "month": int(mm)}

def continuous_months(code: str) -> List[int]:
    """主力连续构建所需的合约月份。

    只保留流动性集中的主要月份，避免为冷门月份发起大量空请求；
    这是对"逐日按 OI 选主力"的近似，主力几乎总落在这些月份中。
    """
    p = product(code)
    if p.exchange == "cffex":
        return p.months
    if p.code in ("RB", "HC"):
        return [1, 5, 10]
    if p.code in ("AU", "AG"):
        return [6, 12]
    if p.code in ("CU", "AL", "ZN", "PB", "NI", "SN", "AO"):
        # 有色主力逐月切换，双月采样不足以覆盖主力
        return [1, 3, 5, 7, 9, 11]
    if len(p.months) > 6:
        return [1, 5, 9]
    return p.months
