# -*- coding: utf-8 -*-
"""东财个股资金流: 按股票一次全历史, 多线程, 断点续传"""
import socket, os, sys, time, json
_orig = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **kw: [r for r in _orig(*a, **kw) if r[0] == socket.AF_INET]
socket.setdefaulttimeout(30)
import warnings; warnings.filterwarnings('ignore')
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
import akshare as ak

BASE = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5fd6b2558bffeYG74Ts057P2dLR/strategy_repro'
DATA = os.path.join(BASE, 'data')
codes = pd.read_csv(os.path.join(DATA, 'constituent_codes.csv'))['code'].tolist()
outdir = os.path.join(DATA, 'em_fundflow')
os.makedirs(outdir, exist_ok=True)
done_file = os.path.join(DATA, 'em_ff_done.json')
done = json.load(open(done_file)) if os.path.exists(done_file) else {}
lock = __import__('threading').Lock()

def market_of(code):
    return 'sh' if code.endswith('.SH') else ('sz' if code.endswith('.SZ') else 'bj')

def work(code):
    key = code
    with lock:
        if done.get(key): return (code, 'skip', None)
    sym = code.split('.')[0]
    try:
        df = ak.stock_individual_fund_flow(stock=sym, market=market_of(code))
        if df is None or len(df) == 0:
            with lock: done[key] = 'empty'
            return (code, 'empty', None)
        df['code'] = code
        df.to_csv(os.path.join(outdir, code.replace('.', '_') + '.csv'), index=False)
        with lock:
            done[key] = True
        return (code, 'ok', len(df))
    except Exception as e:
        if '频繁' in str(e) or '访问' in str(e) or 'token' in str(e).lower():
            return (code, 'rate', str(e)[:80])
        return (code, 'err', str(e)[:120])

todo = [c for c in codes if not done.get(c)]
print(f'total={len(codes)} todo={len(todo)}', flush=True)
with ThreadPoolExecutor(max_workers=4) as ex:
    futs = {ex.submit(work, c): c for c in todo}
    n_ok = n_err = n_rate = 0
    for i, f in enumerate(as_completed(futs)):
        code, st, info = f.result()
        if st == 'ok': n_ok += 1
        elif st == 'rate': n_rate += 1
        elif st == 'err': n_err += 1
        with lock:
            json.dump(done, open(done_file, 'w'))
        if i % 50 == 0:
            print(f'{time.strftime("%H:%M:%S")} {i+1}/{len(todo)} ok={n_ok} rate={n_rate} err={n_err}', flush=True)
        if st == 'rate':
            time.sleep(5)
print(f'FINISH ok={n_ok} rate={n_rate} err={n_err}', flush=True)
with open(done_file, 'w') as f: json.dump(done, f)
