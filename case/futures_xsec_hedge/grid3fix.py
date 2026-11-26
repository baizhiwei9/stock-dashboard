# -*- coding: utf-8 -*-
"""修正: 3+3口径重跑 λ=0.6/0.75/1.0 (N20/N60)"""
import pandas as pd, numpy as np, json
exec(open("backtest_mom.py").read().split('if __name__')[0])
res={}
for N in [20,60]:
    for lam in [0.6,0.75,1.0]:
        r=run_full(mode="A",N=N,param=lam)
        res[f"A_N{N}_λ{lam}"]=r
        print("A_N%d_λ%.2f: nav=%.4f ann=%.4f maxdd=%.4f@%s turn=%.1f"%(N,lam,r["nav"],r["ann"],r["maxdd"],r["maxdd_date"],r["turn"]))
# 一致性: run_full vs robust nav_series
r=run_full(mode="A",N=20,param=0.75)
print("一致性检查 run_full A_N20_λ0.75: nav=%.4f maxdd=%.4f (robust: nav=%.4f@1.1544?, maxdd=0.0557)"%(r["nav"],r["maxdd"]))
old=json.load(open("data/norm/mom_grid.json"))
for k in [f"A_N20_λ{v}" for v in [0.6,0.75,1.0]]+[f"A_N60_λ{v}" for v in [0.6,0.75,1.0]]:
    old.pop(k,None)  # 删除错误口径(2+2)条目
old.update({k:{kk:(str(vv) if kk=="maxdd_date" else round(float(vv),4)) for kk,vv in v.items()} for k,v in res.items()})
json.dump(old,open("data/norm/mom_grid.json","w"),ensure_ascii=False,indent=0)
print("已修正并入, 共",len(old),"组")
