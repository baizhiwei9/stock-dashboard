# -*- coding: utf-8 -*-
"""final: 单进程串行, 间隔1.5s, 退避重试, 断点续传(main fetch_done.json)"""
import socket, time, os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pylib'))  # 工作区持久依赖
_orig = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **kw: [r for r in _orig(*a, **kw) if r[0] == socket.AF_INET]
socket.setdefaulttimeout(20)
import warnings; warnings.filterwarnings('ignore')
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
from ts_fast import init_tushare  # Session复用直连: 固定出口IP, 0.6s≈95/min (旧代理通道仅~10/min)
pro = init_tushare()

BASE = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5fd6b2558bffeYG74Ts057P2dLR/strategy_repro'
DATA = os.path.join(BASE, 'data')
SD, ED = '20191230', '20260822'
codes = pd.read_csv(os.path.join(DATA, 'constituent_codes.csv'))['code'].tolist()
done_file = os.path.join(DATA, 'fetch_done.json')

# 时间预算(秒): 到点保存进度退出, 供 scheduler 幂等调度
BUDGET = float(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].replace('.', '').isdigit() else float('inf')
t_start = time.time()

def call(fn, retries=15):
    for i in range(retries):
        try:
            return fn()
        except Exception as e:
            msg = str(e)
            if 'ip超限' in msg or 'limit' in msg.lower() or '频率' in msg or '并发' in msg:
                time.sleep(6 + i*4)
                continue
            raise e
    return None

def save(kind, code, df):
    d = os.path.join(DATA, kind)
    os.makedirs(d, exist_ok=True)
    df.to_csv(os.path.join(d, code.replace('.','_') + '.csv'), index=False)

def get(k, c):
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

while True:
    done = json.load(open(done_file)) if os.path.exists(done_file) else {}
    KINDS = ['moneyflow', 'margin', 'income', 'daily_basic']
    todo = [(k, c) for k in KINDS for c in codes if not done.get(f'{k}:{c}')]
    if not todo:
        print('ALL DONE', flush=True); break
    n0 = sum(1 for v in done.values() if v)
    print(f'pending {len(todo)} (done {n0})', flush=True)
    t0 = time.time(); cnt = 0
    for k, c in todo:
        if time.time() - t_start > BUDGET:
            with open(done_file, 'w') as f: json.dump(done, f)
            print(f'BUDGET_REACHED done_total={n0+cnt}', flush=True)
            sys.exit(2)  # 时间到, 未完成
        try:
            df = call(lambda k=k, c=c: get(k, c))
            if df is not None and len(df):
                save(k, c, df)
        except Exception as e:
            print(f'ERROR {k}:{c} {str(e)[:60]}', flush=True)
        done[f'{k}:{c}'] = True
        if cnt % 10 == 0:
            with open(done_file, 'w') as f: json.dump(done, f)
        cnt += 1
        if cnt % 30 == 0:
            el = time.time() - t0
            print(f'{time.strftime("%H:%M:%S")} +{cnt} ({el/max(cnt,1):.1f}s/task) total_done={n0+cnt}', flush=True)
        time.sleep(0.5)  # ts_fast 内部已 0.6s 节流, 此处仅兜底
    with open(done_file, 'w') as f: json.dump(done, f)
