# -*- coding: utf-8 -*-
"""拉取国证成长/价值指数日线(全区间2010-2026)"""
import socket, time, os, sys
_orig = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **kw: [r for r in _orig(*a, **kw) if r[0] == socket.AF_INET]
socket.setdefaulttimeout(30)
import warnings; warnings.filterwarnings('ignore')
import pandas as pd

sys.path.insert(0, '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5febb5eaeeffe0mTIpBeu5w0Nq0')
from ts_config import init_tushare
pro = init_tushare()

DATA = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5fd6b2558bffeYG74Ts057P2dLR/strategy_repro/data'

def call(fn, retries=5):
    for i in range(retries):
        try:
            return fn()
        except Exception as e:
            if 'ip超限' in str(e) or 'limit' in str(e).lower() or '频率' in str(e):
                time.sleep(8 + i*5)
                continue
            raise e
    return None

for code in ['399370.SZ', '399371.SZ']:
    out = os.path.join(DATA, f'index_{code[:6]}.csv')
    if os.path.exists(out):
        print(code, 'cached'); continue
    df = call(lambda: pro.index_daily(ts_code=code, start_date='20091228', end_date='20260822'))
    if df is None or len(df) == 0:
        print(code, 'FAILED'); continue
    df = df.sort_values('trade_date').reset_index(drop=True)
    df.to_csv(out, index=False)
    print(code, 'rows=', len(df), 'range=', df['trade_date'].iloc[0], '-', df['trade_date'].iloc[-1])
