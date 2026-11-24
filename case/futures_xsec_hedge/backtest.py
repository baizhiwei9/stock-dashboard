# -*- coding: utf-8 -*-
"""2025-2026 商品期货横截面多空对冲回测
规则: 化工/农产品板块内 多空各2-3腿(score最高/最低), ATR20倒数加权, 每板块每边名义0.375,
总杠杆1.5; 周频调仓+日频风控; 成本: 换仓单边0.025%.
风控: ①score反转1σ减半/2σ全平(相对调仓基准) ②back转contango降30% ③组合回撤>8%降仓50%(收复后恢复) ④库存证伪审查(标记)
"""
import pandas as pd, numpy as np, json

p = pd.read_csv("data/norm/factors.csv", parse_dates=["date"])
chem = {"MA","TA","SA","UR","V","PP","L","EG","EB"}
p["sector"] = np.where(p["variety"].isin(chem), "化工","农产品")
p = p.sort_values(["variety","date"]).reset_index(drop=True)
p["w_raw"] = 1.0/p["atr20"]

COST=0.00025; SIGMA_WIN=60; BASE_LEV=0.375; DD_TRIG=0.08
dates_all=np.sort(p["date"].unique()); start=pd.Timestamp("2025-01-01")
dates=dates_all[dates_all>=start]
wdf=pd.DataFrame({"date":dates}); wdf["week"]=pd.to_datetime(wdf["date"]).dt.to_period("W-FRI")
reb=set(wdf.groupby("week")["date"].max().values)

ret =p.pivot_table(index="date",columns="variety",values="ret_hold")
sco =p.pivot_table(index="date",columns="variety",values="score")
wraw=p.pivot_table(index="date",columns="variety",values="w_raw")
roll=p.pivot_table(index="date",columns="variety",values="roll_annual")
sector=p.drop_duplicates("variety").set_index("variety")["sector"].to_dict()

