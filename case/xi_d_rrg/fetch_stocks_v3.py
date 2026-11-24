# -*- coding: utf-8 -*-
"""v3: 按成分股 ts_code 拉全历史(moneyflow/margin/income/daily_basic)
   多进程(默认3)并发, 每进程串行, 每worker独立断点文件 fetch_done_{wid}.json
   用法: python3 fetch_stocks_v3.py --worker 0 --nw 3  (启动 nw 个进程)"""
import socket, time, os, sys, json
_orig = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **kw: [r for r in _orig(*a, **kw) if r[0] == socket.AF_INET]
socket.setdefaulttimeout(20)
import warnings; warnings.filterwarnings('ignore')
import pandas as pd

sys.path.insert(0, '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5febb5eaeeffe0mTIpBeu5w0Nq0')
from ts_config import init_tushare

BASE = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5fd6b2558bffeYG74Ts057P2dLR/strategy_repro'
DATA = os.path.join(BASE, 'data')
SD, ED = '20191230', '20260822'
codes = pd.read_csv(os.path.join(DATA, 'constituent_codes.csv'))['code'].tolist()
MAIN_DONE = os.path.join(DATA, 'fetch_done.json')

def load_main_done():
    if os.path.exists(MAIN_DONE):
        with open(MAIN_DONE) as f:
            return json.load(f)
    return {}

def call(fn, retries=12):
    for i in range(retries):
        try:
            return fn()
        except Exception as e:
            msg = str(e)
            if 'ip超限' in msg or 'limit' in msg.lower() or '频率' in msg or '并发' in msg:
                time.sleep(4 + i*4)
                continue
            raise e
    return None

def save(kind, code, df):
    d = os.path.join(DATA, kind)
    os.makedirs(d, exist_ok=True)
    df.to_csv(os.path.join(d, code.replace('.','_') + '.csv'), index=False)

def _call_kind(pro, k, c):
    if k == 'moneyflow':
        return pro.moneyflow(ts_code=c, start_date=SD, end_date=ED)
    if k == 'margin':
        return pro.margin_detail(ts_code=c, start_date=SD, end_date=ED)
    if k == 'income':
        return pro.income(ts_code=c, start_date='20181230', end_date=ED,
                          fields='ts_code,ann_date,f_ann_date,end_date,report_type,n_income,total_revenue')
    if k == 'daily_basic':
        return pro.daily_basic(ts_code=c, start_date=SD, end_date=ED,
                               fields='ts_code,trade_date,dv_ratio,pe_ttm,close,total_mv,circ_mv')
    return None

def worker_run(wid, tasks, mydone_file):
    pro = init_tushare()
    t0 = time.time(); cnt = 0
    for k, c in tasks:
        key = f'{k}:{c}'
        try:
            df = call(lambda k=k, c=c: _call_kind(pro, k, c))
            if df is not None and len(df):
                save(k, c, df)
        except Exception as e:
            print(f'[w{wid}] ERROR {key}: {e}', flush=True)
        # 记录到本worker断点文件
        done = {}
        if os.path.exists(mydone_file):
            with open(mydone_file) as f:
                done = json.load(f)
        done[key] = True
        with open(mydone_file, 'w') as f:
            json.dump(done, f)
        cnt += 1
        if cnt % 20 == 0:
            el = time.time() - t0
            print(f'[w{wid}] +{cnt} ({el/max(cnt,1):.1f}s/task)', flush=True)
        time.sleep(0.4)
    print(f'[w{wid}] finished {len(tasks)} tasks', flush=True)

if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--worker', type=int, required=True)
    p.add_argument('--nw', type=int, default=3)
    args = p.parse_args()
    KINDS = ['moneyflow', 'margin', 'income', 'daily_basic']
    done0 = load_main_done()
    pending = [(k, c) for k in KINDS for c in codes if not done0.get(f'{k}:{c}')]
    chunk = [t for i, t in enumerate(pending) if i % args.nw == args.worker]
    mydone = os.path.join(DATA, f'fetch_done_{args.worker}.json')
    print(f'[w{args.worker}] pending {len(pending)}, my {len(chunk)}', flush=True)
    worker_run(args.worker, chunk, mydone)