# -*- coding: utf-8 -*-
"""拉取中债收益率曲线历史(国债/国开/AAA中票/AA+中票) 2023-07 至 2026-09,提取3/5/10年节点"""
import os, sys, time, json, datetime
import requests, pandas as pd
BASE=os.path.dirname(os.path.abspath(__file__)); DATA=os.path.join(BASE,"data")
h={'User-Agent':'Mozilla/5.0'}
URL="https://www.chinamoney.com.cn/ags/ms/cm-u-bk-currency/ClsYldCurvHis"
CURVES={"国债":"CYCC000","国开":"CYCC021","中票AAA":"CYCC82B","中票AA+":"CYCC82D"}
OUT=os.path.join(DATA,"yield_curves.csv")
def month_range(y,m):
    s=datetime.date(y,m,1)
    if m==12: e=datetime.date(y+1,1,1)-datetime.timedelta(days=1)
    else: e=datetime.date(y,m+1,1)-datetime.timedelta(days=1)
    return s.isoformat(), e.isoformat()
rows=[]
try:
    existing=pd.read_csv(OUT)
    done=set(zip(existing['curve'],existing['date']))
except Exception:
    done=set()
cnt=0
for cname,code in CURVES.items():
    for y in range(2023,2027):
        for m in range(1,13):
            s,e=month_range(y,m)
            if e < "2023-07-01" or s > os.environ.get("END_YM_S", "2026-09-30"): continue
            for period in ['1']:
                try:
                    r=requests.get(URL,headers=h,params={'lang':'CN','reference':'1,2,3','bondType':code,
                        'startDate':s,'endDate':e,'termId':period,'pageNum':'1','pageSize':'120'},timeout=20)
                    j=r.json(); rec=j.get('records',[])
                    if not rec: continue
                    for x in rec:
                        d=x.get('newDateValue') or x.get('cdNewDateValue') or x.get('date') or x.get('dataDate')
                        t=x.get('cdCurveCdY') or x.get('term')
                        yv=x.get('rltvYld') or x.get('yield')
                        k=(cname,str(d))
                        if d and t and yv and k not in done:
                            rows.append({'curve':cname,'date':str(d)[:10],'term':float(t),'yield':float(yv)})
                            done.add(k)
                    cnt+=1
                except Exception as ex:
                    print(f"fail {cname} {s}..{e}: {repr(ex)[:80]}", flush=True)
                    time.sleep(2)
                time.sleep(0.6)
    print(f"... {cname} done, total rows {len(rows)}", flush=True)
if rows:
    df=pd.DataFrame(rows).drop_duplicates(['curve','date','term']).sort_values(['curve','date','term'])
    if os.path.exists(OUT):
        old=pd.read_csv(OUT); df=pd.concat([old,df]).drop_duplicates(['curve','date','term']).sort_values(['curve','date','term'])
    df.to_csv(OUT,index=False)
    print("WROTE", OUT, len(df), "calls:", cnt, flush=True)
else:
    print("NO NEW ROWS", flush=True)
    if os.path.exists(OUT):
        df=pd.read_csv(OUT); print("existing:", len(df), df.tail(3).to_string(), flush=True)
