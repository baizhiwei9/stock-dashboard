# -*- coding: utf-8 -*-
"""落盘归属边/关联边CSV，并按共享成分把前20概念分簇（供绘图排布）"""
import csv, json, collections
D = json.load(open('concept_relation_data.json', encoding='utf-8'))

def wr(fn, rows, cols):
    with open(fn,'w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f, fieldnames=cols); w.writeheader()
        for r in rows: w.writerow({c:r[c] for c in cols})
    print(fn, len(rows))

wr('edges_concept_stock_mad20.csv', D['e_mad'], ['概念','概念代码','股票代码','股票名称'])
wr('edges_concept_stock_ytd.csv', D['e_ytd'], ['概念','概念代码','股票代码','股票名称'])

def top_pairs(cc, n=15):
    return cc[:n]
wr('edges_concept_concept_mad20.csv', top_pairs(D['cc_mad']), ['概念A','概念B','共享股数','Jaccard','A家数','B家数'])
wr('edges_concept_concept_ytd.csv', top_pairs(D['cc_ytd']), ['概念A','概念B','共享股数','Jaccard','A家数','B家数'])

def cluster(cc, concepts, thr=0.15):
    names=[c['标签'] for c in concepts]; parent={n:n for n in names}
    def find(x):
        while parent[x]!=x: parent[x]=parent[parent[x]]; x=parent[x]
        return x
    def union(a,b):
        ra,rb=find(a),find(b)
        if ra!=rb: parent[rb]=ra
    for r in cc:
        if r['Jaccard']>=thr: union(r['概念A'], r['概念B'])
    groups=collections.defaultdict(list)
    for i,c in enumerate(concepts): groups[find(c['标签'])].append((i+1,c['标签']))
    out=sorted(groups.values(), key=lambda g: min(r for r,_ in g))
    return out

for key, tag in (('top20_mad','m20'), ('top20_ytd','ytd')):
    cc = D['cc_mad'] if key=='top20_mad' else D['cc_ytd']
    ccs = top_pairs(cc)
    for thr in (0.10,0.15,0.20,0.25):
        g=cluster(cc, D[key], thr)
        print(f'--- {key} thr={thr} 簇数={len(g)}', [ [n for _,n in grp] for grp in g ][:8], '...' if len(g)>8 else '')
