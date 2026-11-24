# -*- coding: utf-8 -*-
"""v2: 按成分股 ts_code 一次拉全历史(moneyflow/margin/income/daily_basic)
   串行短间隔 + 断点续传, 复用 fetch_done.json; 已完成项跳过"""
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
SD, ED = '20191230', '20260822'
codes = pd.read_csv(os.path.join(DATA, 'constituent_codes.csv'))['code'].tolist()

def call(fn, retries=10):
    for i in range(retries):
        try:
            return fn()
        except Exception as e:
            msg = str(e)
            if 'ip超限' in msg or 'limit' in msg.lower() or '频率' in msg or '并发' in msg:
                time.sleep(8 + i*6)
                continue
            if '当日' in msg and '次数' in msg:
                time.sleep(20 + i*15)
                continue
            raise e
    return None

def save(kind, code, df):
    d = os.path.join(DATA, kind)
    os.makedirs(d, exist_ok=True)
    df.to_csv(os.path.join(d, code.replace('.','_') + '.csv'), index=False)

done_file = os.path.join(DATA, 'fetch_done.json')
done = {}
if os.path.exists(done_file):
    with open(done_file) as f: done = json.load(f)

KINDS = {
    'moneyflow': lambda c: pro.moneyflow(ts_code=c, start_date=SD, end_date=ED),
    'margin': lambda c: pro.margin_detail(ts_code=c, start_date=SD, end_date=ED),
    'income': lambda c: pro.income(ts_code=c, start_date='20181230', end_date=ED,
                                   fields='ts_code,ann_date,f_ann_date,end_date,report_type,n_income,total_revenue'),
    'daily_basic': lambda c: pro.daily_basic(ts_code=c, start_date=SD, end_date=ED,
                                             fields='ts_code,trade_date,dv_ratio,pe_ttm,close,total_mv,circ_mv'),
}

todo = [(k, c) for k in KINDS for c in codes]
n_done = sum(1 for k in todo if done.get(f'{k[0]}:{k[1]}'))
print(f'total tasks {len(todo)}, already done {n_done}', flush=True)
t0 = time.time()
cnt = [0]
for i, (k, c) in enumerate(todo):
    key = f'{k}:{c}'
    if done.get(key):
        continue
    df = call(lambda c=c, k=k: KINDS[k](c))
    if df is not None and len(df):
        save(k, c, df)
        done[key] = True
    else:
        done[key] = False
    cnt[0] += 1
    if cnt[0] % 20 == 0:
        el = time.time() - t0
        print(f'{time.strftime("%H:%M:%S")} +{cnt[0]} new done; eta {el/cnt[0]*(sum(1 for k in todo if not done.get(f"{k[0]}:{k[1]}")))/3600:.1f}h', flush=True)
    if cnt[0] % 10 == 0:
        with open(done_file, 'w') as f: json.dump(done, f)
    time.sleep(0.6)
with open(done_file, 'w') as f: json.dump(done, f)
print('ALL DONE', flush=True)