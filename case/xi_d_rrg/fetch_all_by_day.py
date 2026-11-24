# -*- coding: utf-8 -*-
"""tushare 按交易日全市场批量拉取: 资金流/融资/估值(股息), 只保留成分股
   断点续传, 串行, 退避重试, 按月存储 parquet"""
import socket, time, os, sys, json
_orig = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **kw: [r for r in _orig(*a, **kw) if r[0] == socket.AF_INET]
socket.setdefaulttimeout(20)
import warnings; warnings.filterwarnings('ignore')
import pandas as pd

sys.path.insert(0, '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5febb5eaeeffe0mTIpBeu5w0Nq0')
from ts_config import init_tushare
pro = init_tushare()

BASE = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5fd6b2558bffeYG74Ts057P2dLR/strategy_repro'
DATA = os.path.join(BASE, 'data')

# 成分股集合
codes = pd.read_csv(os.path.join(DATA, 'constituent_codes.csv'))['code'].tolist()
code_set = set(codes)

# 交易日历(取自指数日线 20191230起)
idx = pd.read_csv(os.path.join(DATA, 'index_399370.csv'), dtype={'trade_date': str})
days = sorted(idx[idx['trade_date'] >= '20191230']['trade_date'].tolist())
print('trade days:', len(days), days[0], '-', days[-1], flush=True)

def call(fn, retries=10):
    for i in range(retries):
        try:
            return fn()
        except Exception as e:
            msg = str(e)
            if 'ip超限' in msg or 'limit' in msg.lower() or '频率' in msg or '并发' in msg:
                time.sleep(20 + i*10)
                continue
            raise e
    return None

def save_append(dataset, day, df):
    """按月份保存, 只留成分股行"""
    if df is None or len(df) == 0:
        return
    df = df[df['ts_code'].isin(code_set)]
    if len(df) == 0:
        return
    d = os.path.join(DATA, dataset)
    os.makedirs(d, exist_ok=True)
    month = day[:6]
    fp = os.path.join(d, f'{month}.csv')
    if os.path.exists(fp):
        old = pd.read_csv(fp)
        df = pd.concat([old, df]).drop_duplicates(subset=['ts_code','trade_date'], keep='last')
    df.to_csv(fp, index=False)

done_file = os.path.join(DATA, 'byday_done.json')
done = json.load(open(done_file)) if os.path.exists(done_file) else {}

FUNCS = {
    'moneyflow': lambda d: pro.moneyflow(trade_date=d),
    'margin': lambda d: pro.margin_detail(trade_date=d),
    'daily_basic': lambda d: pro.daily_basic(trade_date=d, fields='ts_code,trade_date,dv_ratio,pe_ttm,close,total_mv,circ_mv'),
}

for i, day in enumerate(days):
    for ds, fn in FUNCS.items():
        key = f'{ds}:{day}'
        if done.get(key): continue
        df = call(fn(day) if False else (lambda fn=fn, day=day: fn(day)))
        if df is not None and len(df):
            save_append(ds, day, df)
            done[key] = True
        else:
            done[key] = 'empty'
        if i % 20 == 0:
            print(f'{time.strftime("%H:%M:%S")} {i}/{len(days)} {day} last={ds}', flush=True)
        with open(done_file, 'w') as f: json.dump(done, f)
        time.sleep(1.8)
print('ALL DONE', flush=True)
