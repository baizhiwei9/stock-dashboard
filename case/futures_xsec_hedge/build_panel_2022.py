#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从全量K线重建 panel（main/near/次近月/roll/basis/atr/ret_hold），覆盖 2021-06 起。"""
import pandas as pd, numpy as np, math

k = pd.read_csv("data/full/all_kline.csv", parse_dates=["date"])
k = k.sort_values(["variety","contract","date"]).reset_index(drop=True)

def mon(c):  # 合约月份序号 2024-09 -> 2409 -> 2000+24*12+9
    return 2000 + int(c[-4:-2]) * 12 + int(c[-2:])

def exp_month(c):
    return (2000 + int(c[-4:-2])) * 12 + int(c[-2:])

MONTH_DAYS = {  # 按品种的月长常数（从既有面板反推中位）
    "AP":30.0,"C":30.5,"CF":30.5,"EB":31.0,"EG":31.0,"JD":31.0,"L":31.0,"M":30.5,
    "MA":31.0,"OI":30.5,"P":31.0,"PP":31.0,"RM":30.5,"SA":31.0,"SR":30.5,"TA":31.0,
    "UR":31.0,"V":31.0,"Y":30.5,CENTER:=None
}
