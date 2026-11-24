# -*- coding: utf-8 -*-
"""机会驱动配对回测：出现交易机会才开仓，不强制固定对数
规则：
- 调仓日（双周）对每板块评估"交易机会"：
  * 分化度：该板块当日可用品种 score 标准差 σ_sec（多空两端极值差/2 亦可）
  * 门槛：σ_sec >= sigma_gate 才认为存在足够对冲空间，否则该板块空仓
- 有机会时：多头/空头各选 |score_adj - mean| >= k*σ_sec 的品种（最多 max_n 只）
  * score_adj = score - lam*momz20（动量惩罚，沿用已验证 λ0.6）
  * 腿数动态：信号强的品种才入池，不强制凑满
- 仓位：ATR20 倒数加权，板块内多空分离后每边杠杆 0.375（名义合计 ≤1.5）
- 风控：score反转1σ减半/2σ全平、back→contango降30%、组合回撤>8%降仓50%
- 成本：换仓单边0.025%
窗口：window = 2022-01-01 起全窗口 / 2025-01-01 起对照（与既有报告同口径）
"""
import pandas as pd, numpy as np, json, sys

p = pd.read_csv(sys.argv[1] if len(sys.argv)>1 else "data/full/factors_combined.csv", parse_dates=["date"])
chem = {"MA","TA","SA","UR","V","PP","L","EG","EB"}
p["sector"] = np.where(p["variety"].isin(chem), "化工","农产品")
p = p.sort_values(["variety","date"]).reset_index(drop=True)
p["w_raw"] = 1.0/p["atr20"]

COST=0.00025; SIGMA_WIN=60; BASE_LEV=0.375; DD_TRIG=0.08
start = pd.Timestamp(sys.argv[2]) if len(sys.argv)>2 else pd.Timestamp("2022-01-01")
dates_all=np.sort(p["date"].unique()); dates=dates_all[dates_all>=start]
wdf=pd.DataFrame({"date":dates}); wdf["biweek"]=pd.to_datetime(wdf["date"]).dt.to_period("2W-FRI")
reb=set(wdf.groupby("biweek")["date"].max().values)

ret=p.pivot_table(index="date",columns="variety",values="ret_hold")
sco=p.pivot_table(index="date",columns="variety",values="score")
wraw=p.pivot_table(index="date",columns="variety",values="w_raw")
roll=p.pivot_table(index="date",columns="variety",values="roll_annual")
mom=p.pivot_table(index="date",columns="variety",values="momz_20")
sector=p.drop_duplicates("variety").set_index("variety")["sector"].to_dict()

