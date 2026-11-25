# -*- coding: utf-8 -*-
"""T3校验续: 动量因子周IC方向显著性 + 抽样权重变化"""
import pandas as pd, numpy as np
p=pd.read_csv("data/norm/factors_mom.csv", parse_dates=["date"])
sco=p.pivot_table(index="date",columns="variety",values="score")
mom20=p.pivot_table(index="date",columns="variety",values="momz_20")
mom60=p.pivot_table(index="date",columns="variety",values="momz_60")
ret=p.pivot_table(index="date",columns="variety",values="ret_hold")
retw=ret.rolling(5).sum().shift(-5)

def ic_table(momf,label):
    ics=[]
    for d in momf.index:
        if d not in retw.index: continue
        b=retw.loc[d]
        m=pd.concat([momf.loc[d],b],axis=1).dropna()
        if len(m)<5: continue
        ics.append((d, m.iloc[:,0].corr(m.iloc[:,1])))
    icdf=pd.DataFrame(ics,columns=["date","ic"])
    print("[%s] 全期 IC均值=%.4f 正占比=%.0f%% (n=%d)"%(label,icdf.ic.mean(),(icdf.ic>0).mean()*100,len(icdf)))
    for yr in [2025,2026]:
        sub=icdf[icdf["date"].dt.year==yr]["ic"]
        if len(sub): print("     %d年: IC均值=%.4f 正占比=%.0f%% (n=%d)"%(yr,sub.mean(),(sub>0).mean()*100,len(sub)))
    return icdf
for mf,lab in [(mom20,"动量N20"),(mom60,"动量N60")]:
    ic_table(mf,lab)

# 抽样对比：2026-09-24
d=pd.Timestamp("2026-09-24")
wraw=p.pivot_table(index="date",columns="variety",values="w_raw")
chem={"MA","TA","SA","UR","V","PP","L","EG","EB"}
sec=np.where(p.drop_duplicates("variety").variety.isin(chem),"化工","农产品")
secmap=dict(zip(p.drop_duplicates("variety").variety,sec))
print("\n[3] 抽样 %s 化工板块 score排序(取前3/后3):"%str(d)[:10])
cvs=[v for v in mom60.columns if v in sco.columns and pd.notna(sco.loc[d,v]) and secmap[v]=="化工"]
srt=sorted(cvs,key=lambda v:sco.loc[d,v],reverse=True)
for grp,tag in [(srt[:3],"多"),(srt[-3:],"空")]:
    ws=pd.Series({v:wraw.loc[d,v] for v in grp}); ws=ws/ws.sum()
    pen_up=(1-0.5*mom60.loc[d,grp].clip(lower=0)).clip(lower=0.05)
    pen_sym=(1-0.5*mom60.loc[d,grp]).clip(lower=0.05)
    w_up=ws*pen_up; w_up=w_up/w_up.sum(); w_sym=ws*pen_sym; w_sym=w_sym/w_sym.sum()
    print("  [%s]"%tag)
    for v in grp:
        print("    %-4s score=%+.2f mom60=%+.2f | 基线权重%5.1f%% -> up版%5.1f%% -> sym版%5.1f%%"%(
            v,sco.loc[d,v],mom60.loc[d,v],ws[v]*100,w_up[v]*100,w_sym[v]*100))
