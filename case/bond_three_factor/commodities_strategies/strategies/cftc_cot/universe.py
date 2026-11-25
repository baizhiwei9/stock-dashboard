# -*- coding: utf-8 -*-
"""品种注册表：CFTC 市场全名 / 外盘 AKShare symbol / 国内主力 symbol。

CFTC 的 `Market_and_Exchange_Names` 会随交易所改名而变，历史别名必须一起匹配，
否则 2022 年前的铜持仓会整段缺失（COMEX 铜 2022 年由 COPPER-GRADE #1 改为 COPPER- #1）。

WTI 原油同理且更麻烦：2022 年中由
`CRUDE OIL, LIGHT SWEET - NEW YORK MERCANTILE EXCHANGE` 改为
`WTI-PHYSICAL - NEW YORK MERCANTILE EXCHANGE`。两者共用 CFTC 合约代码 **067651**，
2022 年度包里新旧两个名字会同时出现（52 行重叠），
因此除别名集合外再加一层 `cftc_code` 兜底匹配，并在拼装时按报告日去重。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class Commodity:
    key: str                      # 内部标识
    name_cn: str                  # 展示名（COMEX铜）
    cftc_name: str                # CFTC Market_and_Exchange_Names 精确值
    akshare_symbol: str           # akshare.futures_foreign_hist symbol
    dom_code: Optional[str] = None      # 仓库品种代码（products.yaml），None = 无国内对应
    dom_name: str = ""
    dom_ak_symbol: Optional[str] = None  # akshare.futures_main_sina symbol
    exchange: str = ""
    aliases: Tuple[str, ...] = field(default_factory=tuple)
    note: str = ""
    cftc_code: Optional[str] = None      # CFTC_Contract_Market_Code 兜底匹配（仅改过名的品种需要）

    @property
    def cftc_names(self) -> Tuple[str, ...]:
        return (self.cftc_name, *self.aliases)


COMMODITIES: List[Commodity] = [
    Commodity("copper", "COMEX铜", "COPPER- #1 - COMMODITY EXCHANGE INC.", "HG",
              dom_code="CU", dom_name="沪铜", dom_ak_symbol="CU0", exchange="COMEX",
              aliases=("COPPER-GRADE #1 - COMMODITY EXCHANGE INC.",),
              note="国内沪铜与 COMEX 铜联动弱，COT 信号不可直接套"),
    Commodity("gold", "COMEX黄金", "GOLD - COMMODITY EXCHANGE INC.", "GC",
              dom_code="AU", dom_name="沪金", dom_ak_symbol="AU0", exchange="COMEX",
              note="国内沪金可参考（历史单周胜率约 59%）"),
    Commodity("silver", "COMEX白银", "SILVER - COMMODITY EXCHANGE INC.", "SI",
              dom_code="AG", dom_name="沪银", dom_ak_symbol="AG0", exchange="COMEX",
              note="国内沪银是 4 品种里传导最好的（约 62%）"),
    Commodity("corn", "CBOT玉米", "CORN - CHICAGO BOARD OF TRADE", "C",
              dom_code="C", dom_name="玉米", dom_ak_symbol="C0", exchange="CBOT",
              note="内外盘定价体系不同，联动弱，国内不要套"),
    Commodity("crude", "WTI原油", "WTI-PHYSICAL - NEW YORK MERCANTILE EXCHANGE", "CL",
              dom_code="SC", dom_name="上海原油", dom_ak_symbol="SC0", exchange="NYMEX",
              aliases=("CRUDE OIL, LIGHT SWEET - NEW YORK MERCANTILE EXCHANGE",),
              cftc_code="067651",
              note="2022 年中 CFTC 合约更名（旧名 CRUDE OIL, LIGHT SWEET），已按合约代码 067651 兼容；"
                   "原油 COT 极值常对应趋势延续而非反转，历史胜率明显低于金属，别照搬金属仓位"),
]

BY_KEY: Dict[str, Commodity] = {c.key: c for c in COMMODITIES}
DEFAULT_KEYS: Tuple[str, ...] = ("copper", "gold", "silver", "corn", "crude")


def get(key: str) -> Commodity:
    try:
        return BY_KEY[key]
    except KeyError:
        raise KeyError(f"未知品种: {key}（可选 {', '.join(BY_KEY)}）") from None


def select(keys: Optional[List[str]] = None) -> List[Commodity]:
    """按配置顺序取品种；空/None 用默认品种（铜/金/银/玉米/原油）。"""
    if not keys:
        return [get(k) for k in DEFAULT_KEYS]
    return [get(str(k)) for k in keys]
