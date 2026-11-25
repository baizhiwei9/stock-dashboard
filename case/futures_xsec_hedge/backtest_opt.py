# -*- coding: utf-8 -*-
"""backtest_opt.py — 多方向优化参数化回测引擎
在方案A(score_adj=score-λ*momz_N)基础上可调: 腿数(n_legs) / 调仓频率(weekly/biweekly/monthly)
/ 板块杠杆分配(sec_w) / 多空杠杆分配(long_w, short_w)。总杠杆恒≤1.5, ATR20倒数加权,
四类风控(score反转/back→contango/回撤8%降50%)保留, 成本0.025%单边, 周频调仓+日频风控。
"""
import pandas as pd, numpy as np, json, sys

p=pd.read_csv("data/norm/factors_mom.csv", parse_dates=["date"])
chem={"MA","TA","SA","UR","V","PP","L","EG","EB"}
p["sector"]=np.where(p["variety"].isin(chem),"化工","农产品")
# 扩展动量窗口 10/15 天(与 momz_20/60 同口径: 品种内rolling sum → 板块内截面z)
for N in [10,15]:
    p[f"mr_{N}"]=p.groupby("variety")["ret_hold"].transform(lambda s: s.rolling(N).sum())
    p[f"momz_{N}"]=p.groupby(["date","sector"])[f"mr_{N}"].transform(lambda x:(x-x.mean())/x.std())
    p=p.drop(columns=[f"mr_{N}"])
p=p.sort_values(["variety","date"]).reset_index(drop=True)
p["w_raw"]=1.0/p["atr20"]

COST=0.00025; SIGMA_WIN=60; TOTAL_LEV=1.5; DD_TRIG=0.08
t0=pd.Timestamp("2025-01-01")

sco=p.pivot_table(index="date",columns="variety",values="score")
wraw=p.pivot_table(index="date",columns="variety",values="w_raw")
roll=p.pivot_table(index="date",columns="variety",values="roll_annual")
ret=p.pivot_table(index="date",columns="variety",values="ret_hold")
sector=p.drop_duplicates("variety").set_index("variety")["sector"].to_dict()
moms={N:p.pivot_table(index="date",columns="variety",values=f"momz_{N}") for N in [10,15,20,60]}

dates_all=np.sort(p["date"].unique()); dates=dates_all[dates_all>=t0]
wdf=pd.DataFrame({"date":dates}); wdf["week"]=pd.to_datetime(wdf["date"]).dt.to_period("W-FRI")

def reb_dates(style="weekly"):
    if style=="weekly":
        return set(wdf.groupby("week")["date"].max().values)
    elif style=="biweekly":
        w=wdf.groupby("week")["date"].max().reset_index()
        w["bw"]=w["week"].astype(str).str[:4]+"-W"+((w["week"].dt.week-1)//2*2+1).astype(str).str.zfill(2)
        return set(w.groupby("bw")["date"].max().values)
    elif style=="monthly":
        wdf2=wdf.copy(); wdf2["mon"]=pd.to_datetime(wdf2["date"]).dt.to_period("M")
        return set(wdf2.groupby("mon")["date"].max().values)
    raise ValueError(style)

def run_opt(N=20, lam=0.6, n_legs=3, style="weekly", sec_w=None, long_w=0.5, short_w=0.5, with_risk=True):
    """sec_w: {sector: weight} 板块杠杆权重(和为1); long_w/short_w: 多空杠杆份额(和为1)
    每板块杠杆预算 = TOTAL_LEV * sec_w[板块] * (多long_w/空short_w) / 2(板块数对称分摊保留原口径)
    原口径: 每板块每边 0.375 = 1.5*0.5*0.5  → 需 ×1 修正系数, 见下"""
    global BASE
    sec_w = sec_w or {"化工":0.5,"农产品":0.5}
    reb=reb_dates(style)
    mom = moms[N]
    nav=1.0; peak=1.0; scale=1.0; dd_cut=False; trig_peak=None; pos={}
    turn_total=0.0
    for d in dates:
        dr=0.0
        for v,td in pos.items():
            r=ret.loc[d,v] if (d in ret.index and v in ret.columns) else np.nan
            if pd.notna(r): dr+=td["tw"]*scale*r
        nav*=(1+dr); peak=max(peak,nav)
        if with_risk:
            if not dd_cut and nav/peak-1 < -DD_TRIG:
                trig_peak=peak; dd_cut=True
                turn=sum(abs(t["tw"]) for t in pos.values())*0.5*COST
                nav*=(1-turn); peak=max(peak,nav); scale=0.5
            elif dd_cut and nav>=trig_peak:
                dd_cut=False; trig_peak=None
                turn=sum(abs(t["tw"]) for t in pos.values())*0.5*COST
                nav*=(1-turn); peak=max(peak,nav); scale=1.0
        # 日频风控: score反转/back→contango(基于原score)
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
                    and pd.notna(sco.loc[d,v]) and pd.notna(wraw.loc[d,v]) and pd.notna(mom.loc[d,v] if (v in mom.columns and d in mom.index) else np.nan)]
                if len(vs)<n_legs*2: continue
                srt=sorted(vs, key=lambda v: sco.loc[d,v]-lam*mom.loc[d,v], reverse=True)
                gL,gS=srt[:n_legs],srt[-n_legs:]
                # 杠杆预算: 原口径每板块每边=TOTAL_LEV*0.5(板块)*0.5(多空)=0.375
                lev_L=TOTAL_LEV*sec_w[sec]*long_w
                lev_S=TOTAL_LEV*sec_w[sec]*short_w
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
            turn_total+=turn
            nav*=(1-turn*COST); peak=max(peak,nav)
            pos=new
    navs_tmp=None
    return dict(nav=float(nav), turn=float(turn_total), pos=pos)

