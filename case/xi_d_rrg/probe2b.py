# -*- coding: utf-8 -*-
import socket, time
_orig = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **kw: [r for r in _orig(*a, **kw) if r[0] == socket.AF_INET]
socket.setdefaulttimeout(20)
import sys, warnings
warnings.filterwarnings('ignore')
import pandas as pd

sys.path.insert(0, '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5febb5eaeeffe0mTIpBeu5w0Nq0')
from ts_config import init_tushare
pro = init_tushare()

def t(name, fn):
    time.sleep(2.5)
    try:
        df = fn()
        if df is not None and len(df):
            print(f"{name}: rows={len(df)} cols={list(df.columns)[:14]}")
        else:
            print(f"{name}: empty")
    except Exception as e:
        print(f"{name}: ERR {str(e)[:90]}")

t('cn_fi', lambda: pro.cn_fi(start_date='2021-01-01', end_date='2021-12-31'))
t('fina_indicator', lambda: pro.fina_indicator(ts_code='000001.SZ', start_date='20240101', end_date='20241231'))
t('index_dailybasic', lambda: pro.index_dailybasic(ts_code='399370.SZ', start_date='20250101', end_date='20250105'))
t('dividend', lambda: pro.dividend(ts_code='000001.SZ'))
t('daily_basic', lambda: pro.daily_basic(ts_code='000001.SZ', start_date='20250101', end_date='20250103'))
t('income', lambda: pro.income(ts_code='000001.SZ', start_date='20240101', end_date='20241231'))
