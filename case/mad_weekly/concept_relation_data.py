# -*- coding: utf-8 -*-
"""概念-个股关系网络数据：前20概念 + Top50样本股 + 归属边 + 概念间共享成分关联"""
import csv, json, collections
import numpy as np

def rd(f):
    return list(csv.DictReader(open(f, encoding='utf-8-sig')))

HERE = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5e6ceeb364ffeNZ3DY2SCHDLO20'

# ---------- 1. 前20概念（两个口径） ----------
tm = rd('tag_metrics.csv')
for r in tm:
    r['m20'] = float(r['指数MAD20']); r['ytd'] = float(r['指数今年涨幅']) if r['指数今年涨幅'] else None
top20_mad = sorted(tm, key=lambda r: -r['m20'])[:20]
top20_ytd = sorted([r for r in tm if r['ytd'] is not None], key=lambda r: -r['ytd'])[:20]
with open('concept_top20_mad20.csv','w',newline='',encoding='utf-8-sig') as f:
    w=csv.writer(f); w.writerow(['rank','概念','概念代码','指数MAD20','Top50成员数'])
    for i,r in enumerate(top20_mad,1): w.writerow([i,r['标签'],r['概念代码'],round(r['m20'],6),r['Top50成员数']])
with open('concept_top20_ytd.csv','w',newline='',encoding='utf-8-sig') as f:
    w=csv.writer(f); w.writerow(['rank','概念','概念代码','指数今年涨幅','Top50成员数'])
    for i,r in enumerate(top20_ytd,1): w.writerow([i,r['标签'],r['概念代码'],round(r['ytd'],6),r['Top50成员数']])

# ---------- 2. 个股节点：mad20 前50 + 今年涨幅 ----------
top50 = rd('top50_mad20_rank.csv')
d0 = np.load('cache_ma/D_20251231.npz', allow_pickle=True)
d1 = np.load('cache_ma/D_20261030.npz', allow_pickle=True)
base = {c: cl*aj for c, cl, aj in zip(d0['codes'], d0['close'], d0['adj'])}
last = {c: cl*aj for c, cl, aj in zip(d1['codes'], d1['close'], d1['adj'])}
for s in top50:
    c = s['ts_code']
    s['mad20'] = float(s['mad20']); s['mad'] = float(s['mad']); s['close']=float(s['close'])
    if c in base and c in last and base[c] > 0:
        s['ytd'] = last[c]/base[c] - 1.0
    else:
        s['ytd'] = None
miss = [s['ts_code'] for s in top50 if s['ytd'] is None]
print('Top50 只数', len(top50), '无年初基准', len(miss), miss)

# ---------- 3. 归属边：概念-个股 ----------
mem = rd('concept_members.csv')
code2stocks = collections.defaultdict(set)
name2code = {}
for r in mem:
    code2stocks[r['概念名称']].add(r['股票代码'])
    name2code.setdefault(r['概念名称'], r['概念代码'])
print('全市场概念数', len(code2stocks))
top50codes = [s['ts_code'] for s in top50]
stk_names = {s['ts_code']: s['name'] for s in top50}

def edges(concepts):
    rows = []
    for r in concepts:
        nm = r['标签']
        for c in sorted(set(code2stocks.get(nm,set())) & set(top50codes)):
            rows.append({'概念': nm, '概念代码': r['概念代码'], '股票代码': c, '股票名称': stk_names[c]})
    return rows

e_mad = edges(top20_mad); e_ytd = edges(top20_ytd)
print('MAD20版 归属边', len(e_mad), '涉及个股', len({r['股票代码'] for r in e_mad}))
print('YTD版 归属边', len(e_ytd), '涉及个股', len({r['股票代码'] for r in e_ytd}))

# ---------- 4. 概念间共享成分（Jaccard，全市场成分） ----------
def cc_edges(concepts):
    rows=[]
    for i in range(len(concepts)):
        for j in range(i+1, len(concepts)):
            a,b = concepts[i], concepts[j]
            sa, sb = code2stocks.get(a['标签'],set()), code2stocks.get(b['标签'],set())
            inter = len(sa & sb); uni = len(sa | sb)
            if inter: rows.append({'概念A':a['标签'],'概念B':b['标签'],'共享股数':inter,'Jaccard':round(inter/uni,4),'A家数':len(sa),'B家数':len(sb)})
    return sorted(rows, key=lambda r: -r['Jaccard'])

cc_mad = cc_edges(top20_mad); cc_ytd = cc_edges(top20_ytd)
print('概念对 数量 MAD版', len(cc_mad), ' YTD版', len(cc_ytd))
print('MAD版 Jaccard 分布 前10:', [(r['概念A'],r['概念B'],r['Jaccard'],r['共享股数']) for r in cc_mad[:10]])

json.dump({'top20_mad':[{k:r[k] for k in ('标签','概念代码','m20','Top50成员数','指数MAD20','指数今年涨幅')} for r in top20_mad],
           'top20_ytd':[{k:r[k] for k in ('标签','概念代码','ytd','Top50成员数','指数MAD20','指数今年涨幅')} for r in top20_ytd],
           'top50':[{k:s[k] for k in ('rank','ts_code','name','industry','mad20','mad','ytd','close_over_ma40_pct')} for s in top50],
           'e_mad':e_mad,'e_ytd':e_ytd,'cc_mad':cc_mad,'cc_ytd':cc_ytd},
          open('concept_relation_data.json','w',encoding='utf-8'), ensure_ascii=False, indent=1, default=float)
print('落盘 concept_top20_mad20.csv / concept_top20_ytd.csv / concept_relation_data.json')
