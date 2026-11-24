# -*- coding: utf-8 -*-
"""拉取国证成长/价值成分股权重(月度快照, 2020-01 至 2026-08)"""
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

def call(fn, retries=6):
    for i in range(retries):
        try:
            return fn()
        except Exception as e:
            if 'ip超限' in str(e) or 'limit' in str(e).lower() or '频率' in str(e):
                time.sleep(10 + i*6)
                continue
            raise e
    return None

# 按季度拉，每季度返回月末快照
for code, name in [('399370.SZ','growth'), ('399371.SZ','value')]:
    out = os.path.join(DATA, f'weights_{name}.csv')
    parts = []
    years = range(2020, 2027)
    for y in years:
        for q in range(1, 5):
            sm = (q-1)*3 + 1
            em = q*3
            sd = f'{y}{sm:02d}01'
            ed = f'{y}{em:02d}31'
            if y == 2026 and sm > 8: break
            if (y, q) > (2026, 3): break
            df = call(lambda: pro.index_weight(index_code=code, start_date=sd, end_date=ed))
            if df is not None and len(df):
                parts.append(df)
            time.sleep(2.2)
    if parts:
        allw = pd.concat(parts).drop_duplicates()
        allw.to_csv(out, index=False)
        print(name, 'rows=', len(allw), 'snapshots=', allw['trade_date'].nunique() if 'trade_date' in allw else '?')
    else:
        print(name, 'FAILED')
