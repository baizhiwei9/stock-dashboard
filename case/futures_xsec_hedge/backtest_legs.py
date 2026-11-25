# -*- coding: utf-8 -*-
"""backtest_legs.py — 腿数加宽引擎: 支持板块内各自腿数(满池) 与 跨板块 TopK/BottomK
在 backtest_opt 数据管线上扩展两种选腿口径:
  1) legs_mode="sector": 板块内独立排序, 化工取 n_chem 多+n_chem 空, 农产品取 n_agr 多+n_agr 空 (守板块隔离红线)
  2) legs_mode="cross": 全市场按 score_adj 排序, TopK 多 + BottomK 空 (总杠杆分配 多0.75/空0.75, 与板块内口径可比)
与 backtest_opt 保持: 四因子score、N20动量惩罚λ、ATR20倒数加权、总名义杠杆≤1.5、四类风控、0.025%单边成本。
"""
import numpy as np, pandas as pd, json
from backtest_opt import (dates, sco, wraw, roll, ret, sector, moms, reb_dates,
                          COST, SIGMA_WIN, TOTAL_LEV, DD_TRIG)

def pick_legs(d, lam, mom, sec_legs=None, legs_mode="sector", k=10):
    """返回 {variety: +1(多)/-1(空)} 选腿集合(不含权重)。
    legs_mode="sector": sec_legs={化工:n, 农产品:m}; legs_mode="cross": 全局TopK/BottomK。
    跨板块时若品种不足 2k, 重叠席位保留双向(自对冲即可, 选腿标记为 None 由调用方合并)。"""
    if legs_mode == "sector":
        out={}
        for sec, n in (sec_legs or {"化工":3,"农产品":3}).items():
            vs=[v for v in sector if sector[v]==sec and d in sco.index and d in wraw.index
                and pd.notna(sco.loc[d,v]) and pd.notna(wraw.loc[d,v])
                and (v not in mom.columns or d not in mom.index or pd.notna(mom.loc[d,v]))]
            if len(vs) < n*2: continue
            srt=sorted(vs, key=lambda v: sco.loc[d,v]-lam*mom.loc[d,v], reverse=True)
            for v in srt[:n]: out[v]=1.0
            for v in srt[-n:]: out[v]=-1.0
        return out
    else:  # cross
        vs=[v for v in sector if d in sco.index and d in wraw.index
            and pd.notna(sco.loc[d,v]) and pd.notna(wraw.loc[d,v])
            and (v not in mom.columns or d not in mom.index or pd.notna(mom.loc[d,v]))]
        # 19品种: TopK多+BottomK空, 席位重叠的第K名净中性化(多空抵消), 至少需 K+1 只
        if len(vs) < k+1: return {}
        srt=sorted(vs, key=lambda v: sco.loc[d,v]-lam*mom.loc[d,v], reverse=True)
        out={}
        for v in srt[:k]: out[v]=out.get(v,0.0)+1.0
        for v in srt[-k:]: out[v]=out.get(v,0.0)-1.0
        return {v:s for v,s in out.items() if s!=0}

