# -*- coding: utf-8 -*-
"""λ=0.6/0.75/1.0 找拐点; 同时验证6腿变4腿(每板块2+2)是否进一步改善"""
import pandas as pd, numpy as np, json
NS = open("backtest_mom.py").read().split('if __name__')[0]
exec(NS.replace("n=3 if len(vs)>=6 else 2","n=2 if len(vs)>=4 else 2"))
res={}
for N in [20,60]:
    for lam in [0.6,0.75,1.0]:
        r=run_full(mode="A",N=N,param=lam)
        res[f"A_N{N}_λ{lam}"]=r
        print("A_N%d_λ%.2f: nav=%.4f ann=%.4f maxdd=%.4f@%s turn=%.1f"%(N,lam,r["nav"],r["ann"],r["maxdd"],r["maxdd_date"],r["turn"]))
old=json.load(open("data/norm/mom_grid.json"))
old.update({k:{kk:(str(vv) if kk=="maxdd_date" else round(float(vv),4)) for kk,vv in v.items()} for k,v in res.items()})
json.dump(old,open("data/norm/mom_grid.json","w"),ensure_ascii=False,indent=0)
print("已并入, 共",len(old),"组")
