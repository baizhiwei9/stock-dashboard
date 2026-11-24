# -*- coding: utf-8 -*-
"""请求 35 之二：把背驰配对改为『两段同向笔』（修正实现与文档不一致）后，再叠加向上确认。"""
import sys, json
import numpy as np, pandas as pd
BASE="/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ed3f1d6b1ffeo164XKTVu7vvVA"
RAW=f"{BASE}/raw"; OUT=f"{BASE}/output"
src=open(f"{RAW}/chan_final_rank.py",encoding="utf-8").read(); ns={}; exec(src[:src.index("rk = pd.read_csv")],ns)
synth,con_members,ind_members,theme_members=ns["synth"],ns["con_members"],ns["ind_members"],ns["theme_members"]
cf,cc,WINDOW=ns["cf"],ns["cc"],ns["WINDOW"]

def backshift_fix(ends,macd_hist,px_series,min_seg):
    """标准口径：相邻两段【同向笔】比较（底 i：ends[i-3]->ends[i-2] 与 ends[i-1]->ends[i]）。"""
    ev=[]
    for i in range(3,len(ends)):
        if ends[i][1]!=ends[i-2][1]: continue
        r1a,r1b=ends[i-3][0],ends[i-2][0]; r2a,r2b=ends[i-1][0],ends[i][0]
        if r1b<=r1a or r2b<=r2a: continue
        s1=macd_hist[r1a:r1b+1]; s2=macd_hist[r2a:r2b+1]
        if len(s1)<2 or len(s2)<2: continue
        a1,a2=abs(float(np.sum(s1))),abs(float(np.sum(s2)))
        if a1<=1e-12: continue
        if ends[i][1]=="bottom":
            l1=float(np.min(px_series[r1a:r1b+1])); l2=float(np.min(px_series[r2a:r2b+1]))
            if l2<=l1 and a2<a1*0.9: ev.append({'k_idx':r2b,'type':'bottom','price':ends[i][2],'signal':1})
        else:
            h1=float(np.max(px_series[r1a:r1b+1])); h2=float(np.max(px_series[r2a:r2b+1]))
            if h2>=h1 and a2<a1*0.9: ev.append({'k_idx':r2b,'type':'top','price':ends[i][2],'signal':-1})
    return ev

def buys_detail_fix(ends,hist,close,min_seg):
    """复用 factor 侧逻辑，但 B1 由修正版背驰给出。"""
    res=[]; bs=backshift_fix(ends,hist,close,min_seg)
    b1_rows=set(e["k_idx"] for e in bs if e["signal"]==1)
    for e in bs:
        if e["signal"]==1: res.append(dict(row=e["k_idx"],kind="B1",anchor=e["k_idx"],zg=None))
    last_b1=None
    for idx,(row,tp,px_) in enumerate(ends):
        if row in b1_rows: last_b1=idx
        elif tp=="bottom" and last_b1 is not None and row not in b1_rows:
            if any(ends[k][1]=="top" for k in range(last_b1+1,idx)) and px_>ends[last_b1][2]:
                res.append(dict(row=row,kind="B2",anchor=ends[last_b1][0],zg=None)); last_b1=None
    zss=cc.detect_zhongshu(ends,min_seg)
    zinfo=[(ends[s][0],float(zg)) for s,_,zd,zg in zss]
    seen=set()
    for s,e_,zd,zg in zss:
        left=False
        for idx in range(e_,len(ends)):
            row,tp,px_=ends[idx]
            if tp=="top":
                if idx>0 and max(px_,ends[idx-1][2])>zg: left=True
            elif left and tp=="bottom" and px_>zg:
                bc=float(close[row]); cand=[z for (sr,z) in zinfo if z<bc and sr<=row]
                az=max(cand) if cand else float(zg)
                if (row,"B3") not in seen: seen.add((row,"B3")); res.append(dict(row=row,kind="B3",anchor=row,zg=az))
                left=False
    res.sort(key=lambda x:x["row"]); return res

def judge(buy,close,low,n,datev):
    if buy is None: return None
    r0=buy["row"]; ac=close[r0+1:]
    if buy["kind"]=="B3":
        zg=buy["zg"]; inv=bool(ac.size and zg is not None and (ac<=zg).any())
        why=(f"买点后收盘跌回中枢内(≤ZG={zg:.1f})" if inv else ""); ref=round(float(zg),2) if zg is not None else None
    else:
        alo=float(low[buy["anchor"]]); inv=bool(ac.size and (ac<alo).any())
        why=(f"买点后收盘跌破买点低点({alo:.1f})" if inv else ""); ref=round(float(alo),2)
    return dict(kind=buy["kind"],date=str(datev[r0]),ago=int(n-1-r0),px=round(float(close[r0]),2),ref=ref,valid=(not inv),inv=why)

