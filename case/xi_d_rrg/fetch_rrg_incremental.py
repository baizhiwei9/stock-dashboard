#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RRG 数据增量更新: 把行业指数 / day 截面 / diffusion 补到最新交易日
用法: python fetch_rrg_incremental.py [YYYYMMDD 目标日期, 默认最新交易日]
幂等: 已存在且到目标日的文件跳过; 只补缺口
"""
import socket, time, os, sys, glob, json
import pandas as pd
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pylib'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_orig = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **kw: [r for r in _orig(*a, **kw) if r[0] == socket.AF_INET]
socket.setdefaulttimeout(25)
import warnings; warnings.filterwarnings('ignore')
from ts_fast import init_tushare

# ---- DataHub（三级体系）：行业指数、复权因子与截面数据的统一入口 ----
HUB_ROOT = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/projects/datahub-multi-tier'
HUB_DEPS = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/py_deps'
for _p in (HUB_DEPS, HUB_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, 'data_rrg')
pro = init_tushare()

def call(fn, retries=10):
    last = None
    for i in range(retries):
        try:
            return fn()
        except Exception as e:
            last = e
            time.sleep(4 if 'ip超限' in str(e) else 2.5)
    raise RuntimeError(f'retry exhausted: {last}')

def last_trade_date():
    """最近交易日: 优先 DataHub 交易日历, 失败回退 Tushare"""
    import datetime
    d = datetime.date.today()
    try:
        from data_hub import edb
        cal = edb.get_dataset('CN.MARKET.CAL.TRADE',
                              start_date=(d - datetime.timedelta(days=14)).strftime('%Y%m%d'),
                              end_date=d.strftime('%Y%m%d'))
        ds = sorted(str(x) for x in cal['cal_date'].astype(str)) if cal is not None and len(cal) else []
        ds = [x for x in ds if x <= d.strftime('%Y%m%d')]
        if ds:
            return ds[-1]
    except Exception as e:
        print(f'  warn: datahub cal failed {str(e)[:80]}', flush=True)
    for back in range(14):
        dd = (d - datetime.timedelta(days=back)).strftime('%Y%m%d')
        cal = call(lambda c=dd: pro.trade_cal(exchange='SSE', start_date=dd, end_date=dd, is_open='1'))
        if len(cal) > 0 and int(cal.iloc[0]['is_open']) == 1:
            return dd
    raise RuntimeError('no trade date found')

# ---------- 1. 行业指数增量 ----------
def update_industry(target):
    """行业指数增量：DataHub 股票模块 fetch_sw_index_daily（申万行业指数日线）。"""
    from data_hub.collectors import fetch_sw_index_daily
    files = sorted(glob.glob(os.path.join(DATA, 'industry', '*.csv')))
    n_upd = 0
    for fp in files:
        df = pd.read_csv(fp, dtype={'trade_date': str})
        last_d = df['trade_date'].max()
        if last_d >= target:
            continue
        name_code = os.path.basename(fp)[:-4]
        code = name_code.split('_')[-1]          # 801170.SI
        try:
            hub = fetch_sw_index_daily(code, start_date=str(last_d), end_date=str(target))
        except Exception as e:
            print(f'  idx {name_code} ERR {str(e)[:60]}', flush=True)
            continue
        if hub is None or len(hub) == 0:
            print(f'  idx {name_code}: DataHub 无新数据', flush=True)
            continue
        nd = hub[['trade_date', 'close']].copy()
        nd['trade_date'] = nd['trade_date'].astype(str).str.replace('-', '')
        nd = nd[nd['trade_date'] > str(last_d)].sort_values('trade_date')
        if len(nd) == 0:
            continue
        pd.concat([df, nd]).drop_duplicates('trade_date').sort_values('trade_date').to_csv(fp, index=False)
        n_upd += 1
        print(f'  idx {name_code}: +{len(nd)} rows -> {nd["trade_date"].max()}', flush=True)
    print(f'industry updated: {n_upd} files (datahub)', flush=True)


# ---------- 2. day 截面增量 ----------
def _sina_quotes(codes, batch=400):
    """新浪批量行情: 返回 {symbol: (prev_close, current, quote_date)}"""
    import requests
    H = {'Referer': 'https://finance.sina.com.cn', 'User-Agent': 'Mozilla/5.0'}

    def _sym(c):
        code, ex = c.split('.')
        return {'SH': 'sh', 'SZ': 'sz'}.get(ex, 'bj') + code

    out = {}
    for i in range(0, len(codes), batch):
        syms = [_sym(c) for c in codes[i:i + batch]]
        try:
            r = requests.get('https://hq.sinajs.cn/list=' + ','.join(syms), headers=H, timeout=25)
            r.encoding = 'gbk'
        except Exception as e:
            print(f'  sina batch ERR {str(e)[:50]}', flush=True)
            continue
        for line in r.text.split('\n'):
            if '="' not in line:
                continue
            try:
                sym = line.split('="')[0].split('hq_str_')[-1].strip()
                p = line.split('"')[1].split(',')
                if len(p) < 32:
                    continue
                out[sym] = (float(p[2]), float(p[3]), p[30])
            except Exception:
                continue
    return out


def _day_from_sina(d):
    """DataHub 截面快照未就绪时的降级: 新浪行情 + 前一日复权收盘链式推当日
    adj_close_t = adj_close_{t-1} * current_t / prev_close_t  (前收已含除权调整)
    circ_mv_t   = circ_mv_{t-1}   * current_t / prev_close_t  (股本视为不变)
    """
    prev_files = sorted(glob.glob(os.path.join(DATA, 'day', '*.csv')))
    prev_files = [f for f in prev_files if os.path.basename(f)[:-4] < str(d)]
    if not prev_files:
        return 0
    prev = pd.read_csv(prev_files[-1], dtype={'ts_code': str})
    q = _sina_quotes(prev['ts_code'].tolist())
    want_date = f'{d[:4]}-{d[4:6]}-{d[6:]}'
    rows = []
    for _, r_ in prev.iterrows():
        code = r_['ts_code']
        code_, ex = code.split('.')
        sym = {'SH': 'sh', 'SZ': 'sz'}.get(ex, 'bj') + code_
        hit = q.get(sym)
        if not hit:
            continue
        pc, cur, qd = hit
        if qd != want_date or pc <= 0:
            continue
        ratio = cur / pc if cur > 0 else 1.0
        rows.append((code, float(r_['adj_close']) * ratio, float(r_['circ_mv']) * ratio))
    if not rows:
        return 0
    out = pd.DataFrame(rows, columns=['ts_code', 'adj_close', 'circ_mv'])
    out.to_csv(os.path.join(DATA, 'day', f'{d}.csv'), index=False)
    return len(out)


def update_day(target):
    """day 截面增量：DataHub 数据集 CN.MARKET.BASIC.DAILY + CN.MARKET.ADJ.FACTOR；
    快照未就绪(返回空/通道故障)时降级为新浪行情链式推算。"""
    from data_hub import edb
    dates = sorted(d for d in os.listdir(os.path.join(DATA, 'day')) if d.endswith('.csv'))
    cal = edb.get_dataset('CN.MARKET.CAL.TRADE', start_date='20200101', end_date=str(target))
    all_dates = sorted(str(x) for x in cal['cal_date'].astype(str)) if cal is not None and len(cal) else []
    have = {f[:-4] for f in dates}
    todo = [d for d in all_dates if d not in have]
    print(f'day todo: {len(todo)}/{len(all_dates)}, latest target {target}', flush=True)
    for d in todo:
        try:
            db = edb.get_dataset('CN.MARKET.BASIC.DAILY', trade_date=d)
            try:
                af = edb.get_dataset('CN.MARKET.ADJ.FACTOR', trade_date=d)
            except Exception:
                # DataHub 目录重组后未登记 ADJ.FACTOR，回退 TuShare 原始复权因子
                af = call(lambda d=d: pro.adj_factor(trade_date=d))
            if db is None or len(db) == 0 or af is None or len(af) == 0:
                n = _day_from_sina(d)
                print(f'  day {d}: DataHub 截面未就绪, 新浪降级 {n} stocks', flush=True)
                continue
            m = db[['ts_code', 'close', 'circ_mv']].merge(af[['ts_code', 'adj_factor']], on='ts_code')
            m['adj_close'] = pd.to_numeric(m['close'], errors='coerce') * pd.to_numeric(m['adj_factor'], errors='coerce')
            m[['ts_code', 'adj_close', 'circ_mv']].to_csv(os.path.join(DATA, 'day', f'{d}.csv'), index=False)
            print(f'  day {d}: {len(m)} stocks', flush=True)
        except Exception as e:
            n = _day_from_sina(d)
            print(f'  day {d}: 通道异常({str(e)[:40]}), 新浪降级 {n} stocks', flush=True)


# ---------- 3. diffusion 全量重算 ----------
def rebuild_diffusion(target, window=300):
    """day 数据已增量补全, 全量重算最稳"""
    import rrg_data
    import importlib; importlib.reload(rrg_data)
    df = rrg_data.build_diffusion()
    print(f'diffusion rebuilt: {df.shape}, last date {df.index[-1].date()}', flush=True)

if __name__ == '__main__':
    target = sys.argv[1] if len(sys.argv) > 1 and len(sys.argv[1]) == 8 else last_trade_date()
    print(f'=== RRG incremental update to {target} ===', flush=True)
    update_industry(target)
    update_day(target)
    rebuild_diffusion(target)
    print('DONE', flush=True)