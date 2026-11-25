# -*- coding: utf-8 -*-
"""补取 ETF 行情（一次性/补历史脚本）

数据经 DataHub/EDB 数据集 ``CN.MARKET.ETF.PRICE`` 取得（内部 fund_daily 优先、fund_nav 兜底）。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dh  # noqa: E402

import pandas as pd  # noqa: E402

D = os.path.join(HERE, "data")
START = "20260301"
END = "20261022"
ETFS = ["512480.SH", "588200.SH", "515880.SH", "159819.SZ", "512000.SH", "512800.SH", "512170.SH", "159992.SZ",
        "516270.SH", "159796.SZ", "159928.SZ", "515170.SH", "515080.SH", "563020.SH", "515220.SH", "512400.SH",
        "510300.SH", "588000.SH", "159915.SZ"]

al = dh.etf_price(ETFS, START, END)      # 一次取多标的，单只失败由数据集内部容忍
if al is None or al.empty:
    print("etf_price：未取到数据，保留原文件")
    sys.exit(1)
al.to_csv(os.path.join(D, "etf_price.csv"), index=False)
print("etf_price rows", len(al), "codes", al.ts_code.nunique(), "cols", list(al.columns)[:10])
print(al.head(2).to_string())