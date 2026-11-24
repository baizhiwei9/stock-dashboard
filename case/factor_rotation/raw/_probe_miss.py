# -*- coding: utf-8 -*-
import numpy as np, pandas as pd
BASE="/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ed3f1d6b1ffeo164XKTVu7vvVA"
RAW,OUT=f"{BASE}/raw",f"{BASE}/output"
src=open(f"{RAW}/chan_final_rank.py",encoding="utf-8").read()
ns={}; exec(src[:src.index("rk = pd.read_csv")],ns)
synth=ns["synth"]; con_members=ns["con_members"]; ind_members=ns["ind_members"]; theme_members=ns["theme_members"]
cf=ns["cf"]; cc=ns["cc"]

def backshift_fix(ends,hist,plo,phi):
    ev=[]
    for i in range(3,len(ends)):
        if ends[i][1]!=ends[i-2][1]: continue
        r1a,r1b=ends[i-3][0],ends[i-2][0]; r2a,r2b=ends[i-1][0],ends[i][0]
        if r1b<=r1a or r2b<=r2a: continue
        s1=hist[r1a:r1b+1]; s2=hist[r2a:r2b+1]
        if len(s1)<2 or len(s2)<2: continue
        a1,a2=abs(float(np.sum(s1))),abs(float(np.sum(s2)))
        if a1<=1e-12: continue
        if ends[i][1]=="bottom":
            l1=float(np.min(plo[r1a:r1b+1])); l2=float(np.min(plo[r2a:r2b+1]))
            if l2<=l1 and a2<a1*0.9: ev.append((r2b,"B1"))
        else:
            h1=float(np.max(phi[r1a:r1b+1])); h2=float(np.max(phi[r2a:r2b+1]))
            if h2>=h1 and a2<a1*0.9: ev.append((r2b,"S1"))
    return ev

def detect_bs(ends,hist,plo,phi):
    ev=backshift_fix(ends,hist,plo,phi)
    res=[dict(row=r,kind=k) for r,k in ev]
    b1=set(r for r,k in ev if k=="B1"); s1=set(r for r,k in ev if k=="S1")
    lb=None
    for idx,(row,tp,px) in enumerate(ends):
        if row in b1: lb=idx
        elif tp=="bottom" and lb is not None and row not in b1:
            if any(ends[k][1]=="top" for k in range(lb+1,idx)) and px>ends[lb][2]:
                res.append(dict(row=row,kind="B2")); lb=None
    ls=None
    for idx,(row,tp,px) in enumerate(ends):
        if row in s1: ls=idx
        elif tp=="top" and ls is not None and row not in s1:
            if any(ends[k][1]=="bottom" for k in range(ls+1,idx)) and px<ends[ls][2]:
                res.append(dict(row=row,kind="S2")); ls=None
    zss=cc.detect_zhongshu(ends,cc.MIN_BI_GAP)
    seen=set()
    for s,e_,zd,zg in zss:
        left=False
        for idx in range(e_,len(ends)):
            row,tp,px=ends[idx]
            if tp=="top":
                if idx>0 and max(px,ends[idx-1][2])>zg: left=True
            elif left and tp=="bottom" and px>zg:
                if (row,"B3") not in seen: seen.add((row,"B3")); res.append(dict(row=row,kind="B3"))
                left=False
    seen=set()
    for s,e_,zd,zg in zss:
        left=False
        for idx in range(e_,len(ends)):
            row,tp,px=ends[idx]
            if tp=="bottom":
                if idx>0 and min(px,ends[idx-1][2])<zd: left=True
            elif left and tp=="top" and px<zd:
                if (row,"S3") not in seen: seen.add((row,"S3")); res.append(dict(row=row,kind="S3"))
                left=False
    res.sort(key=lambda x:x["row"])
    return res,zss

targets=[("概念","AI视频"),("概念","机器人概念"),("概念","人形机器人"),("概念","中国AI 50"),
         ("主题","AI软件与应用"),("主题","机器人与智能制造"),("主题","AI算力与芯片")]
for lv,name in targets:
    mem=(con_members.get(name,set()) if lv=="概念" else (ind_members.get(name,set()) if lv=="行业" else theme_members.get(name,set())))
    print("="*70)
    print(f"[{lv}] {name} 成分数={len(mem)}")
    if not mem:
        print("  !!! 无成分 —— 归类缺失/名称不匹配"); continue
    dfi,nm=synth(mem)
    if dfi is None: print("  !!! synth 返回 None"); continue
    df2,_=cf.normalize_input(dfi,name=name)
    ends=cf.get_endpoints(df2,"struct")
    m=cc.macd_on(df2,20,40,5)
    close=df2["close"].values; low=df2["low"].values; high=df2["high"].values
    datev=df2["time"].dt.strftime("%Y-%m-%d").values
    bs,zss=detect_bs(ends,m["hist"].values,low,high)
    n=len(close)
    print(f"  bars={n} {datev[0]}~{datev[-1]}  close[-1]={close[-1]:.2f}  60日涨幅={(close[-1]/close[-61]-1)*100:+.1f}%  20日={(close[-1]/close[-21]-1)*100:+.1f}%")
    ma10=pd.Series(close).rolling(10).mean().values; ma20=pd.Series(close).rolling(20).mean().values
    print(f"  MA10={ma10[-1]:.1f} MA20={ma20[-1]:.1f} MA10-MA20={(ma10[-1]/ma20[-1]-1)*100:+.2f}%")
    print("  最后6个笔端点:")
    for e in ends[-6:]:
        print(f"    row={e[0]} {e[1]:7s} {datev[e[0]]} px={e[2]:.2f}")
    print("  中枢:", [(datev[int(s)],datev[int(e_)],round(float(zd),1),round(float(zg),1)) for s,e_,zd,zg in zss])
    print("  买点:", [(datev[int(r)],k) for r,k in bs if k[0]=="B"])
    print("  卖点:", [(datev[int(r)],k) for r,k in bs if k[0]=="S"])
