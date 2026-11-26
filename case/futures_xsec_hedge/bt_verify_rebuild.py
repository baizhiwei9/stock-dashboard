# -*- coding: utf-8 -*-
"""用重建因子 factors_full.csv 复现既有最优配置(双周·N20动量惩罚λ0.6·3+3)
验证重建面板与既有面板的端到端一致性（对照 +14.52% / 4.44%）"""
import pandas as pd, numpy as np, json

for name,path in [("重建","data/full/factors_full.csv"),("既有","data/norm/factors_mom.csv")]:
    p=pd.read_csv(path,parse_dates=["date"])
    chem={"MA","TA","SA","UR","V","PP","L","EG","EB"}
    p["sector"]=np.where(p["variety"].isin(chem),"化工","农产品")
    p=p.sort_values(["variety","date"]).reset_index(drop=True)
    p["w_raw"]=1.0/p["atr20"]
    COST=0.00025; SIGMA_WIN=60; BASE_LEV=0.375; DD_TRIG=0.08
    dates_all=np.sort(p["date"].unique()); start=pd.Timestamp("2025-01-01")
    dates=dates_all[dates_all>=start]
    wdf=pd.DataFrame({"date":dates}); wdf["biweek"]=pd.to_datetime(wdf["date"]).dt.to_period("2W-FRI")
    reb=set(wdf.groupby("biweek")["date"].max().values)
    ret=p.pivot_table(index="date",columns="variety",values="ret_hold")
    sco=p.pivot_table(index="date",columns="variety",values="score")
    wraw=p.pivot_table(index="date",columns="variety",values="w_raw")
    roll=p.pivot_table(index="date",columns="variety",values="roll_annual")
    mom=p.pivot_table(index="date",columns="variety",values="momz_20")
    sector=p.drop_duplicates("variety").set_index("variety")["sector"].to_dict()
    N=20; lam=0.6
    nav=1.0; peak=1.0; scale=1.0; dd_cut=False; trig_peak=None; pos={}; turn_total=0.0; navs=[]
    for d in dates:
        dr=0.0
        for v,td in pos.items():
            r=ret.loc[d,v] if (d in ret.index and v in ret.columns) else np.nan
            if pd.notna(r): dr+=td["tw"]*scale*r
        nav*=(1+dr); peak=max(peak,nav)
        if not dd_cut and nav/peak-1 < -DD_TRIG:
            trig_peak=peak; dd_cut=True
            turn=sum(abs(t["tw"]) for t in pos.values())*0.5*COST
            nav*=(1-turn); peak=max(peak,nav); scale=0.5
        elif dd_cut and nav>=trig_peak:
            dd_cut=False; trig_peak=None
            turn=sum(abs(t["tw"]) for t in pos.values())*0.5*COST
            nav*=(1-turn); peak=max(peak,nav); scale=1.0
        if d not in reb:
            for v in list(pos.keys()):
                td=pos[v]
                if td["tw"]==0 or v not in sco.columns or d not in sco.index: continue
                st=sco.loc[d,v]
                if pd.isna(st): continue
                dsc=st-td["bs"]
                if td["tw"]>0:
                    if dsc < -2*td["sigma"]: td["tw"]=0.0
                    elif dsc < -td["sigma"]: td["tw"]*=0.5
                else:
                    if dsc > 2*td["sigma"]: td["tw"]=0.0
                    elif dsc > td["sigma"]: td["tw"]*=0.5
                if v in roll.columns and d in roll.index and td["tw"]!=0 and td["base_roll"]>0:
                    rv=roll.loc[d,v]
                    if pd.notna(rv) and rv<0: td["tw"]*=0.30
        if d in reb:
            new={}
            for sec in ["化工","农产品"]:
                vs=[v for v in sector if sector[v]==sec and d in sco.index and d in wraw.index
                    and pd.notna(sco.loc[d,v]) and pd.notna(wraw.loc[d,v]) and (d not in mom.index or pd.notna(mom.loc[d,v]))]
                if len(vs)<4: continue
                n=3 if len(vs)>=6 else 2
                srt=sorted(vs, key=lambda v: sco.loc[d,v]-lam*mom.loc[d,v], reverse=True)
                for grp,sgn in [(srt[:n],1.0),(srt[-n:],-1.0)]:
                    ws=pd.Series({v:wraw.loc[d,v] for v in grp})
                    if ws.sum()==0: continue
                    for v,wv in (ws/ws.sum()*BASE_LEV).items():
                        sigma=sco.loc[:d,v].tail(SIGMA_WIN).std()
                        sigma=sigma if (pd.notna(sigma) and sigma>0) else 1.0
                        new[v]=dict(tw=sgn*abs(wv), bs=sco.loc[d,v], sigma=sigma,
                                    base_roll=roll.loc[d,v] if (d in roll.index and v in roll.columns and pd.notna(roll.loc[d,v])) else 0.0)
            turn=0.0
            for v,td in new.items():
                old=pos[v]["tw"] if v in pos else 0.0
                turn+=abs(td["tw"]-old)
            for v in pos:
                if v not in new: turn+=abs(pos[v]["tw"])
            turn_total+=turn
            nav*=(1-turn*COST); peak=max(peak,nav)
            pos=new
        navs.append(nav)
    navs=np.array(navs); cummax=np.maximum.accumulate(navs)
    dd=((cummax-navs)/cummax).max()
    yrs=(pd.to_datetime(dates[-1])-pd.to_datetime(dates[0])).days/365.25
    print(f"[{name}] final={navs[-1]:.4f} ann={navs[-1]**(1/yrs)-1:.4f} maxdd={dd:.4f} turn={turn_total:.1f}")
