# -*- coding: utf-8 -*-
"""兼容适配层（已废弃直连，全量改走 DataHub/EDB）

历史上本模块直连 TuShare 代理网关（自己读 ``ts_config`` 里的 token 与 URL、
自己 POST）。按「取数统一收敛到 DataHub」的要求，这里只保留**签名兼容**：
``call(api, params) -> (fields, items)``，实际取数转发给 :mod:`dh`
→ ``data_hub.edb.get_dataset`` → EDB 数据集层。

新代码请直接用 ``import dh`` 里的业务函数（``dh.margin_summary`` /
``dh.margin_detail_day`` / ``dh.daily_basic_day`` / ``dh.etf_share`` / ``dh.etf_price`` /
``dh.index_daily`` / ``dh.limit_counts_day`` / ``dh.stock_basic`` / ``dh.industry_map`` /
``dh.trade_calendar``），不要再用本模块。

未在 EDB 数据集层登记的接口一律报错，不再提供旁路。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import dh  # noqa: E402  DataHub/EDB 唯一取数入口


def call(api, params=None, retry=3, timeout=60):
    """兼容老调用形式，数据仍全部来自 DataHub/EDB（``retry``/``timeout`` 已不生效）。"""
    return dh.call(api, params or {})