def run(risk_on=True, dd_control=True):
    nav=1.0; peak=1.0; scale=1.0; dd_cut=False; trig_peak=None
    pos={}   # v -> dict(tw, bs, sigma)
    hist=[]; events=[]
    for d in dates:
        # 1) 日收益
        day_ret=0.0
        for v,td in pos.items():
            r=ret.loc[d,v] if (d in ret.index and v in ret.columns) else np.nan
            if pd.notna(r): day_ret+=td["tw"]*scale*r
        nav*=(1+day_ret); peak=max(peak,nav)
        # 2) 组合回撤风控
        if dd_control:
            if not dd_cut and nav/peak-1 < -DD_TRIG:
                trig_peak=peak; dd_cut=True
                turn_cost=sum(abs(t["tw"]) for t in pos.values())*0.5*COST
                nav*=(1-turn_cost); scale=0.5; peak=max(peak,nav)
                events.append({"date":str(d)[:10],"type":"组合回撤降仓","detail":"回撤%.2f%% -> 全组合×0.5 (叠加换仓成本)"%((nav/peak-1)*100)})
            elif dd_cut and nav>=trig_peak:
                dd_cut=False; trig_peak=None
                turn_cost=sum(abs(t["tw"]) for t in pos.values())*0.5*COST
                nav*=(1-turn_cost); scale=1.0; peak=max(peak,nav)
                events.append({"date":str(d)[:10],"type":"组合回撤恢复","detail":"净值收复触发点 -> 恢复满仓 (叠加换仓成本)"})
        # 3) 日频风控(非调仓日)
        if risk_on and d not in reb:
            for v in list(pos.keys()):
                td=pos[v]
                if td["tw"]==0 or v not in sco.columns or d not in sco.index: continue
                st=sco.loc[d,v]
                if pd.isna(st): continue
                dsc=st-td["bs"]
                if td["tw"]>0:
                    if dsc < -2*td["sigma"]:  td["tw"]=0.0; events.append({"date":str(d)[:10],"type":"score2σ全平","variety":v,"detail":"Δscore=%.2f σ=%.2f"%(dsc,td["sigma"])})
                    elif dsc < -td["sigma"]:  td["tw"]*=0.5; events.append({"date":str(d)[:10],"type":"score1σ减半","variety":v,"detail":"Δscore=%.2f σ=%.2f"%(dsc,td["sigma"])})
                else:
                    if dsc > 2*td["sigma"]:  td["tw"]=0.0; events.append({"date":str(d)[:10],"type":"score2σ全平","variety":v,"detail":"Δscore=%.2f σ=%.2f"%(dsc,td["sigma"])})
                    elif dsc > td["sigma"]: td["tw"]*=0.5; events.append({"date":str(d)[:10],"type":"score1σ减半","variety":v,"detail":"Δscore=%.2f σ=%.2f"%(dsc,td["sigma"])})
                if v in roll.columns and d in roll.index and td["tw"]!=0 and td["base_roll"]>0:
                    rv=roll.loc[d,v]
                    if pd.notna(rv) and rv<0:
                        td["tw"]*=0.3; events.append({"date":str(d)[:10],"type":"back→contango降30%","variety":v,"detail":"roll %.3f→%.3f"%(td["base_roll"],rv)})
        # 4) 周频调仓
        if d in reb:
            new={}
            for sec in ["化工","农产品"]:
                vs=[v for v in sector if sector[v]==sec and d in sco.index and d in wraw.index
                    and pd.notna(sco.loc[d,v]) and pd.notna(wraw.loc[d,v])]
                if len(vs)<4: continue
                n=3 if len(vs)>=6 else 2
                srt=sorted(vs,key=lambda v:sco.loc[d,v],reverse=True)
                for grp,sgn in [(srt[:n],1.0),(srt[-n:],-1.0)]:
                    ws=pd.Series({v:wraw.loc[d,v] for v in grp})
                    if ws.sum()==0: continue
                    for v,wv in (ws/ws.sum()*BASE_LEV).items():
                        sigma=sco.loc[:d,v].tail(SIGMA_WIN).std()
                        sigma=sigma if (pd.notna(sigma) and sigma>0) else 1.0
                        new[v]=dict(tw=sgn*abs(wv), bs=sco.loc[d,v], sigma=sigma, base_roll=roll.loc[d,v] if (d in roll.index and v in roll.columns and pd.notna(roll.loc[d,v])) else 0.0)
            # 换仓成本(新旧tw差)
            turn=0.0
            for v,td in list(new.items()):
                old=pos[v]["tw"] if v in pos else 0.0
                turn+=abs(td["tw"]-old)
            for v in list(pos.keys()):
                if v not in new: turn+=abs(pos[v]["tw"])
            nav*=(1-turn*COST); peak=max(peak,nav)
            pos=new
        hist.append({"date":d,"nav":nav,"day_ret":day_ret,"lever":sum(abs(t["tw"])*scale for t in pos.values()),
                     "positions":{v:t["tw"]*scale for v,t in pos.items()}})
    return pd.DataFrame(hist),pd.DataFrame(events)

H,ev=run(True,True); Hn,evn=run(False,False)
def stats(H):
    H=H.set_index(pd.to_datetime(H["date"])); nav=H["nav"]
    dd=(nav.cummax()-nav)/nav.cummax()
    yrs=(H.index[-1]-H.index[0]).days/365.25
    return dict(final=round(nav.iloc[-1],4),ann=round(nav.iloc[-1]**(1/yrs)-1,4),maxdd=round(dd.max(),4),
                maxdd_date=str(dd.idxmax())[:10],years=round(yrs,2),min_nav=round(nav.min(),4))
s=stats(H); sn=stats(Hn)
print("=== 含风控 ==="); print(json.dumps(s,ensure_ascii=False))
print("=== 无风控 ==="); print(json.dumps(sn,ensure_ascii=False))
print("事件数",len(ev),"| 类型分布:"); print(ev.groupby("type").size().to_string())
H.to_csv("data/norm/nav_risk.csv",index=False); Hn.to_csv("data/norm/nav_norisk.csv",index=False)
ev.to_csv("data/norm/events.csv",index=False)
print("\n首月净值:"); print(H.head(25)[["date","nav","lever"]].to_string(index=False))
