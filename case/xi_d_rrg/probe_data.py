# -*- coding: utf-8 -*-
"""数据可得性探测脚本"""
import socket
_orig = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **kw: [r for r in _orig(*a, **kw) if r[0] == socket.AF_INET]
socket.setdefaulttimeout(30)

import sys, warnings
warnings.filterwarnings('ignore')
import pandas as pd
pd.set_option('display.width', 200)

sys.path.insert(0, '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5febb5eaeeffe0mTIpBeu5w0Nq0')
from ts_config import init_tushare
pro = init_tushare()

tests = {}

# 1. 指数日线: 国证成长 399370.SZ / 国证价值 399371.SZ
try:
    df = pro.index_daily(ts_code='399370.SZ', start_date='20220101', end_date='20260822')
    tests['index_daily_399370'] = f"rows={len(df)} cols={list(df.columns)} last={df['trade_date'].iloc[-1] if len(df) else 'N/A'}"
except Exception as e:
    tests['index_daily_399370'] = f"ERR {e}"

try:
    df = pro.index_daily(ts_code='399371.SZ', start_date='20220101', end_date='20260822')
    tests['index_daily_399371'] = f"rows={len(df)} last={df['trade_date'].iloc[-1] if len(df) else 'N/A'}"
except Exception as e:
    tests['index_daily_399371'] = f"ERR {e}"

# 2. 指数成分股
for code in ['399370.SZ', '399371.SZ']:
    try:
        df = pro.index_weight(index_code=code, start_date='20240101', end_date='20260101')
        tests[f'index_weight_{code}'] = f"rows={len(df)} last={df['trade_date'].iloc[-1] if len(df) else 'N/A'}"
    except Exception as e:
        tests[f'index_weight_{code}'] = f"ERR {str(e)[:120]}"

# 3. 宏观数据
try:
    df = pro.cn_ppi(month='20240101')
    tests['cn_ppi'] = f"rows={len(df)} cols={list(df.columns)}" if df is not None and len(df) else "empty"
except Exception as e:
    tests['cn_ppi'] = f"ERR {str(e)[:120]}"

try:
    df = pro.cn_m(month='20240101')
    tests['cn_m'] = f"rows={len(df)} cols={list(df.columns)}" if df is not None and len(df) else "empty"
except Exception as e:
    tests['cn_m'] = f"ERR {str(e)[:120]}"

try:
    df = pro.cn_cpi(month='20240101')
    tests['cn_cpi'] = f"rows={len(df)} cols={list(df.columns)}" if df is not None and len(df) else "empty"
except Exception as e:
    tests['cn_cpi'] = f"ERR {str(e)[:120]}"

# 4. 个股资金流(用于成分股聚合)
try:
    df = pro.moneyflow(ts_code='000001.SZ', start_date='20250101', end_date='20250105')
    tests['moneyflow'] = f"rows={len(df)} cols={list(df.columns)}" if df is not None and len(df) else "empty"
except Exception as e:
    tests['moneyflow'] = f"ERR {str(e)[:120]}"

# 5. 融资融券
try:
    df = pro.margin_detail(ts_code='000001.SZ', start_date='20250101', end_date='20250105')
    tests['margin_detail'] = f"rows={len(df)} cols={list(df.columns)}" if df is not None and len(df) else "empty"
except Exception as e:
    tests['margin_detail'] = f"ERR {str(e)[:120]}"

# 6. 指数日线用akshare备用
try:
    import akshare as ak
    df = ak.stock_zh_index_daily_tx(symbol='sz399370')
    tests['ak_tx_399370'] = f"rows={len(df)} last={df.index[-1] if hasattr(df.index,'shape') else df.iloc[-1].name} cols={list(df.columns)}"
except Exception as e:
    tests['ak_tx_399370'] = f"ERR {str(e)[:120]}"

for k, v in tests.items():
    print(f"{k}: {v}")
