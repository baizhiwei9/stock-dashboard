# -*- coding: utf-8 -*-
"""用 akshare 拉宏观数据: PPI/CPI/M1M2/固投"""
import socket
_orig = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **kw: [r for r in _orig(*a, **kw) if r[0] == socket.AF_INET]
socket.setdefaulttimeout(30)
import warnings; warnings.filterwarnings('ignore')
import pandas as pd
import akshare as ak

BASE = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5fd6b2558bffeYG74Ts057P2dLR/strategy_repro/data'

tests = {
    'ppi': lambda: ak.macro_china_ppi_yearly(),
    'cpi': lambda: ak.macro_china_cpi_monthly(),
    'm1m2': lambda: ak.macro_china_money_supply(),
    'fi': lambda: ak.macro_china_fixed_investment(),
}
for k, fn in tests.items():
    try:
        df = fn()
        df.to_csv(f'{BASE}/macro_{k}.csv', index=False)
        print(k, df.shape, list(df.columns)[:8])
        print(df.head(2).to_string())
    except Exception as e:
        print(k, 'ERR', str(e)[:150])
