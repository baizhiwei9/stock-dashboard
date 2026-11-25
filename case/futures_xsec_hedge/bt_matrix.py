# -*- coding: utf-8 -*-
"""统一对照矩阵: 频率/腿数/机会驱动 x 窗口, 输出分年表现。统一口径同 backtest_opt。"""
import pandas as pd, numpy as np, json, sys

p = pd.read_csv(sys.argv[1], parse_dates=["date"])
chem = {"MA","TA","SA","UR","V","PP","L","EG","EB"}
p["sector"] = np.where(p["variety"].isin(chem), "化工","农产品")
p = p.sort_values(["variety","date"]).reset_index(drop=True)
p["w_raw"] = 1.0/p["atr20"]
COST=0.00025; SIGMA_WIN=60; BASE_LEV=0.375; TOTAL_LEV=1.5; DD_TRIG=0.08
start = pd.Timestamp(sys.argv[2]); out_file = sys.argv[3]
dates_all=np.sort(p["date"].unique()); dates=dates_all[dates_all>=start]
wdf=pd.DataFrame({"date":dates}); wdf["week"]=pd.to_datetime(wdf["date"]).dt.to_period("W-FRI")
wk=wdf.groupby("week")["date"].max()
REB_W=set(wk.values)
w2=wk.reset_index(); w2["bw"]=w2["week"].astype(str).str[:4]+"-W"+((w2["week"].dt.week-1)//2*2+1).astype(str).str.zfill(2)
REB_BW=set(w2.groupby("bw")["date"].max().values)
p["mr_20"]=p.groupby("variety")["ret_hold"].transform(lambda s: s.rolling(20).sum())
p["momz_20"]=p.groupby(["date","sector"])["mr_20"].transform(lambda x:(x-x.mean())/x.std())
ret=p.pivot_table(index="date",columns="variety",values="ret_hold")
sco=p.pivot_table(index="date",columns="variety",values="score")
wraw=p.pivot_table(index="date",columns="variety",values="w_raw")
roll=p.pivot_table(index="date",columns="variety",values="roll_annual")
mom=p.pivot_table(index="date",columns="variety",values="momz_20")
sector=p.drop_duplicates("variety").set_index("variety")["sector"].to_dict()

def budget(vs, d, lev, sgn):
    out={}
    ws=pd.Series({v:wraw.loc[d,v] for v in vs if pd.notna(wraw.loc[d,v])})
    if ws.sum()==0: return out
    for v,wv in (ws/ws.sum()*lev).items():
        sigma=sco.loc[:d,v].tail(SIGMA_WIN).std()
        sigma=sigma if(pd.notna(sigma) and sigma>0) else 1.0
        out[v]=dict(tw=sgn*abs(wv), bs=sco.loc[d,v], sigma=sigma,
                    base_roll=roll.loc[d,v] if(d in roll.index and v in roll.columns and pd.notna(roll.loc[d,v])) else 0.0)
    return out

def pick_sector(d, lam, sec_legs):
    out={}
    for sec,n in sec_legs.items():
        vs=[v for v in sector if sector[v]==sec and d in sco.index and d in wraw.index
            and pd.notna(sco.loc[d,v]) and pd.notna(wraw.loc[d,v]) and pd.notna(mom.loc[d,v])]
        if len(vs) < n*2: continue   # 严格口径: 品种不足整板块跳过(与backtest_legs一致)
        srt=sorted(vs, key=lambda v: sco.loc[d,v]-lam*mom.loc[d,v], reverse=True)
        for v in srt[:n]: out[v]=1.0
        for v in srt[-n:]: out[v]=-1.0
    return out

def pick_cross(d, lam, k):
    vs=[v for v in sector if d in sco.index and d in wraw.index
        and pd.notna(sco.loc[d,v]) and pd.notna(wraw.loc[d,v]) and pd.notna(mom.loc[d,v])]
    if len(vs)<k+1: return {}
    srt=sorted(vs, key=lambda v: sco.loc[d,v]-lam*mom.loc[d,v], reverse=True)
    out={}
    for v in srt[:k]: out[v]=out.get(v,0.0)+1.0
    for v in srt[-k:]: out[v]=out.get(v,0.0)-1.0
    return {v:s for v,s in out.items() if s!=0}

def run(cfg, lam=0.6):
    style=cfg.get("style","biweekly"); mode=cfg.get("mode","fixed"); reb=REB_BW if style=="biweekly" else REB_W
    nav=1.0; peak=1.0; scale=1.0; dd_cut=False; trig_peak=None; pos={}; turn_total=0.0; navs=[]; nleg=0; nreb=0
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
            if mode in ("fixed","fullpool"):
                legs=pick_sector(d, lam, cfg["sec_legs"])
            elif mode=="cross":
                legs=pick_cross(d, lam, cfg["k"])
            elif mode=="opp":
                legs={}
                for sec in ["化工","农产品"]:
                    vs=[v for v in sector if sector[v]==sec and d in sco.index and d in wraw.index
                        and pd.notna(sco.loc[d,v]) and pd.notna(wraw.loc[d,v]) and pd.notna(mom.loc[d,v])]
                    if len(vs)<3: continue
                    sv=pd.Series({v:sco.loc[d,v] for v in vs})
                    g=sv.std(ddof=1)
                    if pd.isna(g) or g<cfg["sigma_gate"]: continue
                    mu=sv.mean()
                    srt=sorted(vs, key=lambda v: sco.loc[d,v]-lam*mom.loc[d,v], reverse=True)
                    cand=[v for v in srt if abs(sco.loc[d,v]-mu)>=cfg["k"]*g][:cfg["max_n"]]
                    neg=[v for v in srt if abs(sco.loc[d,v]-mu)>=cfg["k"]*g][-cfg["max_n"]:]
                    for v in cand: legs[v]=1.0
                    for v in reversed(neg):
                        if v not in legs or True: legs[v]=-1.0
                    legs={v:legs[v] for v in legs if not (v in cand and legs[v]==-1.0)}
            new={}
            if mode=="opp":
                nreb+=1
                for sec in ["化工","农产品"]:
                    lv=[v for v,sg in legs.items() if sg>0 and sector[v]==sec]
                    sv=[v for v,sg in legs.items() if sg<0 and sector[v]==sec]
                    for grp,lev,sgn in [(lv,BASE_LEV,1.0),(sv,BASE_LEV,-1.0)]:
                        if not grp: continue
                        new.update(budget(grp,d,lev,sgn))
            else:
                long_v=[v for v,sg in legs.items() if sg>0]; short_v=[v for v,sg in legs.items() if sg<0]
                if not long_v and not short_v: pos={}; navs.append(nav); continue
                if mode=="cross":
                    for grp,lev,sgn in [(long_v,TOTAL_LEV*0.5,1.0),(short_v,TOTAL_LEV*0.5,-1.0)]:
                        if grp: new.update(budget(grp,d,lev,sgn))
                else:
                    for sec in ["化工","农产品"]:
                        lv=[v for v in long_v if sector[v]==sec]; sv=[v for v in short_v if sector[v]==sec]
                        for grp,lev,sgn in [(lv,BASE_LEV,1.0),(sv,BASE_LEV,-1.0)]:
                            if grp: new.update(budget(grp,d,lev,sgn))
            turn=0.0
            for v,td in new.items():
                old=pos[v]["tw"] if v in pos else 0.0
                turn+=abs(td["tw"]-old)
            for v in pos:
                if v not in new: turn+=abs(pos[v]["tw"])
            turn_total+=turn
            nav*=(1-turn*COST); peak=max(peak,nav)
            pos=new
            if mode=="opp": nleg+=len(pos)
        navs.append(nav)
    navs=np.array(navs); cums=np.maximum.accumulate(navs)
    dd=((cums-navs)/cums).max(); dd_idx=int(((cums-navs)/cums).argmax())
    dd_date=str(pd.to_datetime(dates)[dd_idx])[:10]
    yrs=(pd.to_datetime(dates[-1])-pd.to_datetime(dates[0])).days/365.25
    out=dict(nav=float(navs[-1]), ann=float(navs[-1]**(1/yrs)-1), maxdd=float(dd),
             maxdd_date=dd_date, turn=float(turn_total), yrs=round(yrs,2))
    if mode=="opp": out["avg_legs"]=round(nleg/max(nreb,1),2)
    # 分年表现
    idx_dates=pd.to_datetime(dates); yrs_=idx_dates.year
    for y in sorted(set(yrs_)):
        m=yrs_==y
        seg=navs[m]; seg0=1.0 if m[0] else navs[np.where(m)[0][0]-1]
        ann_y=seg[-1]/seg0-1
        c2=np.maximum.accumulate(seg); dd_y=((c2-seg)/c2).max()
        out[f"y{y}_ann"]=float(ann_y); out[f"y{y}_dd"]=float(dd_y)
    return out

cfgs={
 "fixed3x3_biweekly":{"style":"biweekly","mode":"fixed","sec_legs":{"化工":3,"农产品":3}},
 "fixed3x3_weekly":{"style":"weekly","mode":"fixed","sec_legs":{"化工":3,"农产品":3}},
 "fullpool_biweekly":{"style":"biweekly","mode":"fullpool","sec_legs":{"化工":4,"农产品":5}},
 "cross_top10":{"style":"biweekly","mode":"cross","k":10},
 "opp_s04_k05_n3":{"style":"biweekly","mode":"opp","sigma_gate":0.4,"k":0.5,"max_n":3},
 "opp_s08_k05_n3":{"style":"biweekly","mode":"opp","sigma_gate":0.8,"k":0.5,"max_n":3},
 "opp_s10_k05_n3":{"style":"biweekly","mode":"opp","sigma_gate":1.0,"k":0.5,"max_n":3},
 "opp_s06_k05_n3":{"style":"biweekly","mode":"opp","sigma_gate":0.6,"k":0.5,"max_n":3},
}
res={}
for name,cfg in cfgs.items():
    try:
        res[name]=run(cfg); print(name, json.dumps(res[name],ensure_ascii=False)[:200])
    except Exception as e:
        res[name]={"error":str(e)}; print(name,"ERR",e)
json.dump(res, open(out_file,"w"), ensure_ascii=False, indent=1)
print("saved", out_file)
