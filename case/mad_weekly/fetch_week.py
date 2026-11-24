# -*- coding: utf-8 -*-
"""最近一周（2026-10-23 → 2026-10-30，5 个交易日）个股与同花顺行业/概念指数行情"""
import csv, sys
import numpy as np
sys.path.insert(0,'/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5e9bb55354ffe9Rg1AoYf1k3lwu')
import ts_http

d0 = np.load('cache_ma/D_20261023.npz', allow_pickle=True)
d1 = np.load('cache_ma/D_20261030.npz', allow_pickle=True)
b = {c: cl*aj for c, cl, aj in zip(d0['codes'], d0['close'], d0['adj'])}
l = {c: cl*aj for c, cl, aj in zip(d1['codes'], d1['close'], d1['adj'])}
univ = list(csv.DictReader(open('universe_all.csv', encoding='utf-8-sig')))
rows = []
for r in univ:
    c = r['ts_code']
    ok = c in b and c in l and b[c] > 0 and l[c] > 0
    rows.append({'ts_code': c, 'name': r['name'], 'industry': r['industry'], 'status': r['status'],
                 'close_1023': b.get(c), 'close_1030': l.get(c),
                 'ret_w': (l[c]/b[c]-1) if ok else None,
                 'mad': r['mad'], 'mad20': (float(r['ma20'])/float(r['ma40'])-1) if r['ma20'] and r['ma40'] else None})
with open('stock_week_20261023_20261030.csv','w',newline='',encoding='utf-8-sig') as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
have = [r for r in rows if r['ret_w'] is not None]
print('个股：全市场 %d 只，可算周涨幅 %d 只（缺数据 %d）' % (len(rows), len(have), len(rows)-len(have)), flush=True)
print('   周涨幅 中位 %.2f%% 均值 %.2f%% 上涨 %d 下跌 %d' % (
    float(np.median([r['ret_w'] for r in have]))*100, float(np.mean([r['ret_w'] for r in have]))*100,
    sum(1 for r in have if r['ret_w']>0), sum(1 for r in have if r['ret_w']<0)), flush=True)

idx = list(csv.DictReader(open('ths_index_all.csv', encoding='utf-8-sig')))
sel = [r for r in idx if r['ts_code'][:3] in ('881','884','885','886')]
print('指数：行业(881/884) %d 个，概念(885/886) %d 个' % (
    sum(1 for r in sel if r['ts_code'][:3] in ('881','884')), sum(1 for r in sel if r['ts_code'][:3] in ('885','886'))), flush=True)
params = [{'ts_code': r['ts_code'], 'start_date': '20261020', 'end_date': '20261030'} for r in sel]
res = ts_http.fetch_many('ths_daily', params, workers=8, tag='ths_daily')
out, err = [], []
for r, df in zip(sel, res):
    if isinstance(df, Exception) or df is None or len(df) == 0:
        err.append((r['ts_code'], r['name'], str(df)[:60])); continue
    df = df.sort_values('trade_date')
    m = {str(t): float(cc) for t, cc in zip(df['trade_date'], df['close'])}
    if '20261023' not in m or '20261030' not in m:
        err.append((r['ts_code'], r['name'], '缺 1023/1030')); continue
    grp = '行业' if r['ts_code'][:3] in ('881','884') else '概念'
    out.append({'ts_code': r['ts_code'], 'name': r['name'], 'grp': grp, 'count': r['count'], 'type': r['type'],
                'close_1023': m['20261023'], 'close_1030': m['20261030'],
                'ret_w': m['20261030']/m['20261023']-1, 'bars': len(df)})
with open('index_week_20261023_20261030.csv','w',newline='',encoding='utf-8-sig') as f:
    w = csv.DictWriter(f, fieldnames=list(out[0].keys())); w.writeheader(); w.writerows(out)
print('指数取数：成功 %d，失败 %d' % (len(out), len(err)), err[:5], flush=True)
for g in ('行业','概念'):
    s = sorted([r for r in out if r['grp']==g], key=lambda r: -r['ret_w'])
    print('%s 榜 前8：' % g, [(r['name'], round(r['ret_w']*100,2), int(float(r['count']))) for r in s[:8]], flush=True)
    print('%s 榜 后3：' % g, [(r['name'], round(r['ret_w']*100,2)) for r in s[-3:]], flush=True)
print('DONE', flush=True)
