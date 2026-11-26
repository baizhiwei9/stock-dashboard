# -*- coding: utf-8 -*-
"""T1 单变量扫描: 对照方案A基线(N20 λ0.6) 逐个维度扫描"""
import pandas as pd, numpy as np, json
exec(open("backtest_opt.py").read().split('if __name__')[0])

results={}
def add(name, **kw):
    r=run_full(**kw)
    results[name]=dict(nav=r["nav"], ann=r["ann"], maxdd=r["maxdd"], maxdd_date=r["maxdd_date"], turn=r["turn"])
    print("%-28s nav=%.4f ann=%+.4f maxdd=%.4f@%s turn=%.0f"%(name,r["nav"],r["ann"],r["maxdd"],r["maxdd_date"],r["turn"]))

BASE=dict(N=20, lam=0.6, n_legs=3, style="weekly", sec_w=None, long_w=0.5, short_w=0.5)
add("BASE_方案A_N20_λ0.6", **BASE)

# ① 动量细扫: λ∈{0.5,0.6,0.7,0.8} × N∈{10,15,20}
for N in [10,15,20]:
    for lam in [0.5,0.6,0.7,0.8]:
        add(f"MOM_N{N}_λ{lam}", N=N, lam=lam)

# ② 调仓频率
for st in ["biweekly","monthly"]:
    add(f"FREQ_{st}", style=st)

# ③ 腿数
for nl in [2,4]:
    add(f"LEGS_{nl}+{nl}", n_legs=nl)

# ④ 板块分配 (化工偏重60%/农产偏重60%)
add("SEC_化工60", sec_w={"化工":0.6,"农产品":0.4})
add("SEC_农产60", sec_w={"化工":0.4,"农产品":0.6})

# ⑤ 多空分配 (偏空0.40/0.60杠杆份额 → 每边0.30/0.45; 偏多反向)
add("SIDE_偏空", long_w=0.4, short_w=0.6)
add("SIDE_偏多", long_w=0.6, short_w=0.4)

json.dump({k:{kk:(str(vv) if kk=="maxdd_date" else round(float(vv),4)) for kk,vv in v.items()} for k,v in results.items()},
          open("data/norm/opt_grid.json","w"), ensure_ascii=False, indent=0)
print("\n共",len(results),"组 已落盘 opt_grid.json")
