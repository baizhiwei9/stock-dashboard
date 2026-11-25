# -*- coding: utf-8 -*-
"""T3 校验+最终净值序列: 8%回撤风控触发核查 + 净值导出"""
import pandas as pd, numpy as np, json
exec(open("backtest_opt.py").read().split('if __name__')[0])

def nav_series(N=20, lam=0.6, style="biweekly", sec_w=None, long_w=0.5, short_w=0.5, with_risk=True, track_dd=False):
    sec_w = sec_w or {"化工":0.5,"农产品":0.5}
    reb=reb_dates(style); mom=moms[N]
    nav=1.0; peak=1.0; scale=1.0; dd_cut=False; trig_peak=None; pos={}
    out=[]; dd_trig_cnt={"组合降仓":0,"恢复":0}
    for d in dates:
        dr=0.0
        for v,td in pos.items():
            r=ret.loc[d,v] if (d in ret.index and v in ret.columns) else np.nan
            if pd.notna(r): dr+=td["tw"]*scale*r
        nav*=(1+dr); peak=max(peak,nav)
        if with_risk:
            if not dd_cut and nav/peak-1 < -DD_TRIG:
                trig_peak=peak; dd_cut=True; dd_trig_cnt["组合降仓"]+=1
                turn=sum(abs(t["tw"]) for t in pos.values())*0.5*COST
                nav*=(1-turn); peak=max(peak,nav); scale=0.5
            elif dd_cut and nav>=trig_peak:
                dd_cut=False; trig_peak=None; dd_trig_cnt["恢复"]+=1
                turn=sum(abs(t["tw"]) for t in pos.values())*0.5*COST
                nav*=(1-turn); peak=max(peak,nav); scale=1.0
        if with_risk and d not in reb:
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
    return pd.DataFrame(out,columns=["date","nav"]).set_index("date")["nav"], dd_trig_cnt

# 8% 回撤风控触发核查 (最优双周基准)
_,cnt=nav_series(style="biweekly")
print("双周基准 8%回撤降仓触发:", cnt)

# 导出4条关键净值曲线
curves={}
for name,kw in [("基线_周频_λ0.6",dict(style="weekly")),
                ("双周_λ0.6(推荐)",dict(style="biweekly")),
                ("双周_偏空",dict(style="biweekly",long_w=0.4,short_w=0.6)),
                ("双周_化工60_偏空",dict(style="biweekly",sec_w={"化工":0.6,"农产品":0.4},long_w=0.4,short_w=0.6))]:
    s,_=nav_series(**kw)
    curves[name]=s
df=pd.DataFrame(curves)
df.to_csv("data/norm/nav_opt_final.csv")
print(df.tail(2).round(4).to_string())
print("nav_opt_final.csv 已落盘,", len(df), "行")