def run(sigma_gate=0.7, k=0.5, max_n=3, lam=0.6, with_risk=True):
    nav=1.0; peak=1.0; scale=1.0; dd_cut=False; trig_peak=None; pos={}
    turn_total=0.0; navs=[]; active={}; leg_hist=[]
    for d in dates:
        dr=0.0
        for v,td in pos.items():
            r=ret.loc[d,v] if (d in ret.index and v in ret.columns) else np.nan
            if pd.notna(r): dr+=td["tw"]*scale*r
        nav*=(1+dr); peak=max(peak,nav)
        # 回撤风控
        if with_risk:
            if not dd_cut and nav/peak-1 < -DD_TRIG:
                trig_peak=peak; dd_cut=True
                turn=sum(abs(t["tw"]) for t in pos.values())*0.5*COST
                nav*=(1-turn); peak=max(peak,nav); scale=0.5
            elif dd_cut and nav>=trig_peak:
                dd_cut=False; trig_peak=None
                turn=sum(abs(t["tw"]) for t in pos.values())*0.5*COST
                nav*=(1-turn); peak=max(peak,nav); scale=1.0
            # 日频风控
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
        # 调仓日：机会驱动选腿
        if d in reb:
            new={}
            sec_active={}
            for sec in ["化工","农产品"]:
                vs=[v for v in sector if sector[v]==sec and d in sco.index and d in wraw.index
                    and pd.notna(sco.loc[d,v]) and pd.notna(wraw.loc[d,v])
                    and (d not in mom.index or pd.notna(mom.loc[d,v]))]
                if len(vs) < 4: continue
                sc_adj={v: sco.loc[d,v]-lam*mom.loc[d,v] for v in vs}
                vals=np.array(list(sc_adj.values()))
                mu=vals.mean(); sig=vals.std(ddof=1)
                sec_active[sec] = dict(mu=mu, sig=sig, n=len(vs), legs=0)
                if pd.isna(sig) or sig < sigma_gate:   # 无交易机会 -> 空仓
                    continue
                longs=[v for v in vs if sc_adj[v]-mu >= k*sig]
                shorts=[v for v in vs if mu-sc_adj[v] >= k*sig]
                longs=sorted(longs, key=lambda v: sc_adj[v], reverse=True)[:max_n]
                shorts=sorted(shorts, key=lambda v: sc_adj[v])[:max_n]
                for grp,sgn in [(longs,1.0),(shorts,-1.0)]:
                    if not grp: continue
                    ws=pd.Series({v:wraw.loc[d,v] for v in grp})
                    if ws.sum()==0: continue
                    for v,wv in (ws/ws.sum()*BASE_LEV).items():
                        sigma=sco.loc[:d,v].tail(SIGMA_WIN).std()
                        sigma=sigma if (pd.notna(sigma) and sigma>0) else 1.0
                        new[v]=dict(tw=sgn*abs(wv), bs=sco.loc[d,v], sigma=sigma,
                                    base_roll=roll.loc[d,v] if (d in roll.index and v in roll.columns and pd.notna(roll.loc[d,v])) else 0.0)
                sec_active[sec]["legs"]=len(longs)+len(shorts)
            # 换仓成本
            turn=0.0
            for v,td in new.items():
                old=pos[v]["tw"] if v in pos else 0.0
                turn+=abs(td["tw"]-old)
            for v in pos:
                if v not in new: turn+=abs(pos[v]["tw"])
            turn_total+=turn
            nav*=(1-turn*COST); peak=max(peak,nav)
            pos=new
            active[d]=sec_active
        navs.append(nav)
        leg_hist.append((d, sum(1 for t in pos.values() if t["tw"]!=0)))
    navs=np.array(navs); cummax=np.maximum.accumulate(navs)
    dd=((cummax-navs)/cummax).max()
    dd_date=str(pd.to_datetime(dates)[int(((cummax-navs)/cummax).argmax())])[:10]
    yrs=(pd.to_datetime(dates[-1])-pd.to_datetime(dates[0])).days/365.25
    # 平均腿数与空仓天数
    legs=np.array([x[1] for x in leg_hist]); empty=(legs==0).mean()
    n_reb=len([1 for d in reb if d>=dates[0]])
    return dict(nav=navs[-1], ann=navs[-1]**(1/yrs)-1, maxdd=dd, maxdd_date=dd_date,
                turn=turn_total, yrs=yrs, avg_legs=legs.mean(), empty_days=empty, reb_count=n_reb)

if __name__=="__main__":
    window=sys.argv[2] if len(sys.argv)>2 else "2022-01-01"
    label="2022起" if window=="2022-01-01" else "2025-2026"
    results={}
    for sg in [0.3,0.5,0.7,0.9,1.1]:
        for k in [0.3,0.5,0.8]:
            r=run(sigma_gate=sg,k=k)
            results[f"σ≥{sg} k={k}"]=r
    df=pd.DataFrame({k:{kk:round(vv,4) if kk not in("maxdd_date",) else vv for kk,vv in v.items()} for k,v in results.items()}).T
    print(f"=== {label} 机会驱动配对 (信号: score-0.6*momz20) ===")
    print(df.to_string())
    json.dump({f"{label}":{k:{kk:(str(vv) if kk=="maxdd_date" else round(float(vv),4)) for kk,vv in v.items()} for k,v in results.items()}},
              open(f"data/full/opp_{window[:4]}.json","w"), ensure_ascii=False, indent=0)
