# -*- coding: utf-8 -*-
"""关系网络图数据与数值核验"""
import csv, json, glob
import numpy as np
D = json.load(open('concept_relation_data.json', encoding='utf-8'))
def rd(f): return list(csv.DictReader(open(f, encoding='utf-8-sig')))

top50 = rd('top50_mad20_rank.csv')
by_code = {r['ts_code']: r for r in top50}
print('Top50 排名范围', min(int(r['rank']) for r in top50), max(int(r['rank']) for r in top50), '| 唯一股票', len(by_code))

# 1) 名词一致性：json 中 top50 排名/名称 与 CSV 是否一致
bad = [(s['ts_code'], s['rank'], s['name'], by_code[s['ts_code']]['rank'], by_code[s['ts_code']]['name'])
       for s in D['top50'] if str(s['rank']) != by_code[s['ts_code']]['rank'] or s['name'] != by_code[s['ts_code']]['name']]
print('Top50 排名/名称不一致条数:', len(bad), bad[:3])

# 2) 今年涨幅：npz 复权价比值 vs 逐日 pct 连乘（两条独立路径）
files = sorted(glob.glob('cache_ma/D_*.npz'))
d0 = np.load('cache_ma/D_20251231.npz', allow_pickle=True); d1 = np.load('cache_ma/D_20261030.npz', allow_pickle=True)
base = {c: cl*aj for c, cl, aj in zip(d0['codes'], d0['close'], d0['adj'])}
last = {c: cl*aj for c, cl, aj in zip(d1['codes'], d1['close'], d1['adj'])}
cum = {}
for f in files[files.index('cache_ma/D_20251231.npz')+1:]:
    d = np.load(f, allow_pickle=True)
    if 'pct' not in d.files: continue
    for c, p in zip(d['codes'], d['pct']):
        cum[c] = cum.get(c, 1.0) * (1 + p/100.0)
diff = []
for s in D['top50']:
    c = s['ts_code']
    if c in cum and c in base and base[c] > 0:
        alt = cum[c] - 1
        diff.append(abs(alt - s['ytd']))
print('今年涨幅 两条路径最大偏差: %.6f  中位 %.6f  覆盖 %d/50' % (
    max(diff), sorted(diff)[len(diff)//2], len(diff)))

# 3) 前20概念与 tag_metrics 一致性
tm = rd('tag_metrics.csv')
tm_m = {r['标签']: float(r['指数MAD20']) for r in tm}
tm_y = {r['标签']: (float(r['指数今年涨幅']) if r['指数今年涨幅'] else None) for r in tm}
for key, mm in (('top20_mad','m20'), ('top20_ytd','ytd')):
    errs = []
    for c in D[key]:
        exp = tm_m[c['标签']] if key=='top20_mad' else tm_y[c['标签']]
        if abs(float(c[mm]) - exp) > 1e-9: errs.append((c['标签'], c[mm], exp))
    print(key, '概念数', len(D[key]), '值与 tag_metrics 不一致', len(errs),
          '| 排名区间 %.2f%%~%.2f%%' % (min(float(c[mm]) for c in D[key])*100, max(float(c[mm]) for c in D[key])*100))
# 成员数一致性
mem = rd('concept_members.csv'); import collections
cnt = collections.Counter(r['概念名称'] for r in mem if r['股票代码'] in by_code)
for key in ('top20_mad','top20_ytd'):
    bad = [(c['标签'], c['Top50成员数'], cnt[c['标签']]) for c in D[key] if int(c['Top50成员数']) != cnt[c['标签']]]
    print(key, 'Top50成员数不一致:', len(bad), bad[:3])

# 4) 边的一致性
for key, ek in (('top20_mad','e_mad'), ('top20_ytd','e_ytd')):
    names = {c['标签'] for c in D[key]}; codes = set()
    bad = 0
    for e in D[ek]:
        if e['概念'] not in names: bad += 1
        if e['股票代码'] not in by_code: bad += 1
        if e['股票名称'] != by_code[e['股票代码']]['name']: bad += 1
        if e['股票代码'] not in set(r['股票代码'] for r in mem if r['概念名称']==e['概念']): bad += 1
        codes.add(e['股票代码'])
    print(ek, '边', len(D[ek]), '| 涉及个股', len(codes), '| 归属关系错误', bad,
          '| 每股平均归属概念 %.2f' % (len(D[ek])/len(codes)))
