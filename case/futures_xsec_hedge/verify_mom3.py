# -*- coding: utf-8 -*-
import pandas as pd, numpy as np
p=pd.read_csv("data/norm/factors_mom.csv", parse_dates=["date"])
sco=p.pivot_table(index="date",columns="variety",values="score")
mom20=p.pivot_table(index="date",columns="variety",values="momz_20")
mom60=p.pivot_table(index="date",columns="variety",values="momz_60")
wraw=(1/p.pivot_table(index="date",columns="variety",values="atr20"))
chem={"MA","TA","SA","UR","V","PP","L","EG","EB"}
secmap=dict(zip(p.drop_duplicates("variety").variety,
                np.where(p.drop_duplicates("variety").variety.isin(chem),"化工","农产品")))

d=pd.Timestamp("2026-09-24")
for secname in ["化工","农产品"]:
    cvs=[v for v in mom60.columns if v in sco.columns and pd.notna(sco.loc[d,v]) and secmap[v]==secname]
    srt=sorted(cvs,key=lambda v:sco.loc[d,v],reverse=True)
    print("[%s] score top3(多) / bottom3(空):"%secname)
    for grp,tag in [(srt[:3],"多"),(srt[-3:],"空")]:
        ws=pd.Series({v:wraw.loc[d,v] for v in grp}); ws=ws/ws.sum()
        pen_up=(1-0.5*mom60.loc[d,grp].clip(lower=0)).clip(lower=0.05)
        pen_sym=(1-0.5*mom60.loc[d,grp]).clip(lower=0.05)
        w_up=ws*pen_up; w_up=w_up/w_up.sum(); w_sym=ws*pen_sym; w_sym=w_sym/w_sym.sum()
        print("  %s"%tag)
        for v in grp:
            print("    %-4s score=%+.2f mom60=%+.2f atr20=%.0f | 基线%5.1f%% up版%5.1f%% sym版%5.1f%%"%(
                v,sco.loc[d,v],mom60.loc[d,v],p[p.variety==v].atr20.iloc[-1],ws[v]*100,w_up[v]*100,w_sym[v]*100))
