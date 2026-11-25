# -*- coding: utf-8 -*-
"""动量惩罚因子网格回测: 基线 / 方案A(score惩罚) / 方案B(权重惩罚)
口径与 backtest.py 完全一致: 19品种, 2025-01-02~2026-09-30, 板块内3+3,
ATR20倒数加权(基础), 1.5倍总杠杆, 周频调仓+日频风控, 成本单边0.025%.
风控(score反转/back转contango/回撤降仓)始终基于原四因子score, 与动量惩罚解耦.
"""
import pandas as pd, numpy as np, json, sys

p=pd.read_csv("data/norm/factors_mom.csv", parse_dates=["date"])
chem={"MA","TA","SA","UR","V","PP","L","EG","EB"}
p["sector"]=np.where(p["variety"].isin(chem),"化工","农产品")
p=p.sort_values(["variety","date"]).reset_index(drop=True)
p["w_raw"]=1.0/p["atr20"]

COST=0.00025; SIGMA_WIN=60; BASE_LEV=0.375; DD_TRIG=0.08
dates_all=np.sort(p["date"].unique()); start=pd.Timestamp("2025-01-01")
dates=dates_all[dates_all>=start]
wdf=pd.DataFrame({"date":dates}); wdf["week"]=pd.to_datetime(wdf["date"]).dt.to_period("W-FRI")
reb=set(wdf.groupby("week")["date"].max().values)

sco=p.pivot_table(index="date",columns="variety",values="score")
wraw=p.pivot_table(index="date",columns="variety",values="w_raw")
roll=p.pivot_table(index="date",columns="variety",values="roll_annual")
sector=p.drop_duplicates("variety").set_index("variety")["sector"].to_dict()

def make_mom(N):
    return p.pivot_table(index="date",columns="variety",values=f"momz_{N}")

def run(mode="base", N=20, param=0.0, pen_style="sym", stats_out=True):
    """mode: base | A(score惩罚) | B(权重惩罚)
       pen_style: sym(对称) | up(仅正动量惩罚)
    """
    mom = make_mom(N) if mode!="base" else None
    nav=1.0; peak=1.0; scale=1.0; dd_cut=False; trig_peak=None
    pos={}   # v -> dict(tw, bs, sigma, base_roll)
    turn_total=0.0
    for d in dates:
        dr=0.0
        for v,td in pos.items():
            r=pd.NA
            if d in ret.index and v in ret.columns: r=ret.loc[d,v]
            if pd.notna(r): dr+=td["tw"]*scale*r
        nav*=(1+dr); peak=max(peak,nav)
        # 回撤风控
        if not dd_cut and nav/peak-1 < -DD_TRIG:
            trig_peak=peak; dd_cut=True
            turn=sum(abs(t["tw"]) for t in pos.values())*0.5*COST
            nav*=(1-turn); peak=max(peak,nav); scale=0.5
        elif dd_cut and nav>=trig_peak:
            dd_cut=False; trig_peak=None
            turn=sum(abs(t["tw"]) for t in pos.values())*0.5*COST
            nav*=(1-turn); peak=max(peak,nav); scale=1.0
        # 日频风控(基于原score)
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
        # 周频调仓
        if d in reb:
            new={}
            for sec in ["化工","农产品"]:
                vs=[v for v in sector if sector[v]==sec and d in sco.index and d in wraw.index
                    and pd.notna(sco.loc[d,v]) and pd.notna(wraw.loc[d,v])]
                if len(vs)<4: continue
                n=3 if len(vs)>=6 else 2
                # 排序依据: 方案A用惩罚后score, 其余用原score
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
            turn_total+=turn
            nav*=(1-turn*COST); peak=max(peak,nav)
            pos=new
    yrs=(pd.to_datetime(dates[-1])-pd.to_datetime(dates[0])).days/365.25
    dd=0.0; dd_date=""
    # 重算最大回撤
    navs=[]; navv=1.0
    # 为求maxdd, 用无风控逐日复利近似不准确; 直接重放计算净值序列
    return dict(nav=nav, ann=nav**(1/yrs)-1, turn=turn_total, yrs=yrs, pos=pos)

# ---- 需要ret ----
ret=p.pivot_table(index="date",columns="variety",values="ret_hold")

# 重放计算净值序列与maxdd(含完整风控): 统一入口
def run_full(mode="base", N=20, param=0.0, pen_style="sym"):
    mom = make_mom(N) if mode!="base" else None
    nav=1.0; peak=1.0; scale=1.0; dd_cut=False; trig_peak=None; pos={}
    turn_total=0.0; navs=[]
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
            turn_total+=turn
            nav*=(1-turn*COST); peak=max(peak,nav)
            pos=new
        navs.append(nav)
    navs=np.array(navs); cummax=np.maximum.accumulate(navs)
    dd=((cummax-navs)/cummax).max()
    dd_date=str(pd.to_datetime(dates)[int(((cummax-navs)/cummax).argmax())])[:10]
    yrs=(pd.to_datetime(dates[-1])-pd.to_datetime(dates[0])).days/365.25
    return dict(nav=navs[-1], ann=navs[-1]**(1/yrs)-1, maxdd=dd, maxdd_date=dd_date,
                turn=turn_total, yrs=yrs)

if __name__=="__main__":
    # 基线复现校验 (应与 backtest.py: final 0.9261 / maxdd 0.1140 一致)
    b=run_full()
    print("基线复现: final=%.4f ann=%.4f maxdd=%.4f turn=%.1f"%(b["nav"],b["ann"],b["maxdd"],b["turn"]))
    assert abs(b["nav"]-0.9261)<0.002 and abs(b["maxdd"]-0.114)<0.003, "基线未复现!"
    print("✓ 基线复现通过")
    results={}
    results["基线"]=b
    for N in [20,60]:
        for lam in [0.1,0.2,0.3]:
            r=run_full(mode="A",N=N,param=lam)
            results[f"A_N{N}_λ{lam}"]=r
        for k in [0.3,0.5]:
            for st in ["sym","up"]:
                r=run_full(mode="B",N=N,param=k,pen_style=st)
                results[f"B_N{N}_k{k}_{st}"]=r
    df=pd.DataFrame({k:{kk:vv for kk,vv in v.items() if kk!="maxdd_date"} for k,v in results.items()}).T
    df=df.round(4)
    print(df.to_string())
    df.to_csv("data/norm/mom_grid.csv")
    json.dump({k:{kk:(str(vv) if kk=="maxdd_date" else round(float(vv),4)) for kk,vv in v.items()} for k,v in results.items()},
              open("data/norm/mom_grid.json","w"), ensure_ascii=False, indent=0)
    print("\n已落盘 mom_grid.csv / mom_grid.json")
