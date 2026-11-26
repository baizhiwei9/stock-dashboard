# -*- coding: utf-8 -*-
"""T3校验: 1)惩罚量级占比 2)动量因子周IC方向显著性 3)抽样权重变化"""
import pandas as pd, numpy as np
p=pd.read_csv("data/norm/factors_mom.csv", parse_dates=["date"])
chem={"MA","TA","SA","UR","V","PP","L","EG","EB"}
p["sector"]=np.where(p["variety"].isin(chem),"化工","农产品")

# 1) score 截面std / 惩罚量级对比
sco=p.pivot_table(index="date",columns="variety",values="score")
mom20=p.pivot_table(index="date",columns="variety",values="momz_20")
mom60=p.pivot_table(index="date",columns="variety",values="momz_60")
cs=sco.std(axis=1).dropna()
print("[1] score截面std: 均值%.3f 中位%.3f | momz范围[%.2f, %.2f]"%(cs.mean(),cs.median(),mom60.min().min(),mom60.max().max()))
print("    λ=0.3惩罚量|Δ|均值=%.3f, 与score截面std之比=%.2f"%( (0.3*mom60.abs()).mean().mean(), (0.3*mom60.abs().mean().mean())/cs.mean() ))
print("    k=0.5权重惩罚: up版平均削减%.1f%%, sym版最多放大到%.2f倍"%( (1-(1-0.5*mom60.clip(lower=0))).mean().mean()*100, (1-0.5*mom60).clip(lower=0.05).max().max() ))

# 2) 动量因子周IC: corr(momz_N, 未来5日累计ret_hold)
ret=p.pivot_table(index="date",columns="variety",values="ret_hold")
retw=ret.rolling(5).sum().shift(-5)  # t+1..t+5
def ic_table(momf):
    out=[]
    for d in momf.index:
        a=momf.loc[d]; b=retw.loc[d] if d in retw.index else None
        if b is None: continue
        m=pd.concat([a,b],axis=1).dropna()
        if len(m)<5: continue
        c=m[0].corr(m[1]); out.append(c)
    s=pd.Series(out)
    return s.mean(), s.median(), (s>0).mean(), len(s)
for name,mf in [("N20",mom20),("N60",mom60)]:
    m,md,pos,n=ic_table(mf)
    print("[2] 动量%s 周IC均值=%.4f 正IC占比=%.0f%% (n=%d) => 方向%s"%(name,m,pos*100,n,"反转(惩罚有益)" if m<0 else "延续(惩罚有害)"))
    for yr in [2025,2026]:
        sub=[x for d,x in zip(mf.index, pd.Series({d:np.nan for d in mf.index}).index)] if False else None
    # 分年
    ics=[]
    for d in mf.index:
        a=mf.loc[d]; b=retw.loc[d] if d in retw.index else None
        if b is None: continue
        m2=pd.concat([a,b],axis=1).dropna()
        if len(m2)<5: continue
        ics.append((d,m2[0].corr(m2[1])))
    icdf=pd.DataFrame(ics,columns=["date","ic"])
    for yr in [2025,2026]:
        sub=icdf[icdf["date"].dt.year==yr]["ic"]
        print("    %d年: IC均值=%.4f 正占比=%.0f%% (n=%d)"%(yr,sub.mean(),(sub>0).mean()*100,len(sub)))

# 3) 抽样对比 2026-09-24 化工多头组: 基线ATR权重 vs B_sym vs B_up 权重 + A选仓变化
d=pd.Timestamp("2026-09-24")
vs=[v for v in mom60.columns if v in sco.columns and pd.notna(sco.loc[d,v])]
srt=sorted(vs,key=lambda v:sco.loc[d,v],reverse=True)
wraw=p.pivot_table(index="date",columns="variety",values="w_raw")
print("\n[3] 抽样 %s 全市场score排序 top6/bottom6:"%str(d)[:10])
rank=srt
for i,v in enumerate(rank):
    print("   %2d %-4s score=%+.2f mom60=%+.2f"%(i+1,v,sco.loc[d,v],mom60.loc[d,v] if pd.notna(mom60.loc[d,v]) else np.nan))
