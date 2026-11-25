#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
配对表加载与数据结构
"""

import os
import csv
from dataclasses import dataclass
from typing import List
from enum import Enum


class Board(str, Enum):
    农产品 = "农产品"
    有色金属 = "有色金属"
    贵金属 = "贵金属"
    有色贵金属 = "有色贵金属"


class RelationType(str, Enum):
    跨品种替代 = "跨品种替代"
    经济作物争地 = "经济作物争地"
    红色金属替代 = "红色金属替代"
    铅锌矿共生 = "铅锌矿共生"
    不锈钢产业链 = "不锈钢产业链"
    有色龙头联动 = "有色龙头联动"
    有色板块联动 = "有色板块联动"
    小金属配对 = "小金属配对"
    合金基体 = "合金基体"
    贵金属比价 = "贵金属比价"
    工业贵金属联动 = "工业贵金属联动"
    植物油替代 = "植物油替代"
    蛋白能量比 = "蛋白能量比"
    能量替代 = "能量替代"


class CalcMethod(str, Enum):
    spread = "spread"   # 价差: A - B
    ratio = "ratio"     # 比价: A / B


@dataclass
class Pair:
    pair_id: str
    leg_a: str
    leg_b: str
    board: str
    relation_type: str
    calc_method: str        # "spread" or "ratio"
    multiplier_a: int       # A的合约乘数
    multiplier_b: int       # B的合约乘数
    history_window: int     # 历史窗口(交易日)
    entry_z: float          # 开仓z阈值
    exit_z: float           # 平仓z阈值


def load_pair_table(csv_path: str = None) -> List[Pair]:
    """加载配对表CSV"""
    if csv_path is None:
        csv_path = os.path.join(os.path.dirname(__file__), "..", "config", "pair_table.csv")
    pairs = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            pairs.append(Pair(
                pair_id=row["pair_id"].strip(),
                leg_a=row["leg_a"].strip(),
                leg_b=row["leg_b"].strip(),
                board=row["board"].strip(),
                relation_type=row["relation_type"].strip(),
                calc_method=row["calc_method"].strip(),
                multiplier_a=int(row["multiplier_a"]),
                multiplier_b=int(row["multiplier_b"]),
                history_window=int(row["history_window"]),
                entry_z=float(row["entry_z"]),
                exit_z=float(row["exit_z"]),
            ))
    return pairs


def get_unique_symbols(pairs: List[Pair]) -> set:
    """从配对表中提取所有不重复的品种代码"""
    symbols = set()
    for p in pairs:
        symbols.add(p.leg_a)
        symbols.add(p.leg_b)
    return symbols