rk=pd.read_csv(f"{OUT}/nonneg_layer_rank.csv"); rows=[]
for _,r0 in rk.iterrows():
    lv,name,rk_=r0["层级"],r0["名称"],int(r0["名次"])
    mem=con_members.get(name,set()) if lv=="概念" else (ind_members.get(name,set()) if lv=="行业" else theme_members.get(name,set()))
    dfi,nm=synth(mem)
    if dfi is None: continue
    df2,_=cf.normalize_input(dfi,name="b"); ends=cf.get_endpoints(df2,"struct")
    m=cc.macd_on(df2,20,40,5); close=df2["close"].values; low=df2["low"].values; n=len(close)
    datev=df2["time"].dt.strftime("%Y-%m-%d").values
    buys=buys_detail_fix(ends,m["hist"].values,close,cc.MIN_BI_GAP)
    rec=[b for b in buys if (n-1-b["row"])<=WINDOW]
    zss=cc.detect_zhongshu(ends,cc.MIN_BI_GAP)
    zl=max(zss,key=lambda z:(z[1],z[0])) if zss else None
    zg=float(zl[3]) if zl else None
    brk=zg is not None and close[-1]>zg
    j=judge(rec[-1] if rec else None,close,low,n,datev)
    ma10=pd.Series(close).rolling(10).mean().values[-1]; ma20=pd.Series(close).rolling(20).mean().values[-1]
    k=0
    for t in range(n-1,0,-1):
        if close[t]>close[t-1]: k+=1
        else: break
    c1=bool(np.isfinite(ma10) and np.isfinite(ma20) and ma10>ma20); c2=bool(k>=3)
    c3=bool(rec and any(e[1]=="top" and e[0]>rec[-1]["row"] for e in ends))
    if j and j["valid"]: tier="A" if (c1 or c2 or c3) else "B"
    elif j: tier="D"
    elif brk: tier="C"
    else: tier="E"
    rows.append(dict(层级=lv,名称=name,因子名次=rk_,修正后档位=tier,
                     买点类型=(j["kind"] if j else None),买点日期=(j["date"] if j else None),
                     买点距今天=(j["ago"] if j else None),买点价位=(j["px"] if j else None),
                     买点关键位=(j["ref"] if j else None),仍有效=(j["valid"] if j else None),
                     最新收盘=round(float(close[-1]),2),MA10MA20差pct=round(float(ma10/ma20-1)*100,2),
                     末端连涨根数=k,C1=c1,C2=c2,C3=c3,
                     严格确认=bool(c1 or c2)))
o=pd.DataFrame(rows); o.to_csv(f"{OUT}/chan_confirm_v3_fixed_backshift.csv",index=False,encoding="utf-8-sig")
print("[out] chan_confirm_v3_fixed_backshift.csv", o.shape)
print("\n=== 修正背驰配对（两段同向笔）后分档 ===")
for lv in ["行业","主题","概念"]:
    s=o[o["层级"]==lv]; print(f"{lv}: n={len(s)}", {k:int((s['修正后档位']==k).sum()) for k in "ABCDE"})
print("\n=== 修正后 A 类 ===")
print(o[o["修正后档位"]=="A"][["层级","名称","因子名次","买点类型","买点日期","买点距今天","最新收盘","MA10MA20差pct","末端连涨根数","C1","C2","C3","严格确认"]].to_string(index=False))
print("\n=== 修正后 B 类 ===")
print(o[o["修正后档位"]=="B"][["层级","名称","因子名次","买点类型","买点日期","买点距今天","最新收盘","MA10MA20差pct","末端连涨根数","C1","C2","C3"]].to_string(index=False))
print("\n=== 修正后 C 类 ===")
print(o[o["修正后档位"]=="C"][["层级","名称","因子名次","最新收盘","MA10MA20差pct","末端连涨根数"]].to_string(index=False))
print("\n=== 修正 vs 未修正：买点类型变化（仅列变化项）===")
old=pd.read_csv(f"{OUT}/chan_confirm_v2.csv"); old=old[old["状态"]=="ok"]
mm=old.merge(o,on=["层级","名称"],how="inner")
ch=mm[(mm["近40日买点类型"].astype(str)!=mm["买点类型"].astype(str))|(mm["新档位"]!=mm["修正后档位"])]
print(ch[["层级","名称","新档位","修正后档位","近40日买点类型","近40日买点日期","买点类型","买点日期"]].to_string(index=False))
