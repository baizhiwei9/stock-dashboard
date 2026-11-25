# -*- coding: utf-8 -*-
"""T2b 组合试探 + 稳健性: 分年/无风控对照/排序改写核查"""
import pandas as pd, numpy as np, json
exec(open("backtest_opt.py").read().split('if __name__')[0])
results=json.load(open("data/norm/opt_grid.json"))
def add(name, **kw):
    r=run_full(**kw)
    results[name]=dict(nav=r["nav"], ann=r["ann"], maxdd=r["maxdd"], maxdd_date=r["maxdd_date"], turn=r["turn"])
    print("%-32s nav=%.4f ann=%+.4f maxdd=%.4f@%s turn=%.0f"%(name,r["nav"],r["ann"],r["maxdd"],r["maxdd_date"],r["turn"]))

# 组合: biweekly × (偏空 / 化工60 / λ1.0) 及其组合
add("BW_偏空", N=20, lam=0.6, style="biweekly", long_w=0.4, short_w=0.6)
add("BW_λ0.8_偏空", N=20, lam=0.8, style="biweekly", long_w=0.4, short_w=0.6)
add("BW_化工60_λ0.8", N=20, lam=0.8, style="biweekly", sec_w={"化工":0.6,"农产品":0.4})
add("BW_化工60_偏空", N=20, lam=0.6, style="biweekly", sec_w={"化工":0.6,"农产品":0.4}, long_w=0.4, short_w=0.6)
add("BW_农产60_偏空", N=20, lam=0.6, style="biweekly", sec_w={"化工":0.4,"农产品":0.6}, long_w=0.4, short_w=0.6)
# 无风控对照(评估风控贡献): biweekly基准
r_nr=run_full(N=20, lam=0.6, style="biweekly", with_risk=False)
print("BW基准 无风控: nav=%.4f maxdd=%.4f"%(r_nr["nav"], r_nr["maxdd"]))
r_nr2=run_full(N=20, lam=0.6, style="weekly", with_risk=False)
print("WK基准 无风控: nav=%.4f maxdd=%.4f"%(r_nr2["nav"], r_nr2["maxdd"]))
json.dump({k:{kk:(str(vv) if kk=="maxdd_date" else round(float(vv),4)) for kk,vv in v.items()} for k,v in results.items()},
          open("data/norm/opt_grid.json","w"), ensure_ascii=False, indent=0)
print("总计",len(results),"组")
