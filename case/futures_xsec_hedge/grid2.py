# -*- coding: utf-8 -*-
"""补网格: 方案A λ=0.4/0.5 与 方案B k=0.7 (N20/N60), 寻找回撤下限与最优参数"""
import pandas as pd, numpy as np, json
exec(open("backtest_mom.py").read().split('if __name__')[0])
res={}
for N in [20,60]:
    for lam in [0.4,0.5]:
        r=run_full(mode="A",N=N,param=lam)
        res[f"A_N{N}_λ{lam}"]=r
        print("A_N%d_λ%.1f: nav=%.4f ann=%.4f maxdd=%.4f@%s turn=%.1f"%(N,lam,r["nav"],r["ann"],r["maxdd"],r["maxdd_date"],r["turn"]))
    for k in [0.7]:
        for st in ["up"]:
            r=run_full(mode="B",N=N,param=k,pen_style=st)
            res[f"B_N{N}_k{k}_{st}"]=r
            print("B_N%d_k%.1f_%s: nav=%.4f ann=%.4f maxdd=%.4f@%s turn=%.1f"%(N,k,st,r["nav"],r["ann"],r["maxdd"],r["maxdd_date"],r["turn"]))
old=json.load(open("data/norm/mom_grid.json"))
old.update({k:{kk:(str(vv) if kk=="maxdd_date" else round(float(vv),4)) for kk,vv in v.items()} for k,v in res.items()})
json.dump(old,open("data/norm/mom_grid.json","w"),ensure_ascii=False,indent=0)
print("已并入 mom_grid.json, 共",len(old),"组")
