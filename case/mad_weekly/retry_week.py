# -*- coding: utf-8 -*-
"""补取限流失败的同花顺指数 + 取主要宽基指数周涨跌幅"""
import csv, sys, time
sys.path.insert(0,'/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5e9bb55354ffe9Rg1AoYf1k3lwu')
import ts_http

rows=list(csv.DictReader(open('index_week_20261023_20261030.csv',encoding='utf-8-sig')))
got={r['ts_code'] for r in rows}
idx=list(csv.DictReader(open('ths_index_all.csv',encoding='utf-8-sig')))
sel=[r for r in idx if r['ts_code'][:3] in ('881','884','885','886')]
miss=[r for r in sel if r['ts_code'] not in got]
print('待补指数 %d 个' % len(miss), flush=True)
params=[{'ts_code':r['ts_code'],'start_date':'20261020','end_date':'20261030'} for r in miss]
res=ts_http.fetch_many('ths_daily', params, workers=2, tag='retry')
add, err = [], []
for r, df in zip(miss, res):
    if isinstance(df, Exception) or df is None or len(df)==0:
        err.append((r['ts_code'], r['name'], str(df)[:50])); continue
    df=df.sort_values('trade_date')
    m={str(t):float(cc) for t,cc in zip(df['trade_date'],df['close'])}
    if '20261023' not in m or '20261030' not in m:
        err.append((r['ts_code'], r['name'], '缺 1023/1030')); continue
    add.append({'ts_code':r['ts_code'],'name':r['name'],'grp':'行业' if r['ts_code'][:3] in ('881','884') else '概念',
                'count':r['count'],'type':r['type'],'close_1023':m['20261023'],'close_1030':m['20261030'],
                'ret_w':m['20261030']/m['20261023']-1,'bars':len(df)})
print('补取成功 %d，仍失败 %d' % (len(add), len(err)), err[:6], flush=True)
with open('index_week_20261023_20261030.csv','w',newline='',encoding='utf-8-sig') as f:
    w=csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows+add)
print('合计指数 %d 个（行业 %d，概念 %d）' % (
    len(rows)+len(add), sum(1 for r in rows+add if r['grp']=='行业'), sum(1 for r in rows+add if r['grp']=='概念')), flush=True)

# 宽基指数周涨跌幅（独立口径校验）
for code,nm in (('000001.SH','上证指数'),('000300.SH','沪深300'),('399006.SZ','创业板指'),('000688.SH','科创50'),('000905.SH','中证500')):
    time.sleep(0.6)
    df=ts_http.fetch_df('index_daily',{'ts_code':code,'start_date':'20261020','end_date':'20261030'}).sort_values('trade_date')
    m={str(t):float(c) for t,c in zip(df['trade_date'],df['close'])}
    print('%s %s: 1023 %.2f → 1030 %.2f | 周涨跌幅 %+.2f%%' % (nm, code, m.get('20261023',float('nan')), m.get('20261030',float('nan')),
          (m['20261030']/m['20261023']-1)*100 if '20261023' in m else float('nan')), flush=True)
print('DONE', flush=True)
