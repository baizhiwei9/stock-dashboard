# -*- coding: utf-8 -*-
"""T2a 联合扫描: biweekly 基础上微调各维度"""
import pandas as pd, numpy as np, json
exec(open("backtest_opt.py").read().split('if __name__')[0])
results=json.load(open("data/norm/opt_grid.json"))
def add(name, **kw):
    r=run_full(**kw)
    results[name]=dict(nav=r["nav"], ann=r["ann"], maxdd=r["maxdd"], maxdd_date=r["maxdd_date"], turn=r["turn"])
    print("%-30s nav=%.4f ann=%+.4f maxdd=%.4f@%s turn=%.0f"%(name,r["nav"],r["ann"],r["maxdd"],r["maxdd_date"],r["turn"]))

# biweekly 基准
add("BW_基准_N20_λ0.6", N=20, lam=0.6, style="biweekly")
# 动量细扫 (biweekly): λ 0.5~1.0, N 15/20
for lam in [0.5,0.7,0.8,1.0]:
    add(f"BW_MOM_λ{lam}", N=20, lam=lam, style="biweekly")
for N in [15]:
    for lam in [0.6,0.8]:
        add(f"BW_MOM_N{N}_λ{lam}", N=N, lam=lam, style="biweekly")
# 腿数
for nl in [2,4]:
    add(f"BW_LEGS_{nl}+{nl}", N=20, lam=0.6, style="biweekly", n_legs=nl)
# 板块
add("BW_SEC_农产60", N=20, lam=0.6, style="biweekly", sec_w={"化工":0.4,"农产品":0.6})
add("BW_SEC_化工60", N=20, lam=0.6, style="biweekly", sec_w={"化工":0.6,"农产品":0.4})
# 多空
add("BW_SIDE_偏多", N=20, lam=0.6, style="biweekly", long_w=0.6, short_w=0.4)
add("BW_SIDE_偏空", N=20, lam=0.6, style="biweekly", long_w=0.4, short_w=0.6)
# λ0.8+偏多组合试探
add("BW_λ0.8_SIDE偏多", N=20, lam=0.8, style="biweekly", long_w=0.6, short_w=0.4)

json.dump({k:{kk:(str(vv) if kk=="maxdd_date" else round(float(vv),4)) for kk,vv in v.items()} for k,v in results.items()},
          open("data/norm/opt_grid.json","w"), ensure_ascii=False, indent=0)
print("\n总计",len(results),"组")
