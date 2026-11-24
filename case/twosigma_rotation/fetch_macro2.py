# -*- coding: utf-8 -*-
"""用akshare bond_china_close_return 拉取中债曲线历史(逐月)，提取节点"""
import os, sys, time, datetime
sys.path.insert(0, '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/pylibs')
import pandas as pd, akshare as ak
BASE=os.path.dirname(os.path.abspath(__file__)); DATA=os.path.join(BASE,"data")
OUT=os.path.join(DATA,"yield_curves.csv")
CURVES={"国债":"国债","国开":"政策性金融债(国开)","中票AAA":"中短期票据(AAA)","中票AA+":"中短期票据(AA+)"}
def month_range(y,m):
    s=datetime.date(y,m,1)
    if m==12: e=datetime.date(y+1,1,1)-datetime.timedelta(days=1)
    else: e=datetime.date(y,m+1,1)-datetime.timedelta(days=1)
    return s,e
done=set()
if os.path.exists(OUT):
    old=pd.read_csv(OUT); done=set(zip(old['curve'],old['date']))
    print("existing rows:",len(old),"range:",old['date'].min(),old['date'].max(), flush=True)
rows=[]; calls=0; fails=0
for ckey,cname in CURVES.items():
    for y in range(2023,2027):
        for m in range(1,13):
            s,e=month_range(y,m)
            if e.isoformat() < "2023-06-01" or s.isoformat() > os.environ.get("END_YM_S", "2026-09-30"): continue
            got=False
            for attempt in range(3):
                try:
                    df=ak.bond_china_close_return(symbol=cname, period='1', start_date=s.strftime('%Y%m%d'), end_date=e.strftime('%Y%m%d'))
                    calls+=1
                    if df is not None and len(df):
                        for _,r in df.iterrows():
                            k=(ckey,str(r['日期']))
                            if k not in done:
                                rows.append({'curve':ckey,'date':str(r['日期']),'term':float(r['期限']),'yield':float(r['到期收益率'])})
                                done.add(k)
                    got=True; time.sleep(0.4); break
                except Exception as ex:
                    fails+=1; time.sleep(1.5+1.5*attempt)
            if not got:
                print(f"FAIL {cname} {s}..{e}", flush=True)
    print(f"...{ckey} rows={len(rows)} calls={calls} fails={fails}", flush=True)
    if rows:
        nd=pd.DataFrame(rows).drop_duplicates(['curve','date','term']).sort_values(['curve','date','term'])
        if os.path.exists(OUT):
            nd=pd.concat([pd.read_csv(OUT),nd]).drop_duplicates(['curve','date','term']).sort_values(['curve','date','term'])
        nd.to_csv(OUT,index=False)
print("DONE total rows=",len(rows),"calls=",calls,"fails=",fails, flush=True)
