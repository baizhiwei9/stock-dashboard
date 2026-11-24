# -*- coding: utf-8 -*-
import sys
sys.path.insert(0,"/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ed3f1d6b1ffeo164XKTVu7vvVA/raw")
from ts_session import api
import pandas as pd
# 1) 试 ths_daily 单日全板
try:
    d1=api("ths_daily", trade_date="20261029")
    print("[ths_daily by trade_date] shape=", d1.shape, "cols=", list(d1.columns)[:6])
    print("n_boards=", d1.ts_code.nunique() if len(d1) else 0)
except Exception as e:
    print("by trade_date FAIL", e)
# 2) 试 ths_daily 区间全板（不带 ts_code）
try:
    d2=api("ths_daily", start_date="20261020", end_date="20261029")
    print("[ths_daily range] shape=", d2.shape, "n_boards=", d2.ts_code.nunique() if len(d2) else 0)
except Exception as e:
    print("range FAIL", e)
# 3) ths_index 列表
try:
    ti=api("ths_index")
    print("[ths_index] shape=", ti.shape, list(ti.columns))
    print(ti.exchange.value_counts().to_dict() if 'exchange' in ti else ti.head())
    print("type counts:", ti.type.value_counts().to_dict() if 'type' in ti else 'na')
except Exception as e:
    print("ths_index FAIL", e)
