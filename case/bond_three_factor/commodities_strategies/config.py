# -*- coding: utf-8 -*-
"""commodities_strategies 全局配置与配置对象定义。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


def _load_yaml(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _env_bool(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class DataConfig:
    datahub_root: str = ""
    sqlite_path: str = "data/commodities_strategies.db"
    cache_dir: str = "data/cache"
    lookback_days: int = 900
    source_priority: List[str] = field(default_factory=lambda: ["datahub", "akshare", "tushare", "sina_web"])
    tushare_token_env: str = "TUSHARE_TOKEN"
    request_sleep: float = 0.35

    @property
    def db_path(self) -> Path:
        p = Path(self.sqlite_path)
        return p if p.is_absolute() else ROOT / p

    @property
    def cache_path(self) -> Path:
        p = Path(self.cache_dir)
        return p if p.is_absolute() else ROOT / p


@dataclass
class HedgeConfig:
    percentile_window: int = 252
    min_observations: int = 252
    entry_z: float = 2.0
    exit_z: float = 0.5
    extreme_pct: float = 0.95
    vol_window: int = 20
    vol_extreme: float = 0.80
    adf_alpha: float = 0.05
    hurst_threshold: float = 0.50
    confirm_days: int = 0
    fundamental_filter: bool = True
    require_both_conflicts: bool = True
    basis_extreme_high: float = 0.85
    basis_extreme_low: float = 0.15
    warehouse_chg_pct: float = 5.0


@dataclass
class BacktestConfig:
    target_notional: float = 300_000
    commission_turnover: float = 0.0002
    slippage_turnover: float = 0.0008
    margin_rate: float = 0.10
    annual_days: int = 252


@dataclass
class Settings:
    data: DataConfig
    hedge: HedgeConfig
    backtest: BacktestConfig
    project_name: str = "commodities_strategies"
    timezone: str = "Asia/Shanghai"


@dataclass
class Product:
    code: str
    name: str
    name_cn: str
    exchange: str
    multiplier: float
    tick: float
    board: str
    months: List[int]
    inventory_name: str = ""

    def contract(self, year: int, month: int) -> str:
        return f"{self.code}{year % 100:02d}{month:02d}"


@dataclass
class LegDef:
    product: str
    mode: str = "continuous"      # continuous / contract
    contract: Optional[str] = None


@dataclass
class SpreadDef:
    id: str
    name: str
    type: str                     # seasonal / calendar / cross
    calc: str                     # spread / ratio
    board: str = ""
    relation_type: str = ""
    product: Optional[str] = None
    months: List[int] = field(default_factory=list)
    years: List[int] = field(default_factory=list)
    contracts: List[str] = field(default_factory=list)
    leg_a: Optional[LegDef] = None
    leg_b: Optional[LegDef] = None
    multiplier_a: Optional[float] = None
    multiplier_b: Optional[float] = None
    scale_a: float = 1.0
    scale_b: float = 1.0
    gap_months: int = 0
    fundamental_products: List[str] = field(default_factory=list)
    note: str = ""

    def legs(self) -> List[str]:
        """返回用于展示的两腿标识。"""
        if self.type == "seasonal":
            return [f"{self.product}{self.months[0]:02d}", f"{self.product}{self.months[1]:02d}"]
        if self.type == "calendar":
            return list(self.contracts)
        if self.type == "cross":
            a = self.leg_a.contract if self.leg_a and self.leg_a.mode == "contract" else (self.leg_a.product if self.leg_a else "?")
            b = self.leg_b.contract if self.leg_b and self.leg_b.mode == "contract" else (self.leg_b.product if self.leg_b else "?")
            return [a, b]
        return []


def load_settings() -> Settings:
    raw = _load_yaml(CONFIG_DIR / "settings.yaml")
    d = raw.get("data", {})
    h = raw.get("hedge", {})
    b = raw.get("backtest", {})
    data = DataConfig(
        datahub_root=os.environ.get("FUTURES_DATAHUB_ROOT", d.get("datahub_root", "") or ""),
        sqlite_path=d.get("sqlite_path", "data/commodities_strategies.db"),
        cache_dir=d.get("cache_dir", "data/cache"),
        lookback_days=int(d.get("lookback_days", 900)),
        source_priority=list(d.get("source_priority", ["datahub", "akshare", "tushare", "sina_web"])),
        tushare_token_env=d.get("tushare_token_env", "TUSHARE_TOKEN"),
        request_sleep=float(d.get("request_sleep", 0.35)),
    )
    hedge = HedgeConfig(
        percentile_window=int(h.get("percentile_window", 252)),
        min_observations=int(h.get("min_observations", 252)),
        entry_z=float(h.get("entry_z", 2.0)),
        exit_z=float(h.get("exit_z", 0.5)),
        extreme_pct=float(h.get("extreme_pct", 0.95)),
        vol_window=int(h.get("vol_window", 20)),
        vol_extreme=float(h.get("vol_extreme", 0.80)),
        adf_alpha=float(h.get("adf_alpha", 0.05)),
        hurst_threshold=float(h.get("hurst_threshold", 0.50)),
        confirm_days=int(h.get("confirm_days", 0)),
        fundamental_filter=bool(h.get("fundamental_filter", True)),
        require_both_conflicts=bool(h.get("require_both_conflicts", True)),
        basis_extreme_high=float(h.get("basis_extreme_high", 0.85)),
        basis_extreme_low=float(h.get("basis_extreme_low", 0.15)),
        warehouse_chg_pct=float(h.get("warehouse_chg_pct", 5.0)),
    )
    bt = BacktestConfig(
        target_notional=float(b.get("target_notional", 300_000)),
        commission_turnover=float(b.get("commission_turnover", 0.0002)),
        slippage_turnover=float(b.get("slippage_turnover", 0.0008)),
        margin_rate=float(b.get("margin_rate", 0.10)),
        annual_days=int(b.get("annual_days", 252)),
    )
    return Settings(data=data, hedge=hedge, backtest=bt,
                    project_name=raw.get("project", {}).get("name", "commodities_strategies"),
                    timezone=raw.get("project", {}).get("timezone", "Asia/Shanghai"))


def load_products() -> Dict[str, Product]:
    raw = _load_yaml(CONFIG_DIR / "products.yaml")
    out: Dict[str, Product] = {}
    for code, item in (raw.get("products") or {}).items():
        out[code] = Product(
            code=code,
            name=item.get("name", code),
            name_cn=item.get("name_cn", item.get("name", code)),
            exchange=item.get("exchange", ""),
            multiplier=float(item.get("multiplier", 1)),
            tick=float(item.get("tick", 1)),
            board=item.get("board", ""),
            months=[int(m) for m in item.get("months", list(range(1, 13)))],
            inventory_name=item.get("inventory_name", ""),
        )
    return out


def _leg(raw: Any) -> Optional[LegDef]:
    if not raw:
        return None
    if isinstance(raw, str):
        return LegDef(product=raw)
    return LegDef(product=raw.get("product", ""), mode=raw.get("mode", "continuous"),
                  contract=raw.get("contract"))


def load_spreads() -> List[SpreadDef]:
    raw = _load_yaml(CONFIG_DIR / "spreads.yaml")
    out: List[SpreadDef] = []
    for item in raw.get("spreads", []):
        out.append(SpreadDef(
            id=str(item["id"]),
            name=item.get("name", item["id"]),
            type=item["type"],
            calc=item.get("calc", "spread"),
            board=item.get("board", ""),
            relation_type=item.get("relation_type", ""),
            product=item.get("product"),
            months=[int(m) for m in item.get("months", [])],
            years=[int(y) for y in item.get("years", [])],
            contracts=list(item.get("contracts", [])),
            leg_a=_leg(item.get("leg_a")),
            leg_b=_leg(item.get("leg_b")),
            multiplier_a=item.get("multiplier_a"),
            multiplier_b=item.get("multiplier_b"),
            scale_a=float(item.get("scale_a", 1.0)),
            scale_b=float(item.get("scale_b", 1.0)),
            gap_months=int(item.get("gap_months", 0)),
            fundamental_products=list(item.get("fundamental_products", [])),
            note=item.get("note", ""),
        ))
    return out


def get_spread(spread_id: str) -> SpreadDef:
    for s in load_spreads():
        if s.id == spread_id:
            return s
    raise KeyError(f"未找到价差定义: {spread_id}")