def run_legs(N=20, lam=0.6, style="biweekly", legs_mode="sector", sec_legs=None, k=10,
             long_w=0.5, short_w=0.5, sec_w=None, with_risk=True, return_navs=False):
    """通用回测。口径说明:
    - sector 模式: 每板块每边杠杆 = TOTAL_LEV*sec_w[sec]*long_w/short_w (默认0.375/边, 汇总名义1.5)
    - cross 模式: 多头总杠杆 = TOTAL_LEV*long_w, 空头总杠杆 = TOTAL_LEV*short_w (默认0.75/0.75, 汇总1.5)
    """
    sec_w = sec_w or {"化工":0.5,"农产品":0.5}
    reb=reb_dates(style); mom=moms[N]
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
        # 日频风控
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
            legs=pick_legs(d, lam, mom, sec_legs=sec_legs, legs_mode=legs_mode, k=k)
            new={}
            if not legs:
                pos={}; navs.append(nav); continue
            # 计算每个品种方向与杠杆预算(先按品种归纳多空)
            long_v=[v for v,s in legs.items() if s>0]
            short_v=[v for v,s in legs.items() if s<0]
            if legs_mode=="sector":
                for sec in ["化工","农产品"]:
                    lv=[v for v in long_v if sector[v]==sec]; sv=[v for v in short_v if sector[v]==sec]
                    lev_L=TOTAL_LEV*sec_w[sec]*long_w; lev_S=TOTAL_LEV*sec_w[sec]*short_w
                    for grp,lev,sgn in [(lv,lev_L,1.0),(sv,lev_S,-1.0)]:
                        if not grp: continue
                        ws=pd.Series({v:wraw.loc[d,v] for v in grp})
                        if ws.sum()==0: continue
                        for v,wv in (ws/ws.sum()*lev).items():
                            sigma=sco.loc[:d,v].tail(SIGMA_WIN).std()
                            sigma=sigma if(pd.notna(sigma) and sigma>0) else 1.0
                            new[v]=dict(tw=sgn*abs(wv), bs=sco.loc[d,v], sigma=sigma,
                                        base_roll=roll.loc[d,v] if(d in roll.index and v in roll.columns and pd.notna(roll.loc[d,v])) else 0.0)
            else:
                lev_L=TOTAL_LEV*long_w; lev_S=TOTAL_LEV*short_w
                for grp,lev,sgn in [(long_v,lev_L,1.0),(short_v,lev_S,-1.0)]:
                    if not grp: continue
                    ws=pd.Series({v:wraw.loc[d,v] for v in grp})
                    if ws.sum()==0: continue
                    for v,wv in (ws/ws.sum()*lev).items():
                        sigma=sco.loc[:d,v].tail(SIGMA_WIN).std()
                        sigma=sigma if(pd.notna(sigma) and sigma>0) else 1.0
                        new[v]=dict(tw=sgn*abs(wv), bs=sco.loc[d,v], sigma=sigma,
                                    base_roll=roll.loc[d,v] if(d in roll.index and v in roll.columns and pd.notna(roll.loc[d,v])) else 0.0)
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
    out=dict(nav=float(navs[-1]), ann=float(navs[-1]**(1/yrs)-1), maxdd=float(dd),
             maxdd_date=dd_date, turn=float(turn_total), yrs=yrs)
    if return_navs:
        out["navs"]=navs.tolist()
        out["nav_dates"]=[str(x)[:10] for x in dates]
    return out

def avg_legs_history(lam=0.6, style="biweekly", legs_mode="sector", sec_legs=None, k=10):
    """统计各调仓期的选腿数与多空腿数(用于报告)"""
    mom=moms[20]; reb=reb_dates(style); nl=[]; nlong=[]; nshort=[]
    for d in reb:
        if d not in sco.index: continue
        legs=pick_legs(d, lam, mom, sec_legs=sec_legs, legs_mode=legs_mode, k=k)
        if not legs: continue
        nl.append(len(legs)); nlong.append(sum(1 for s in legs.values() if s>0))
        nshort.append(sum(1 for s in legs.values() if s<0))
    return dict(avg_legs=np.mean(nl), avg_long=np.mean(nlong), avg_short=np.mean(nshort),
                n_periods=len(nl))

if __name__=="__main__":
    # 复现检查: 双周 3+3 板块内 = 1.2662/+14.52%/4.44%
    b=run_legs(N=20, lam=0.6, style="biweekly", legs_mode="sector", sec_legs={"化工":3,"农产品":3})
    print("复现 双周3+3: nav=%.4f ann=%.4f maxdd=%.4f@%s turn=%.1f"%(b["nav"],b["ann"],b["maxdd"],b["maxdd_date"],b["turn"]))
    assert abs(b["nav"]-1.2662)<0.004 and abs(b["maxdd"]-0.0444)<0.003, "3+3基线未复现!"
    print("✓ 1.2662/0.1452/0.0444 复现通过")