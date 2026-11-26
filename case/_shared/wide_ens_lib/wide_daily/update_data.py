# -*- coding: utf-8 -*-
"""增量更新五指数日线 + 利差因子（纯脚本，0 token）

只补最近 N 天，不重抓全历史，避免 tushare 代理 token 的 ip 限流。
"""
import os, sys, socket, argparse, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_orig = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _orig(*a, **k) if r[0] == socket.AF_INET]

import numpy as np
import pandas as pd

import core as C

ROOT = C.ROOT
sys.path.insert(0, os.path.join(ROOT, 'bin'))

IDX_CODE = {
    'idx_hs300.csv': '000300.SH',
    'idx_zz500.csv': '000905.SH',
    'idx_zz1000.csv': '000852.SH',
    'idx_cyb.csv': '399006.SZ',
    'idx_kc50.csv': '000688.SH',
}


def ts_call(*a, **k):
    from tushare_init import ts_call as _f
    return _f(*a, **k)


def update_index(fn, code, days=30):
    p = os.path.join(C.RAW, fn)
    old = None
    if os.path.exists(p) and os.path.getsize(p) > 500:
        old = pd.read_csv(p, dtype={'trade_date': str})
        last = pd.to_datetime(old['trade_date'], format='%Y%m%d', errors='coerce').max()
        start = (last - pd.Timedelta(days=3)).strftime('%Y%m%d')
    else:
        start = '20130101'
    end = datetime.date.today().strftime('%Y%m%d')
    try:
        new = ts_call('index_daily', ts_code=code, start_date=start, end_date=end)
    except Exception as e:
        print('  [%s] 拉取失败 %s' % (fn, e))
        return False
    if new is None or not len(new):
        print('  [%s] 无新数据' % fn)
        return False
    if old is not None:
        out = pd.concat([old, new]).drop_duplicates(subset=['trade_date'], keep='last')
        out = out.sort_values('trade_date', ascending=False)
    else:
        out = new.sort_values('trade_date', ascending=False)
    out.to_csv(p, index=False)
    print('  [%s] %s 共 %d 行，最新 %s' % (fn, '更新' if old is not None else '新建',
                                          len(out), out['trade_date'].max()))
    return True


def update_macro():
    """重算利差因子面板（复用 research 里的 build_spread_factors）"""
    script = os.path.join(ROOT, 'research', 'weekly_forecast', 'build_spread_factors.py')
    if not os.path.exists(script):
        print('  [macro] build_spread_factors.py 不存在，跳过')
        return
    import subprocess
    try:
        r = subprocess.run([sys.executable, script], capture_output=True, text=True, timeout=600)
        tail = (r.stdout or r.stderr).strip().splitlines()
        print('  [macro] %s' % (' / '.join(tail[-2:]) if tail else 'done'))
    except Exception as e:
        print('  [macro] 失败 %s' % e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=30)
    ap.add_argument('--skip-macro', action='store_true')
    args = ap.parse_args()

    print('[%s] wide_daily 数据更新' % datetime.datetime.now().strftime('%F %T'))
    ok = 0
    for fn, code in IDX_CODE.items():
        if update_index(fn, code, args.days):
            ok += 1
    print('  指数更新 %d/%d' % (ok, len(IDX_CODE)))
    if not args.skip_macro:
        update_macro()
    return 0


if __name__ == '__main__':
    sys.exit(main())
