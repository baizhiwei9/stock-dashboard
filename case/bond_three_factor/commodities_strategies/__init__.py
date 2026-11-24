# -*- coding: utf-8 -*-
"""commodities_strategies —— 期货对冲套利价差工程。

顶层分工：
- Futures：行情数据层（DataHub 优先，AKShare/TuShare/新浪兜底）
- Hedge  ：对冲套利层（价差构建、分位数、统计稳健性、信号、回测）
- storage：SQLite 持久化
"""
__version__ = "0.6.0"
