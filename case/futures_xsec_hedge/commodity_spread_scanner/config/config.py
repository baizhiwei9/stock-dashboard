#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
价差回归策略全局配置
合约乘数、成本模型、阈值参数
"""
import os

# ---- 统一配置（DataHub config / notify；真实值在本机 config.local.json，不入库）----
def _dh_secret(_name, _default=""):
    try:
        from data_hub import get_secret as _gs
        return _gs(_name, _default)
    except Exception:
        return _default


def _dh_cfg(_key, _default=None):
    try:
        from data_hub import cfg as _cfg
        return _cfg(_key, _default)
    except Exception:
        return _default


def _dh_tushare(_default=""):
    try:
        from data_hub import get_tushare_token as _gt
        return _gt() or _default
    except Exception:
        return _default
# ---------------------------------------------------------------------------
import sys
from pathlib import Path

_CASE_DIR = Path(__file__).resolve().parents[2]   # …/case/futures_xsec_hedge

# ===== 合约乘数 (元/点) =====
# 用于等名义价值换算手数
CONTRACT_MULTIPLIER = {
    # 有色金属
    "CU0": 5,       # 5吨/手, 元/吨
    "AL0": 5,       # 5吨/手, 元/吨
    "ZN0": 5,       # 5吨/手, 元/吨
    "PB0": 5,       # 5吨/手, 元/吨
    "NI0": 1,       # 1吨/手, 元/吨
    "SN0": 1,       # 1吨/手, 元/吨
    # 农产品
    "M0": 10,       # 10吨/手, 元/吨
    "Y0": 10,       # 10吨/手, 元/吨
    "P0": 10,       # 10吨/手, 元/吨
    "OI0": 10,      # 10吨/手, 元/吨
    "RM0": 10,      # 10吨/手, 元/吨
    "C0": 10,       # 10吨/手, 元/吨
    "SR0": 10,      # 10吨/手, 元/吨
    "CF0": 5,       # 5吨/手, 元/吨
    # 贵金属
    "AG0": 15,      # 15千克/手, 元/千克
    "AU0": 1000,    # 1000克/手, 元/克
    # 化工
    "PP0": 5,       # 5吨/手, 元/吨
    "L0": 5,        # 5吨/手, 元/吨
    "EB0": 5,       # 5吨/手, 元/吨
    "BR0": 5,       # 5吨/手, 元/吨
    "BU0": 10,      # 10吨/手, 元/吨
    "RU0": 10,      # 10吨/手, 元/吨
    "MA0": 10,      # 10吨/手, 元/吨
    "TA0": 5,       # 5吨/手, 元/吨
    "EG0": 5,       # 5吨/手, 元/吨
}

# ===== 品种中文名 =====
SYMBOL_NAME = {
    "CU0": "沪铜", "AL0": "沪铝", "ZN0": "沪锌", "PB0": "沪铅",
    "NI0": "沪镍", "SN0": "沪锡",
    "M0": "豆粕", "Y0": "豆油", "P0": "棕榈油", "OI0": "菜油",
    "RM0": "菜粕", "C0": "玉米", "SR0": "白糖", "CF0": "棉花",
    "AG0": "白银", "AU0": "黄金",
    "PP0": "聚丙烯", "L0": "聚乙烯", "EB0": "苯乙烯", "BR0": "顺丁胶",
    "BU0": "沥青", "RU0": "橡胶", "MA0": "甲醇", "TA0": "PTA", "EG0": "乙二醇",
}

# ===== 保证金率 (估算) =====
MARGIN_RATE = {
    "CU0": 0.10, "AL0": 0.10, "ZN0": 0.10, "PB0": 0.10,
    "NI0": 0.12, "SN0": 0.10,
    "M0": 0.08, "Y0": 0.08, "P0": 0.08, "OI0": 0.08,
    "RM0": 0.08, "C0": 0.08, "SR0": 0.08, "CF0": 0.08,
    "AG0": 0.10, "AU0": 0.10,
    "PP0": 0.10, "L0": 0.10, "EB0": 0.10, "BR0": 0.10,
    "BU0": 0.10, "RU0": 0.10, "MA0": 0.10, "TA0": 0.10, "EG0": 0.10,
}

# ===== 成本模型 =====
# 手续费率 (万分之几, 双边合计)
DEFAULT_COMMISSION_RATE = 0.0002  # 默认万2双边

COST_MODEL = {
    # 品种: (固定手续费元/手, 比例手续费率)
    # 优先用比例费率，无则用固定
    "CU0": (0, 0.0001),   # 万1双边
    "AL0": (3, 0),        # 固定3元/手
    "ZN0": (3, 0),
    "PB0": (3, 0),
    "NI0": (3, 0),
    "SN0": (3, 0),
    "M0":  (1.5, 0),
    "Y0":  (2.5, 0),
    "P0":  (2.5, 0),
    "OI0": (2, 0),
    "RM0": (1.5, 0),
    "C0":  (1.2, 0),
    "SR0": (3, 0),
    "CF0": (4, 0),
    "AG0": (0, 0.0001),
    "AU0": (10, 0),
    "PP0": (0, 0.0001),
    "L0": (0, 0.0001),
    "EB0": (0, 0.0001),
    "BR0": (3, 0),
    "BU0": (0, 0.0001),
    "RU0": (3, 0),
    "MA0": (0, 0.0001),
    "TA0": (3, 0),
    "EG0": (3, 0),
}

# ===== 信号阈值 =====
DEFAULT_ENTRY_Z = 2.0       # 开仓阈值: |z| >= 2.0
DEFAULT_EXIT_Z = 0.5        # 平仓阈值: |z| <= 0.5
DEFAULT_HISTORY_WINDOW = 756  # 3年(交易日)

# ===== 风险参数 =====
MAX_POSITION_PER_PAIR = 2   # 每对最大手数
MIN_NOTIONAL_RATIO = 0.5    # 两腿名义价值比需 >= 0.5 (否则不对等)
MOMENTUM_WINDOW = 5         # 动量观察窗口(交易日)

# ===== 状态文件 =====
# 不再写死某台机器的 Desktop：默认落在 case/futures_xsec_hedge/data/ 下（.gitignore 已忽略
# case/*/data/），需要换位置时用环境变量 SPREAD_STATE_FILE 覆盖。
STATE_FILE = os.environ.get("SPREAD_STATE_FILE") or str(
    _CASE_DIR / "data" / "spread_regression_state.json"
)

# ===== 飞书通知 =====
# chat_id 是群标识、不是密钥，但同样不进仓库：优先环境变量 SPREAD_FEISHU_CHAT_ID，
# 其次 case/_lib/feishu_creds 的 chat_id；都取不到就留空，调用方据此跳过群推送。
def _resolve_chat_id() -> str:
    env = _dh_secret("FEISHU_CHAT_ID", "").strip()
    if env:
        return env
    _lib = str(_CASE_DIR.parent / "_lib")
    if _lib not in sys.path:
        sys.path.insert(0, _lib)
    try:
        from feishu_creds import CHAT_ID
    except Exception:
        return ""
    return CHAT_ID or ""


FEISHU_CHAT_ID = _resolve_chat_id()
