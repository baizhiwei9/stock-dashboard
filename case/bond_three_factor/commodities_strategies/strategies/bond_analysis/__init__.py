# -*- coding: utf-8 -*-
"""债市分析（bond_analysis）：中国债市四层判定 + 中美信用压力 + 宏观货币信用。

数据全部走 DataHub EDB（`data_hub.edb`），不直连任何行情接口。
纯 Python 规则计算，不依赖大模型。

模块结构
--------
    data.py     只调 DataHub EDB 的取数层（定位仓库 / 取序列 / 算利差 / 分位）
    engine.py   四层判定规则（阈值集中在 TH 字典，复盘时只改这里）
    report.py   排版：纯文本 / Markdown / 飞书卡片 / matplotlib 双栏图
    run.py      命令行入口

快速开始
--------
    python -m commodities_strategies.strategies.bond_analysis.run
    python -m commodities_strategies.strategies.bond_analysis.run --status
    python -m commodities_strategies.strategies.bond_analysis.run --refresh --chart
    python -m commodities_strategies.strategies.bond_analysis.run --push
"""
from . import engine, report
from .data import BondData, connect, find_datahub_root
from .engine import BondState, Verdict, build_state, judge, summary_line

__all__ = [
    "BondData", "connect", "find_datahub_root",
    "BondState", "Verdict", "build_state", "judge", "summary_line",
    "engine", "report",
]
