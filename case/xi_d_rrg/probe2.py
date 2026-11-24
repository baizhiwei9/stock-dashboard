# -*- coding: utf-8 -*-
import socket
_orig = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **kw: [r for r in _orig(*a, **kw) if r[0] == socket.AF_INET]
socket.setdefaulttimeout(20)
import sys, warnings
warnings.filterwarnings('ignore')
import pandas as pd
pd.set_option('display.width', 250)

sys.path.insert(0, '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5febb5eaeeffe0mTIpBeu5w0Nq0')
from ts_config import init_tushare
pro = init_tushare()
tests = {}

# index_weight 覆盖
try:
    df = pro.index_weight(index_code='399370.SZ', start_date='20220101', end_date='20221231')
    tests['iw_399370_2022'] = f"rows={len(df)} trade_dates={sorted(df['trade_date'].unique())[:3]}...{sorted(df['trade_date'].unique())[-3:]}" if len(df) else "empty"
except Exception as e:
    tests['iw_399370_2022'] = f"ERR {str(e)[:100]}"

# 固投: tushare 接口名尝试
for api, kw in [('cn_fi', {'month':'20240101'}), ('cn_fi', {'start_date':'2021-01-01','end_date':'2021-12-31'}), ('cn_val', {})]:
    try:
        df = getattr(pro, api)(**kw)
        tests[f'{api}{list(kw.keys())}'] = f"rows={len(df)} cols={list(df.columns)[:12]}" if df is not None and len(df) else "empty"
    except Exception as e:
        tests[f'{api}{list(kw.keys())}'] = f"ERR {str(e)[:100]}"

# fina_indicator 财务(净利润)
try:
    df = pro.fina_indicator(ts_code='000001.SZ', start_date='20240101', end_date='20241231')
    tests['fina_indicator'] = f"rows={len(df)} cols={list(df.columns)[:15]}" if df is not None and len(df) else "empty"
except Exception as e:
    tests['fina_indicator'] = f"ERR {str(e)[:100]}"

# 指数日线基础指标(含股息率?)
try:
    df = pro.index_dailybasic(ts_code='399370.SZ', start_date='20250101', end_date='20250105')
    tests['index_dailybasic'] = f"rows={len(df)} cols={list(df.columns)}" if df is not None and len(df) else "empty"
except Exception as e:
    tests['index_dailybasic'] = f"ERR {str(e)[:100]}"

# 个股分红
try:
    df = pro.dividend(ts_code='000001.SZ')
    tests['dividend'] = f"rows={len(df)} cols={list(df.columns)[:12]}" if df is not None and len(df) else "empty"
except Exception as e:
    tests['dividend'] = f"ERR {str(e)[:100]}"

# 每日指标(个股市值/pe等)
try:
    df = pro.daily_basic(ts_code='000001.SZ', start_date='20250101', end_date='20250103')
    tests['daily_basic'] = f"rows={len(df)} cols={list(df.columns)}" if df is not None and len(df) else "empty"
except Exception as e:
    tests['daily_basic'] = f"ERR {str(e)[:100]}"

for k, v in tests.items():
    print(f"{k}: {v}")
