# -*- coding: utf-8 -*-
import sys, numpy as np, pandas as pd
BASE="/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ed3f1d6b1ffeo164XKTVu7vvVA"
RAW=f"{BASE}/raw"; OUT=f"{BASE}/output"
src=open(f"{RAW}/chan_final_rank.py",encoding="utf-8").read(); ns={}; exec(src[:src.index("rk = pd.read_csv")],ns)
synth,con_members,ind_members=ns["synth"],ns["con_members"],ns["ind_members"]
cf,cc=ns["cf"],ns["cc"]

# ---------- A. 实现内部量：背驰配对到底是哪两段 ----------
print("########## A. 背驰实现内部量（px_series 用的是 df2['low']）##########")
for lv,bn in [("概念","国产航母"),("概念","培育钻石"),("概念","电机")]:
    mem=con_members[bn] if lv=="概念" else ind_members[bn]
    dfi,nm=synth(mem); df2,_=cf.normalize_input(dfi,name="b")
    ends=cf.get_endpoints(df2,"struct"); h=cc.macd_on(df2,20,40,5)["hist"].values
    lowv=df2["low"].values; close=df2["close"].values
    dt=df2["time"].dt.strftime("%Y-%m-%d").values
    ev=cc.detect_backshift(ends,h,lowv,cc.MIN_BI_GAP)
    print(f"\n-- {bn} （成分{nm}） 端点末5: {[(k,ends[k][1],dt[ends[k][0]]) for k in range(len(ends)-5,len(ends))]}")
    print(f"   实现产出的背驰事件: {[(dt[e['k_idx']],e['kind']) for e in ev]}")
    i=len(ends)-1
    r1a,r1b=ends[i-2][0],ends[i-1][0]; r2a,r2b=ends[i-1][0],ends[i][0]
    a1=abs(np.sum(h[r1a:r1b+1])); a2=abs(np.sum(h[r2a:r2b+1]))
    l1=float(np.min(lowv[r1a:r1b+1])); l2=float(np.min(lowv[r2a:r2b+1]))
    if ends[i][1]=="bottom":
        print(f"   实现比较: seg1 {dt[r1a]}({ends[i-2][1]})->{dt[r1b]}({ends[i-1][1]}) 面积{a1:.1f} 最低{l1:.2f} | "
              f"seg2 {dt[r2a]}({ends[i-1][1]})->{dt[r2b]}({ends[i][1]}) 面积{a2:.1f} 最低{l2:.2f} | 新低{l2<=l1} 弱10%{a2<a1*0.9}")
        if len(ends)>=4:
            p1a,p1b=ends[i-3][0],ends[i-2][0]; p2a,p2b=ends[i-1][0],ends[i][0]
            b1=abs(np.sum(h[p1a:p1b+1])); b2=abs(np.sum(h[p2a:p2b+1]))
            q1=float(np.min(lowv[p1a:p1b+1])); q2=float(np.min(lowv[p2a:p2b+1]))
            print(f"   标准同向下跌笔: 前跌笔 {dt[p1a]}->{dt[p1b]} 面积{b1:.1f} 最低{q1:.2f} | 本跌笔 {dt[p2a]}->{dt[p2b]} 面积{b2:.1f} 最低{q2:.2f} | "
                  f"新低{q2<=q1} 弱10%{b2<b1*0.9}")

# ---------- B. 点名板块末端日线明细 ----------
print("\n########## B. 点名板块末端日线（合成等权）##########")
for lv,bn in [("概念","国产航母"),("概念","培育钻石"),("概念","电机"),("行业","银行"),("行业","玻璃"),("行业","航空"),("行业","半导体")]:
    mem=con_members[bn] if lv=="概念" else ind_members[bn]
    dfi,nm=synth(mem); df2,_=cf.normalize_input(dfi,name="b")
    c=df2["close"].values; dt=df2["time"].dt.strftime("%Y-%m-%d").values
    ma10=pd.Series(c).rolling(10).mean().values; ma20=pd.Series(c).rolling(20).mean().values
    tail=pd.DataFrame({"date":dt[-12:],"close":np.round(c[-12:],2),
                       "pct":[round((c[t]/c[t-1]-1)*100,2) for t in range(len(c)-12,len(c))],
                       "MA10":np.round(ma10[-12:],2),"MA20":np.round(ma20[-12:],2)})
    print(f"\n-- {bn}｜成分{nm}｜最新 {dt[-1]} {c[-1]:.2f}｜MA10 {ma10[-1]:.2f} MA20 {ma20[-1]:.2f} 差 {(ma10[-1]/ma20[-1]-1)*100:+.2f}%")
    print(tail.to_string(index=False))

# ---------- C. 真实同花顺指数 vs 合成 ----------
print("\n########## C. 真实同花顺指数 vs 等权合成（20261020-20261029）##########")
real=pd.read_csv(f"{RAW}/_real_idx2.csv"); real["trade_date"]=real.trade_date.astype(int)
name2code={"国产航母":"885795.TI","培育钻石":"885937.TI","电机":"881277.TI","AI视频":"886068.TI","银行":"881155.TI","半导体":"881121.TI","煤炭开采":"884014.TI"}
for bn,code in name2code.items():
    if code not in set(real.ts_code): continue
    r=real[real.ts_code==code].sort_values("trade_date")
    r=r[r.trade_date>=20261020]
    rr=r[["trade_date","close","pct_change"]].copy()
    rr["synth_pct"]=np.nan
    mem=(con_members.get(bn) or ind_members.get(bn) or set())
    dfi,nm=synth(mem)
    if dfi is not None:
        d2,_=cf.normalize_input(dfi,name="b")
        dt=[int(x.replace("-","")) for x in d2["time"].dt.strftime("%Y-%m-%d")]
        cl=d2["close"].values
        mp={dt[t]:round((cl[t]/cl[t-1]-1)*100,2) for t in range(1,len(cl))}
        rr["synth_pct"]=[mp.get(int(d),None) for d in r.trade_date]
    print(f"\n-- {bn} ({code}, 真实成分{int(r['count'].iloc[0]) if 'count' in r else '?'} / 合成池{nm})")
    print(rr.to_string(index=False))
