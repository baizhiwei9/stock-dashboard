# -*- coding: utf-8 -*-
"""T3补: 双周框架下 IC 检验 + 多空/板块桶归因"""
import pandas as pd, numpy as np, json
exec(open("backtest_opt.py").read().split('if __name__')[0])

reb=reb_dates("biweekly")
# ---- 动量IC (双周调仓期) ----
mom=moms[20]
daily_ic=[]
for d in reb:
    if d not in sco.index: continue
    for sec in ["化工","农产品"]:
        vs=[v for v in sector if sector[v]==sec and d in sco.index and pd.notna(sco.loc[d,v])
            and d in mom.index and pd.notna(mom.loc[d,v]) and d in ret.index and pd.notna(ret.loc[d,v])]
        if len(vs)<4: continue
        f=sco.loc[d,vs] - 0.6*mom.loc[d,vs]
        r=ret.loc[d,vs]
        if r.std()>0 and f.std()>0:
            daily_ic.append((d, sec, r.corr(f, method="spearman")))
ic=pd.DataFrame(daily_ic,columns=["date","sec","ic"])
def ann_detail(s): return "%.3f (%.1f月/%.0f期)"%(s.iloc[0], s.iloc[-1]/100, len(s))
g=ic.groupby("date")["ic"].mean()
print("综合score(含动量修正) IC: 全期%.3f | 2025 %.3f | 2026 %.3f"%(
      g.mean(), g[g.index.year==2025].mean(), g[g.index.year==2026].mean()))
print("动量修正IC(负=有效): 全期%.3f | 2025 %.3f | 2026 %.3f"%(
      daily_ic_neg_mean if False else np.nan, np.nan, np.nan))
# 动量原始IC
mIC=[]
for d in reb:
    if d not in sco.index: continue
    for sec in ["化工","农产品"]:
        vs=[v for v in sector if sector[v]==sec and d in sco.index and pd.notna(sco.loc[d,v])
            and d in mom.index and pd.notna(mom.loc[d,v]) and d in ret.index and pd.notna(ret.loc[d,v])]
        if len(vs)<4: continue
        r=ret.loc[d,vs]; f=mom.loc[d,vs]
        if r.std()>0 and f.std()>0: mIC.append(r.corr(f, method="spearman"))
m=pd.Series(mIC)
print("动量20原始IC: 全期%.4f | 2025 %.4f | 2026 %.4f"%(m.mean(),
      np.mean([x for x,d2 in zip(mIC,[1]*len(mIC))]), np.nan)) if False else None
# 分年算动量IC
for yr in [2025,2026]:
    sub=[x for (d,x) in [(r[0],r[1]) for r in [(d,c) for d,c in zip(reb,mIC)]] if d.year==yr]
    pass
# 直接按年度重算
mIC_by=[]
for d in reb:
    if d not in sco.index: continue
    for sec in ["化工","农产品"]:
        vs=[v for v in sector if sector[v]==sec and d in sco.index and pd.notna(sco.loc[d,v])
            and d in mom.index and pd.notna(mom.loc[d,v]) and d in ret.index and pd.notna(ret.loc[d,v])]
        if len(vs)<4: continue
        r=ret.loc[d,vs]; f=mom.loc[d,vs]
        if r.std()>0 and f.std()>0: mIC_by.append((d,r.corr(f,method="spearman")))
mi=pd.DataFrame(mIC_by,columns=["date","ic"])
print("动量20原始IC: 全期%.4f | 2025 %.4f | 2026 %.4f"%(mi.ic.mean(),
      mi.ic[mi.date.dt.year==2025].mean(), mi.ic[mi.date.dt.year==2026].mean()))

# ---- 四因子IC(全期, 周频口径沿用基线报告) ----
# 引用基线: 综合score周IC +0.020(2025+0.044/2026-0.013), 库ICIR1.17, 展期/基差/结构0.15-0.25

# ---- 多空/板块桶归因: 双周基准 ----
ff={}
gp=pd.read_csv("data/norm/panel.csv", parse_dates=["date"])
sector={}
for _,r in gp[["symbol","sector"]].drop_duplicates().iterrows(): sector[r["symbol"]]=r["sector"]
sco=factors.pivot_table(index="date",columns="symbol",values="score")
wraw=pd.read_csv("data/norm/wraw_atr.csv", parse_dates=["date"]).pivot_table(index="date",columns="symbol",values="w")
ret=pd.read_csv("data/norm/ret_fwd.csv", parse_dates=["date"]).pivot_table(index="date",columns="symbol",values="fwd_ret")
COST=0.00025; TOTAL_LEV=1.5; SIGMA_WIN=60; DD_TRIG=-0.08
# 桶收益: 双周调仓, 无成本无风控, 只分桶
buckets={}
pos={}
for d in dates:
    if d in reb and d in sco.index:
        newL={}; newS={}
        for sec in ["化工","农产品"]:
            vs=[v for v in sector if sector[v]==sec and d in sco.index and d in wraw.index and pd.notna(sco.loc[d,v])]
            if len(vs)<6: continue
            srt=sorted(vs,key=lambda v:sco.loc[d,v]-0.6*mom.loc[d,v],reverse=True)
            for v in srt[:3]: newL[v]=wraw.loc[d,v]
            for v in srt[-3:]: newS[v]=wraw.loc[d,v]
        pos=("L",newL,"S",newS)
    key,newL,_,newS=pos
    for tag,grp,sgn in [("long",newL,1),("short",newS,-1)]:
        tot=sum(grp.values())
        if tot==0: continue
        rsum=0
        for v,wv in grp.items():
            rv=ret.loc[d,v] if (d in ret.index and v in ret.columns) else np.nan
            if pd.notna(rv): rsum+=(wv/tot)*sgn*rv
        buckets.setdefault(tag,[]).append((d,rsum*0.75))
for tag in ["long","short"]:
    s=pd.Series({d:r for d,r in buckets[tag]})
    print("%s桶: 年化%s 累计%.1f%%"%("多头", "+" if s.mean()>0 else "", (s.mean()*len(s)/1.7413*100)))
