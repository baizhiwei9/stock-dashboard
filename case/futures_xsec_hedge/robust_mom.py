# -*- coding: utf-8 -*-
"""稳健性: A_N20 λ=0.75 的分年收益/品种归因/回撤区间行情 对比基线"""
import pandas as pd, numpy as np
exec(open("backtest_mom.py").read().split('if __name__')[0])

def nav_series(mode="base",N=20,param=0.0,pen_style="sym"):
    mom = make_mom(N) if mode!="base" else None
    nav=1.0; peak=1.0; scale=1.0; dd_cut=False; trig_peak=None; pos={}
    out=[]
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
                    and pd.notna(sco.loc[d,v]) and pd.notna(wraw.loc[d,v])]
                if len(vs)<4: continue
                n=3 if len(vs)>=6 else 2
                if mode=="A":
                    srt=sorted(vs, key=lambda v: sco.loc[d,v]-param*mom.loc[d,v], reverse=True)
                else:
                    srt=sorted(vs, key=lambda v: sco.loc[d,v], reverse=True)
                for grp,sgn in [(srt[:n],1.0),(srt[-n:],-1.0)]:
                    ws=pd.Series({v:wraw.loc[d,v] for v in grp})
                    if ws.sum()==0: continue
                    if mode=="B" and param>0:
                        mz=mom.loc[d,grp]
                        if pen_style=="up":
                            pen=mz.clip(lower=0).apply(lambda x: max(0.05,1-param*x))
                        else:
                            pen=(1-param*mz).clip(lower=0.05)
                        ws=ws*pen
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
            nav*=(1-turn*COST); peak=max(peak,nav)
            pos=new
        out.append((d,nav))
    return pd.DataFrame(out,columns=["date","nav"]).set_index("date")["nav"]

base=nav_series("base"); adj=nav_series("A",20,0.75)
for name,s in [("基线",base),("A_N20_λ0.75",adj)]:
    yr=s.groupby(s.index.year).last()
    print("%s: 2025 = %+.2f%%  2026YTD = %+.2f%%"%(name,(yr.iloc[0]/1-1)*100,(yr.iloc[-1]/yr.iloc[0]-1)*100))
# 最大回撤区间
for name,s in [("基线",base),("A_N20_λ0.75",adj)]:
    cm=np.maximum.accumulate(s.values); dd=(cm-s.values)/cm
    i=dd.argmax(); t=s.index[i]
    # 回撤起点的前一个峰值
    pk=cm[i]; pkd=s.index[np.argmax(s.values[:i+1]>=pk)]
    print("%s 最大回撤 %.2f%% @ %s (峰值日 %s)"%(name,dd[i]*100,str(t)[:10],str(pkd)[:10]))
# 品种持仓频率与胜率: A_N20_λ0.75 的持仓(周频截面)
import collections
poscnt=collections.Counter()
mom = make_mom(20)
# 重放一次收集持仓
for d in dates:
    if d not in reb: continue
    for sec in ["化工","农产品"]:
        vs=[v for v in sector if sector[v]==sec and d in sco.index and d in wraw.index
            and pd.notna(sco.loc[d,v]) and pd.notna(wraw.loc[d,v])]
        if len(vs)<4: continue
        n=3 if len(vs)>=6 else 2
        srt=sorted(vs, key=lambda v: sco.loc[d,v]-0.75*mom.loc[d,v], reverse=True)
        for g in [srt[:n],srt[-n:]]: 
            for v in g: poscnt[v]+=1
tot=sum(poscnt.values())
print("\nA_N20_λ0.75 持仓频率(周):")
for v,c in poscnt.most_common():
    print("  %-4s %3.0f%%"%(v,c/tot*100))
