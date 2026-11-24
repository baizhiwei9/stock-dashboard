# -*- coding: utf-8 -*-
"""RRG 数据抓取 (幂等, 预算制, 断点续传)
数据需求:
  1. 申万一级行业指数日线 (index_daily by ts_code)      -> data_rrg/industry/*.csv
  2. 行业成分股列表 (index_member_all)                  -> data_rrg/constituents.csv
  3. 全市场日线 close + 复权因子 + 自由流通市值 (按交易日批量) -> data_rrg/day/{date}.csv
用法: python rrg_fetch.py [预算秒数=1200]
"""
import socket, time, os, sys, json, glob
import pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pylib'))
_orig = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **kw: [r for r in _orig(*a, **kw) if r[0] == socket.AF_INET]
socket.setdefaulttimeout(25)
import warnings; warnings.filterwarnings('ignore')

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
DATA = os.path.join(BASE, 'data_rrg')
os.makedirs(os.path.join(DATA, 'industry'), exist_ok=True)
os.makedirs(os.path.join(DATA, 'day'), exist_ok=True)
DONE = os.path.join(DATA, 'fetch_done_rrg.json')

BUDGET = float(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].replace('.', '').isdigit() else 1200.0
t_start = time.time()

from ts_fast import init_tushare  # Session复用直连: 固定出口IP (旧代理通道仅~10/min)
pro = init_tushare()

SD, ED = '20200101', '20260822'   # 扩散需220日回看+MA20, 2022起信号 -> 2020起数据


def call(fn, retries=12):
    last = None
    for i in range(retries):
        try:
            return fn()
        except Exception as e:
            last = e
            msg = str(e)
            if 'ip超限' in msg or '抱歉' in msg:
                time.sleep(6)
            else:
                time.sleep(2.5)
    raise RuntimeError(f'retry exhausted: {last}')


def budget_ok():
    return time.time() - t_start < BUDGET


def save_done(d):
    with open(DONE, 'w') as f:
        json.dump(d, f)


def load_done():
    if os.path.exists(DONE):
        with open(DONE) as f:
            return json.load(f)
    return {}


def log(msg):
    print(msg, flush=True)


# ---------- 1. 行业指数日线 ----------
def fetch_industry(done):
    if done.get('industries') == 'OK':
        return
    # SW2021 一级行业
    cls = call(lambda: pro.index_classify(level='L1', src='SW2021'))
    # 剔除"综合"类(口径对齐: 中信剔综合/综合金融; 申万剔综合)
    inds = cls[~cls['industry_name'].isin(['综合'])].reset_index(drop=True)
    log(f'industry count: {len(inds)}')
    for _, r in inds.iterrows():
        code, name = r['index_code'], r['industry_name']
        fp = os.path.join(DATA, 'industry', f'{name}_{code}.csv')
        if os.path.exists(fp):
            continue
        if not budget_ok():
            save_done(done); log('BUDGET'); sys.exit(0)
        try:
            df = call(lambda c=code: pro.index_daily(ts_code=c, start_date=SD, end_date=ED))
            if df is None or len(df) == 0:
                log(f'index {name} empty')
                continue
            df = df[['trade_date', 'close']].sort_values('trade_date')
            # 存入 aos 兼容格式: trade_date, close
            df.to_csv(fp, index=False)
            log(f'idx {name} {len(df)} rows')
        except Exception as e:
            log(f'index {name} ERR {str(e)[:60]}')
        time.sleep(0.8)  # ts_fast 已节流
    # 存行业元信息
    inds.to_csv(os.path.join(DATA, 'industry_list.csv'), index=False)
    done['industries'] = 'OK'
    save_done(done)


# ---------- 2. 成分股列表 ----------
def fetch_constituents(done):
    if done.get('constituents') == 'OK':
        return
    cls = call(lambda: pro.index_classify(level='L1', src='SW2021'))
    cls = cls[~cls['industry_name'].isin(['综合'])].reset_index(drop=True)
    rows = []
    for _, r in cls.iterrows():
        code, name = r['index_code'], r['industry_name']
        try:
            df = call(lambda c=code: pro.index_member(index_code=c))
            # 当前成分: is_new=Y 或 out_date 为空
            cur = df[(df['is_new'] == 'Y') | (df['out_date'].isna() | (df['out_date'] == ''))]
            cur = cur[['con_code']].drop_duplicates()
            cur['industry'] = name
            rows.append(cur)
            log(f'const {name} {len(cur)}')
        except Exception as e:
            log(f'const {name} ERR {str(e)[:60]}')
        time.sleep(0.8)  # ts_fast 已节流
    if rows:
        allc = pd.concat(rows)
        allc.to_csv(os.path.join(DATA, 'constituents.csv'), index=False)
    done['constituents'] = 'OK'
    save_done(done)


# ---------- 3. 全市场按日行情 ----------
def fetch_daily(done):
    if done.get('daily') == 'OK':
        return
    # 交易日历
    cal = call(lambda: pro.trade_cal(exchange='SSE', start_date=SD, end_date=ED, is_open='1'))
    dates = sorted(cal['cal_date'].tolist())
    todo = [d for d in dates if not os.path.exists(os.path.join(DATA, 'day', f'{d}.csv'))]
    log(f'daily todo: {len(todo)}/{len(dates)}')
    for d in todo:
        if not budget_ok():
            save_done(done); log('BUDGET'); sys.exit(0)
        try:
            db = call(lambda dd=d: pro.daily_basic(trade_date=dd, fields='ts_code,close,circ_mv,trade_date'))
            af = call(lambda dd=d: pro.adj_factor(trade_date=dd))
            m = db[['ts_code', 'close', 'circ_mv']].merge(af, on='ts_code')
            m['adj_close'] = m['close'] * m['adj_factor']  # 后复权价(用于220日涨跌判断, 避免除权失真)
            m[['ts_code', 'adj_close', 'circ_mv']].to_csv(os.path.join(DATA, 'day', f'{d}.csv'), index=False)
        except Exception as e:
            log(f'day {d} ERR {str(e)[:60]}')
        time.sleep(0.8)  # ts_fast 已节流
    done['daily'] = 'OK'
    save_done(done)


def main():
    done = load_done()
    fetch_industry(done)
    if not budget_ok():
        save_done(done); log('BUDGET'); sys.exit(0)
    fetch_constituents(done)
    if not budget_ok():
        save_done(done); log('BUDGET'); sys.exit(0)
    fetch_daily(done)

    n_ind = len(glob.glob(os.path.join(DATA, 'industry', '*.csv')))
    n_day = len(glob.glob(os.path.join(DATA, 'day', '*.csv')))
    log(f'RRG_DONE industry={n_ind} day_files={n_day}')


if __name__ == '__main__':
    main()