# -*- coding: utf-8 -*-
"""T2 稳健性: 最优候选(双周)的分年收益/月度收益集中度/排序改写核查"""
import pandas as pd, numpy as np, json
exec(open("backtest_opt.py").read().split('if __name__')[0])

def nav_series(N=20, lam=0.6, style="biweekly", sec_w=None, long_w=0.5, short_w=0.5):
    sec_w = sec_w or {"化工":0.5,"农产品":0.5}
    reb=reb_dates(style); mom=moms[N]
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
                    and pd.notna(sco.loc[d,v]) and pd.notna(wraw.loc[d,v])
                    and (v not in mom.columns or d not in mom.index or pd.notna(mom.loc[d,v]))]
                if len(vs)<3*2: continue
                srt=sorted(vs, key=lambda v: sco.loc[d,v]-lam*mom.loc[d,v], reverse=True)
                gL,gS=srt[:3],srt[-3:]
                lev_L=TOTAL_LEV*sec_w[sec]*long_w; lev_S=TOTAL_LEV*sec_w[sec]*short_w
                for grp,lev,sgn in [(gL,lev_L,1.0),(gS,lev_S,-1.0)]:
                    ws=pd.Series({v:wraw.loc[d,v] for v in grp})
                    if ws.sum()==0: continue
                    for v,wv in (ws/ws.sum()*lev).items():
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

# 分年 + 月度收益: 基线(周频) vs 双周
for name,style,kw in [("基线(周频)","weekly",dict()), ("双周N20λ0.6","biweekly",dict()),
                      ("双周+偏空","biweekly",dict(long_w=0.4,short_w=0.6)),
                      ("双周+化工60+偏空","biweekly",dict(sec_w={"化工":0.6,"农产品":0.4},long_w=0.4,short_w=0.6))]:
    s=nav_series(style=style, **kw)
    yr=s.groupby(s.index.year).last()
    r25=(yr.iloc[0]-1)*100; r26=(yr.iloc[-1]/yr.iloc[0]-1)*100
    m=s.resample("ME").last().pct_change().fillna(s.resample("ME").last().iloc[0]-1)*100
    print("%s: 2025=%+.1f%% 2026=%+.1f%% 最好月%+.1f%% 最差月%+.1f%% 正月占比%.0f%%"%(
        name, r25, r26, m.max(), m.min(), (m>0).mean()*100))

# 换手成本贡献: 双周 vs 周频空转成本差
print("\n换手: 周频=111.4, 双周=67.0 → 年化节省≈%.2f%%"%( (111.4-67.0)*COST/1.7413*100*1.5 ))

# 排序改写核查: 双周 λ0.6 相对原score 组合变动比例
mom=moms[20]
changed=0; total=0; repl=[]
for d in reb_dates("biweekly"):
    if d not in sco.index: continue
    for sec in ["化工","农产品"]:
        vs=[v for v in sector if sector[v]==sec and d in sco.index and pd.notna(sco.loc[d,v])]
        if len(vs)<6: continue
        base=sorted(vs,key=lambda v:sco.loc[d,v],reverse=True)
        adj=sorted(vs,key=lambda v:sco.loc[d,v]-0.6*mom.loc[d,v],reverse=True)
        bset=set(base[:3])|set(base[-3:]); aset=set(adj[:3])|set(adj[-3:])
        total+=1
        if bset!=aset: changed+=1; repl.append(len(bset-aset))
print("\n双周λ0.6 排序改写: 改写期占比%.0f%% 平均替换%.2f腿 (共%d期)"%(changed/total*100, np.mean(repl) if repl else 0, total))
