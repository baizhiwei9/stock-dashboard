# -*- coding: utf-8 -*-
"""机会驱动配对 v3 —— 统一 backtest_opt 口径 (momz=rolling sum 截面z, 双周调仓)
核心规则(用户最新要求): 出现交易机会才做配对, 不强制固定对数。
- 板块分化度门槛: 板块内 score 截面标准差 < sigma_gate -> 该板块空仓(无机会)
- 极值腿门槛: |score_adj - 板块均值| >= k*板块标准差 的品种才入选(腿数动态, 上限 max_n)
- 全部基准/风控/成本与 backtest_opt 一致
"""
import pandas as pd, numpy as np, json, sys

p = pd.read_csv(sys.argv[1], parse_dates=["date"])
chem = {"MA","TA","SA","UR","V","PP","L","EG","EB"}
p["sector"] = np.where(p["variety"].isin(chem), "化工","农产品")
p = p.sort_values(["variety","date"]).reset_index(drop=True)
p["w_raw"] = 1.0/p["atr20"]
COST=0.00025; SIGMA_WIN=60; BASE_LEV=0.375; DD_TRIG=0.08
start = pd.Timestamp(sys.argv[2])
dates_all=np.sort(p["date"].unique()); dates=dates_all[dates_all>=start]
wdf=pd.DataFrame({"date":dates}); wdf["week"]=pd.to_datetime(wdf["date"]).dt.to_period("W-FRI")
w=wdf.groupby("week")["date"].max().reset_index()
w["bw"]=w["week"].astype(str).str[:4]+"-W"+((w["week"].dt.week-1)//2*2+1).astype(str).str.zfill(2)
reb=set(w.groupby("bw")["date"].max().values)
# momz 统一口径 rolling N sum -> 板块内截面z
p["mr_20"]=p.groupby("variety")["ret_hold"].transform(lambda s: s.rolling(20).sum())
p["momz_20"]=p.groupby(["date","sector"])["mr_20"].transform(lambda x:(x-x.mean())/x.std())
ret=p.pivot_table(index="date",columns="variety",values="ret_hold")
sco=p.pivot_table(index="date",columns="variety",values="score")
wraw=p.pivot_table(index="date",columns="variety",values="w_raw")
roll=p.pivot_table(index="date",columns="variety",values="roll_annual")
mom=p.pivot_table(index="date",columns="variety",values="momz_20")
sector=p.drop_duplicates("variety").set_index("variety")["sector"].to_dict()

def run(sigma_gate, k, max_n, lam=0.6):
    nav=1.0; peak=1.0; scale=1.0; dd_cut=False; trig_peak=None; pos={}
    turn_total=0.0; navs=[]; leg_hist=[]; sec_gated=[]; leg_detail=[]
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
                    and pd.notna(sco.loc[d,v]) and pd.notna(wraw.loc[d,v]) and (v not in mom.columns or d not in mom.index or pd.notna(mom.loc[d,v]))]
                if len(vs) < 4: continue
                sc_adj={v: sco.loc[d,v]-lam*mom.loc[d,v] for v in vs}
                vals=np.array(list(sc_adj.values()))
                mu=vals.mean(); sig=vals.std(ddof=1)
                if pd.isna(sig) or sig < sigma_gate:
                    sec_gated.append((d,sec,"σ不足"))
                    continue
                longs=[v for v in vs if sc_adj[v]-mu >= k*sig]
                shorts=[v for v in vs if mu-sc_adj[v] >= k*sig]
                longs=sorted(longs,key=lambda v:sc_adj[v],reverse=True)[:max_n]
                shorts=sorted(shorts,key=lambda v:sc_adj[v])[:max_n]
                for grp,sgn in [(longs,1.0),(shorts,-1.0)]:
                    if not grp: continue
                    ws=pd.Series({v:wraw.loc[d,v] for v in grp})
                    if ws.sum()==0: continue
                    for v,wv in (ws/ws.sum()*BASE_LEV).items():
                        sigma=sco.loc[:d,v].tail(SIGMA_WIN).std()
                        sigma=sigma if (pd.notna(sigma) and sigma>0) else 1.0
                        new[v]=dict(tw=sgn*abs(wv), bs=sco.loc[d,v], sigma=sigma,
                                    base_roll=roll.loc[d,v] if (d in roll.index and v in roll.columns and pd.notna(roll.loc[d,v])) else 0.0)
                leg_detail.append((d,sec,len(longs),len(shorts),round(sig,3)))
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
        leg_hist.append(sum(1 for t in pos.values() if t["tw"]!=0))
    navs=np.array(navs); cummax=np.maximum.accumulate(navs)
    dd=((cummax-navs)/cummax).max()
    dd_date=str(pd.to_datetime(dates)[int(((cummax-navs)/cummax).argmax())])[:10]
    yrs=(pd.to_datetime(dates[-1])-pd.to_datetime(dates[0])).days/365.25
    legs=np.array(leg_hist)
    out=dict(nav=round(navs[-1],4), ann=round(navs[-1]**(1/yrs)-1,4), maxdd=round(dd,4),
             maxdd_date=dd_date, turn=round(turn_total,1), years=round(yrs,2),
             avg_legs=round(legs.mean(),2), empty=round((legs==0).mean(),3),
             p25=round(np.percentile(legs,25),1), p50=round(np.percentile(legs,50),1),
             gated=len(sec_gated))
    idx_dates=pd.to_datetime(dates); yrs_arr=idx_dates.year
    for y in sorted(set(yrs_arr)):
        m=yrs_arr==y; seg=navs[m]
        base=1.0 if m[0] else navs[np.where(m)[0][0]-1]
        out[f"y{y}_ann"]=round(seg[-1]/base-1,4)
        c2=np.maximum.accumulate(seg); out[f"y{y}_dd"]=round(((c2-seg)/c2).max(),4)
    return out

if __name__=="__main__":
    window=sys.argv[2]; label="2022起" if window=="2022-01-01" else "2025-2026"
    allres={}
    for sg in [0.4,0.6,0.8,1.0,1.2]:
        for k in [0.5,0.8,1.0,1.2,1.5]:
            for mn in [2,3]:
                allres[f"σ≥{sg} k={k} n≤{mn}"]=run(sg,k,mn)
    df=pd.DataFrame(allres).T
    cols=['ann','maxdd','nav','avg_legs','empty','turn','gated']
    print(f"=== {label} 机会驱动配对 (统一backtest_opt口径) Top18 ===")
    print(df.sort_values('ann',ascending=False)[cols].head(18).round(4).to_string())
    json.dump(allres, open(f"data/full/opp_{window[:4]}_grid.json","w"), ensure_ascii=False, indent=0)
