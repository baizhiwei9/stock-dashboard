"""策略侧取数适配层：债券行情一律由 DataHub 提供，本模块不含取数逻辑。

职责边界（2026-11-17 重构）
==========================
- **取数在 DataHub**（含曲线抓取、日期口径对齐、快照回退、来源标注）：
  :mod:`data_hub.collectors.bond.market`，对外提供 ``get_market_data`` /
  ``get_yields`` / ``get_curve`` / ``get_spread_history``。
- **本模块只做两件事**：① 把 DataHub 的债券数据接给策略；② 管理策略自有资产
  （模型权重 ``assets/``、特征面板 ``features/``、运行产物 ``out/``）。
- 策略库不再保存债券**行情**快照；行情快照与利差历史由 DataHub 维护在
  ``data_hub/collectors/bond/data/``（``cn_yield_full.csv`` /
  ``cn_treasury_curve.csv`` / ``yield_spread_5y.csv``）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, Tuple

import pandas as pd

from ..bond_strategies import edb as hub_edb

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data" / "bond_yield"
FEATURE_DIR = DATA_DIR / "features"
ASSET_DIR = DATA_DIR / "assets"
OUT_DIR = DATA_DIR / "out"

#: 特征面板（策略自有的特征工程产物，不是原始行情）
PANEL_87 = FEATURE_DIR / "panel_87_ffill.csv"
PANEL_50 = FEATURE_DIR / "panel_50feat.csv"


def _market():
    """加载 DataHub 债券取数服务（bond 子包）。"""
    for p in hub_edb.candidate_roots(None):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    from data_hub.collectors.bond import market  # noqa: WPS433
    return market


# ============================ 策略自有资产 ============================
def load_features() -> Tuple[pd.DataFrame, pd.DataFrame]:
    """读 87 维与 50 维特征面板（模型输入，策略侧维护）。"""
    p87 = pd.read_csv(PANEL_87, index_col=0, parse_dates=True)
    p50 = pd.read_csv(PANEL_50, index_col=0, parse_dates=True)
    return p87, p50


def asset_path(name: str) -> Path:
    return ASSET_DIR / name


def ensure_out_dir() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)


def spread_path() -> Path:
    """利差历史路径（由 DataHub 维护，策略只读）。"""
    return _market().spread_path()


# ============================ DataHub 数据入口 ============================
def load_snapshot() -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """返回 ``(收益率, 中债8期限曲线, 87维面板, 50维面板)``。

    前两者来自 DataHub 维护的债券数据，后两者是策略自有特征面板。调用方拿到后
    再调 :func:`refresh_yields` / :func:`refresh_curve` 做增量刷新。
    """
    market = _market()
    cn = market.load_yields_snapshot()
    cb = market.load_curve_snapshot()
    p87, p50 = load_features()
    return cn, cb, p87, p50


def refresh_yields(cn: Optional[pd.DataFrame] = None) -> Tuple[pd.DataFrame, bool, dict]:
    """经 DataHub 刷新国债 2/5/10/30 年收益率。"""
    return _market().refresh_yields(cn)


def refresh_curve(cb: Optional[pd.DataFrame] = None,
                  cn: Optional[pd.DataFrame] = None) -> Tuple[pd.DataFrame, dict]:
    """经 DataHub 刷新中债 8 期限曲线（偏移对齐与落盘都在 DataHub 内完成）。"""
    return _market().refresh_curve(cb, cn, persist=True)


def save_curve_snapshot(cb: pd.DataFrame) -> Path:
    """兼容入口：曲线由 DataHub 落盘，这里只返回其快照路径。"""
    return _market().data_dir() / "cn_treasury_curve.csv"


def market_data(end: Optional[str] = None) -> dict:
    """一次性取齐日报所需数据（含 provenance），用于归因与体检。"""
    return _market().get_market_data(end)


def load_spread_history() -> Tuple[Optional[pd.DataFrame], Optional[str]]:
    """利差历史（10Y-5Y / 30Y-10Y / 30Y-5Y，bp）：读取与格式兼容都由 DataHub 负责。"""
    return _market().load_spread_history()


def save_spread_history(hist: Optional[pd.DataFrame]) -> Path:
    """利差历史写回：策略侧不直接落盘，统一交给 DataHub 维护的快照文件。"""
    return _market().save_spread_history(hist)
