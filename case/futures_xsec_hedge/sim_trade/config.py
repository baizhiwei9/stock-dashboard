# -*- coding: utf-8 -*-
"""模拟盘全局配置 —— 横截面多空对冲 · 化工/农产品板块内部对冲
==========================
与回测口径严格一致（backtest_opp3 主配置），并为模拟盘补充账户参数：
- 每个板块单元 10 万本金（化工单元 + 农产品单元各 10 万，单元合计 20 万，与套利单元各 20 万并列）
- 保证金率 >= 10%（按 10% 估算保证金占用）
- 目标：每板块多头名义 = 0.375×单元净值 / 空头名义 = 0.375×单元净值
  两板块多空合计名义 / 总净值 = 1.5 倍（横截面口径沿用 BASE_LEV=0.375）
"""
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parent                       # 套利任务工程根（斜截面对冲）
DATA_FULL = WORKSPACE / "data" / "full"
PANEL_FILE = DATA_FULL / "panel_full.csv"
FACTORS_FILE = DATA_FULL / "factors_full.csv"  # 2022 重建统一口径（回测已验证）
KLINE_DIR = WORKSPACE / "data"
INV_DIR = WORKSPACE / "data"
STATE_DIR = ROOT / "state"
OUT_DIR = ROOT / "output"
for d in (STATE_DIR, OUT_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ---- 板块 ----
CHEM = {"MA", "TA", "SA", "UR", "V", "PP", "L", "EG", "EB"}
SECTORS = ["化工", "农产品"]

# ---- 账户 ----
UNIT_CAPITAL = 100_000.0        # 每个板块单元本金 10 万（化工+农产品=20 万，与套利单元各 20 万并列）
TOTAL_CAPITAL = UNIT_CAPITAL * 2.0   # 单元合计 20 万
# 持仓量横截面对冲单元（独立核算）：每板块 100 万，承载 z 权重下真实手数（10 万口径多数品种开不出 1 手）
OI_UNIT_CAPITAL = 1_000_000.0
OI_TOTAL_CAPITAL = OI_UNIT_CAPITAL * 2.0
MARGIN_RATE = 0.10              # 保证金率（按 10% 估算，>= 用户要求 10%）
COST_RATE = 0.00025             # 单边成本 0.025%
BASE_LEV = 0.375                # 每板块每侧目标权重（多/空各 0.375×单元净值）
MAX_LEV = 1.5                   # 总杠杆上限：多空合计名义 / 总净值（BASE_LEV=0.375 时实际约 0.75 倍，上限留有余量）
DD_TRIGGER = 0.08               # 组合回撤 8% 触发全组合降仓至 50%

# ---- 机会驱动配对参数（与回测 opp_2022_grid 推荐档一致）----
SIGMA_GATE = 0.8                # 板块分化度门槛：σ>=0.8 视为有交易机会
K = 0.5                         # 极值腿门槛：|score_adj - 板块均| >= k×σ
MAX_N = 3                       # 单板块腿数上限
LAM = 0.6                       # 动量惩罚系数：score_adj = score - λ·momz_20
SIGMA_WIN = 60                  # 开仓基准 score 波动窗口（风控 1σ/2σ 用）

# ---- 调仓频率：双周 W-FRI 最后一个交易日（与回测一致）----

# ---- 合约乘数（交易所标准：元/吨 对应每手吨数；JD 报价元/500kg 乘数 10）----
CONTRACT_MULTIPLIER = {
    # 化工
    "MA": 10, "TA": 5, "SA": 20, "UR": 20, "V": 5,
    "PP": 5, "L": 5, "EG": 10, "EB": 5,
    # 农产品
    "M": 10, "RM": 10, "Y": 10, "P": 10, "OI": 10,
    "SR": 10, "CF": 5, "C": 10, "AP": 10, "JD": 10,
}

UNIVERSE = sorted(CHEM | {"M", "RM", "Y", "P", "OI", "SR", "CF", "C", "AP", "JD"})

SECTOR_OF = {v: ("化工" if v in CHEM else "农产品") for v in UNIVERSE}

# ---- 品种中文名（展示用，来源：交易所/99qh 品种名称）----
NAME_OF = {
    # 化工
    "MA": "甲醇", "TA": "PTA", "SA": "纯碱", "UR": "尿素", "V": "PVC",
    "PP": "聚丙烯", "L": "聚乙烯", "EG": "乙二醇", "EB": "苯乙烯",
    # 农产品
    "M": "豆粕", "RM": "菜籽粕", "Y": "豆油", "P": "棕榈油", "OI": "菜籽油",
    "SR": "白糖", "CF": "棉花", "C": "玉米", "AP": "苹果", "JD": "鸡蛋",
}
SHOW_CODE = True                 # True=展示“名称（代码）”；False=只展示名称


def vname(v) -> str:
    """展示用品种名。默认“玉米（C）”；SHOW_CODE=False 时仅“玉米”"""
    if v in NAME_OF:
        return f"{NAME_OF[v]}（{v}）" if SHOW_CODE else NAME_OF[v]
    return str(v)