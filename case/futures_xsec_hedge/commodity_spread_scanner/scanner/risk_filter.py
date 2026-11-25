#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
风险过滤: 手数换算、成本估算、基本面过滤
"""

import numpy as np
from typing import Tuple, Optional

from ..config.config import (
    CONTRACT_MULTIPLIER, COST_MODEL, DEFAULT_COMMISSION_RATE,
    MARGIN_RATE, MAX_POSITION_PER_PAIR, MIN_NOTIONAL_RATIO,
)


def calc_sizes(pair, price_a: float, price_b: float,
               base_lots: int = 1) -> Tuple[int, int]:
    """
    等名义价值手数换算
    使两腿名义价值尽量接近: lots_a * multiplier_a * price_a ≈ lots_b * multiplier_b * price_b

    返回: (lots_a, lots_b)
    """
    mult_a = CONTRACT_MULTIPLIER.get(pair.leg_a, pair.multiplier_a)
    mult_b = CONTRACT_MULTIPLIER.get(pair.leg_b, pair.multiplier_b)

    notional_a = base_lots * mult_a * price_a
    notional_b = base_lots * mult_b * price_b

    if notional_a < 1 or notional_b < 1:
        return base_lots, base_lots

    # 以较大名义价值为基准, 调整另一腿手数
    if notional_a >= notional_b:
        lots_a = base_lots
        target_notional = notional_a
        lots_b = max(1, round(target_notional / (mult_b * price_b))) if (mult_b * price_b) > 0 else base_lots
    else:
        lots_b = base_lots
        target_notional = notional_b
        lots_a = max(1, round(target_notional / (mult_a * price_a))) if (mult_a * price_a) > 0 else base_lots

    # 限制最大手数
    lots_a = min(lots_a, MAX_POSITION_PER_PAIR)
    lots_b = min(lots_b, MAX_POSITION_PER_PAIR)

    return lots_a, lots_b


def estimate_trade_cost(leg: str, price: float, lots: int) -> float:
    """
    估算单腿交易成本(开+平合计)
    返回: 元
    """
    mult = CONTRACT_MULTIPLIER.get(leg, 10)
    notional = lots * mult * price

    cost_entry = COST_MODEL.get(leg, (0, DEFAULT_COMMISSION_RATE))
    if isinstance(cost_entry, tuple):
        fixed, rate = cost_entry
        cost = (fixed * lots * 2) + (notional * rate * 2)  # *2 开+平
    else:
        cost = notional * DEFAULT_COMMISSION_RATE * 2

    return round(cost, 2)


def estimate_pair_cost(pair, price_a: float, price_b: float,
                       lots_a: int, lots_b: int) -> float:
    """估算配对交易总成本(两腿合计)"""
    cost_a = estimate_trade_cost(pair.leg_a, price_a, lots_a)
    cost_b = estimate_trade_cost(pair.leg_b, price_b, lots_b)
    return round(cost_a + cost_b, 2)


def check_notional_ratio(pair, price_a: float, price_b: float,
                         lots_a: int, lots_b: int) -> bool:
    """
    检查两腿名义价值比是否满足最低要求
    返回 True=通过, False=不对等需要过滤
    """
    mult_a = CONTRACT_MULTIPLIER.get(pair.leg_a, pair.multiplier_a)
    mult_b = CONTRACT_MULTIPLIER.get(pair.leg_b, pair.multiplier_b)

    notional_a = lots_a * mult_a * price_a
    notional_b = lots_b * mult_b * price_b

    if notional_a < 1 or notional_b < 1:
        return False

    ratio = min(notional_a, notional_b) / max(notional_a, notional_b)
    return ratio >= MIN_NOTIONAL_RATIO


def estimate_profit_potential(z: float, entry_z: float = 2.0) -> Optional[float]:
    """
    估算回归利润潜力(基于z-score偏离程度)
    返回: 预期回归幅度(占当前z的比例), 或None(不满足条件)
    """
    abs_z = abs(z)
    if abs_z < entry_z:
        return None
    # 越偏离均值, 回归潜力越大
    # 简单线性: 潜力 = (abs_z - exit_z) / abs_z
    potential = (abs_z - 0.5) / abs_z if abs_z > 0 else 0
    return round(potential, 3)
