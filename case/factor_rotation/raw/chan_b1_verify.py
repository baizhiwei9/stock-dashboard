# -*- coding: utf-8 -*-
"""逐笔核验：被点名板块的一买(B1)在“原实现”与“修正版(两段同向下跌笔)”下是否成立。"""
import numpy as np, pandas as pd
BASE="/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ed3f1d6b1ffeo164XKTVu7vvVA"
RAW=f"{BASE}/raw"
src=open(f"{RAW}/chan_final_rank.py",encoding="utf-8").read(); ns={}; exec(src[:src.index("rk = pd.read_csv")],ns)
synth,con_members,ind_members,cf,cc=ns["synth"],ns["con_members"],ns["ind_members"],ns["cf"],ns["cc"]

def fixed_backshift(ends,h,px,min_seg):
    ev=[]
    for i in range(3,len(ends)):
        if ends[i][1]!=ends[i-2][1]: continue
        r1a,r1b=ends[i-3][0],ends[i-2][0]; r2a,r2b=ends[i-1][0],ends[i][0]
        if r1b<=r1a or r2b<=r2a: continue
        s1=h[r1a:r1b+1]; s2=h[r2a:r2b+1]
        if len(s1)<2 or len(s2)<2: continue
        a1,a2=abs(float(np.sum(s1))),abs(float(np.sum(s2)))
        if a1<=1e-12: continue
        if ends[i][1]=="bottom":
            l1=float(np.min(px[r1a:r1b+1])); l2=float(np.min(px[r2a:r2b+1]))
            if l2<=l1 and a2<a1*0.9: ev.append(("bottom",ends[i][0],l2,l1,a2,a1))
        else:
            h1=float(np.max(px[r1a:r1b+1])); h2=float(np.max(px[r2a:r2b+1]))
            if h2>=h1 and a2<a1*0.9: ev.append(("top",ends[i][0],h2,h1,a2,a1))
    return ev

for lv,bn in [("概念","国产航母"),("概念","培育钻石"),("概念","电机"),
              ("行业","石油开采"),("行业","航空"),("行业","电信运营"),("行业","矿物制品"),("行业","焦炭加工")]:
    mem=con_members[bn] if lv=="概念" else ind_members[bn]
    dfi,nm=synth(mem); df2,_=cf.normalize_input(dfi,name="b")
    ends=cf.get_endpoints(df2,"struct"); h=cc.macd_on(df2,20,40,5)["hist"].values
    low=df2["low"].values; close=df2["close"].values; dt=df2["time"].dt.strftime("%Y-%m-%d").values
    ev_old=cc.detect_backshift(ends,h,low,cc.MIN_BI_GAP)
    ev_new=fixed_backshift(ends,h,low,cc.MIN_BI_GAP)
    print(f"\n=== {lv}/{bn} 成分{nm} 最新{dt[-1]} {close[-1]:.2f} ===")
    print("  末6端点:", [(ends[k][1],dt[ends[k][0]]) for k in range(max(0,len(ends)-6),len(ends))])
    print("  原实现(chan_core)底背驰:", [(dt[e['k_idx']]) for e in ev_old if e['signal']==1])
    if ev_new:
        for tp,k,l2,l1,a2,a1 in ev_new:
            tag="底背驰→一买" if tp=="bottom" else "顶背驰→一卖"
            print(f"  修正版: 端点{dt[k]}({tp}) 本段低/高={l2:.2f} 前段低/高={l1:.2f} "
                  f"新低/新高={l2<=l1 if tp=='bottom' else l2>=l1} 本段面积={a2:.1f} 前段面积={a1:.1f} → {tag}")
    else:
        print("  修正版: 无背驰")
    # 修正版 B1 的具体两段
    for i in range(3,len(ends)):
        if ends[i][1]!="bottom" or ends[i][1]!=ends[i-2][1]: continue
        r1a,r1b=ends[i-3][0],ends[i-2][0]; r2a,r2b=ends[i-1][0],ends[i][0]
        s1=h[r1a:r1b+1]; s2=h[r2a:r2b+1]
        if r1b<=r1a or r2b<=r2a or len(s1)<2 or len(s2)<2: continue
        a1,a2=abs(float(np.sum(s1))),abs(float(np.sum(s2)))
        if a1<=1e-12: continue
        l1=float(np.min(low[r1a:r1b+1])); l2=float(np.min(low[r2a:r2b+1]))
        if l2<=l1 and a2<a1*0.9:
            print(f"  >> B1成立依据: 前跌笔 {dt[r1a]}→{dt[r1b]} 面积{a1:.1f} 低{l1:.2f} | "
                  f"本跌笔 {dt[r2a]}→{dt[r2b]} 面积{a2:.1f} 低{l2:.2f} | 新低 & 面积弱{a1/a2 if a2 else 0:.2f}倍")
            break