def run_full(N=20, lam=0.6, n_legs=3, style="weekly", sec_w=None, long_w=0.5, short_w=0.5, with_risk=True):
    sec_w = sec_w or {"化工":0.5,"农产品":0.5}
    reb=reb_dates(style)
    mom = moms[N]
    nav=1.0; peak=1.0; scale=1.0; dd_cut=False; trig_peak=None; pos={}
    turn_total=0.0; navs=[]
    for d in dates:
        dr=0.0
        for v,td in pos.items():
            r=ret.loc[d,v] if (d in ret.index and v in ret.columns) else np.nan
            if pd.notna(r): dr+=td["tw"]*scale*r
        nav*=(1+dr); peak=max(peak,nav)
        if with_risk:
            if not dd_cut and nav/peak-1 < -DD_TRIG:
                trig_peak=peak; dd_cut=True
                turn=sum(abs(t["tw"]) for t in pos.values())*0.5*COST
                nav*=(1-turn); peak=max(peak,nav); scale=0.5
            elif dd_cut and nav>=trig_peak:
                dd_cut=False; trig_peak=None
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
                if len(vs)<n_legs*2: continue
                srt=sorted(vs, key=lambda v: sco.loc[d,v]-lam*mom.loc[d,v], reverse=True)
                gL,gS=srt[:n_legs],srt[-n_legs:]
                lev_L=TOTAL_LEV*sec_w[sec]*long_w
                lev_S=TOTAL_LEV*sec_w[sec]*short_w
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
            turn_total+=turn
            nav*=(1-turn*COST); peak=max(peak,nav)
            pos=new
        navs.append(nav)
    navs=np.array(navs); cummax=np.maximum.accumulate(navs)
    dd=((cummax-navs)/cummax).max()
    dd_date=str(pd.to_datetime(dates)[int(((cummax-navs)/cummax).argmax())])[:10]
    yrs=(pd.to_datetime(dates[-1])-pd.to_datetime(dates[0])).days/365.25
    return dict(nav=float(navs[-1]), ann=float(navs[-1]**(1/yrs)-1), maxdd=float(dd),
                maxdd_date=dd_date, turn=float(turn_total), yrs=yrs)

if __name__=="__main__":
    # 基线复现: 方案A N20 λ0.6 (与 mom_grid.json 中 A_N20_λ0.6: 1.1674/0.0730/0.0930 一致)
    b=run_full(N=20, lam=0.6)
    print("复现检查 A_N20_λ0.6: nav=%.4f ann=%.4f maxdd=%.4f@%s turn=%.1f"%(b["nav"],b["ann"],b["maxdd"],b["maxdd_date"],b["turn"]))
    assert abs(b["nav"]-1.1674)<0.003 and abs(b["maxdd"]-0.0730)<0.004, "基线未复现!"
    print("✓ 方案A基线复现通过 (期望 1.1674 / 0.0930 / 0.0730)")