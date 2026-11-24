# -*- coding: utf-8 -*-
"""在 chan_scan.py 判读结果基础上，增加"买点是否仍有效"校验，重出三层名单。
有效性定义（日线）：
  三买 B3：买点后未跌回中枢 —— 买点日后任一日收盘 > 该买点所突破中枢的上沿 ZG
  一/二买 B1/B2：买点后未破买点低点 —— 买点日后任一日收盘 > 买点当日最低价
失效即"该买点已被打掉"，不再计为当前机会。
"""
import os, sys, json
import numpy as np, pandas as pd

BASE = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ed3f1d6b1ffeo164XKTVu7vvVA"
RAW = f"{BASE}/raw"; OUT = f"{BASE}/output"
CHAN = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/skills/user/chan-fenxing-ban"
sys.path.insert(0, CHAN)
import chan_fractal as cf, chan_core as cc

WINDOW = 40

# ---- 板块->成员 ----
tm = pd.read_csv(f"{RAW}/theme_member_all.csv", dtype=str)
tm2 = pd.read_csv(f"{RAW}/theme_member_ai.csv", dtype=str)
tmall = pd.concat([tm[["board_name","con_code"]], tm2[["board_name","con_code"]]], ignore_index=True)
A = lambda cs: {c for c in cs if isinstance(c,str) and c.split(".")[-1] in ("SZ","SH","BJ")}
con_members = {bn: A(set(g.con_code)) for bn,g in tmall.groupby("board_name")}
sb = pd.read_csv(f"{RAW}/stock_basic.csv", dtype=str)
ind_members = {k: set(g.ts_code) for k,g in sb.groupby("industry")}
src = open(f"{RAW}/final_pick_fullA.py", encoding="utf-8").read()
seg = src[src.index("THEME_MAP = {"):src.index("# ---------- 数据 ----------")]
ns = {}; exec(seg, ns); THEME_MAP = ns["THEME_MAP"]
theme_members = {th: set().union(*[con_members.get(c,set()) for c in cs]) for th,cs in THEME_MAP.items()}

# ---- 日线 ----
cols = ["ts_code","trade_date","open","high","low","close","pre_close","pct_chg"]
d = pd.concat([pd.read_csv(f"{RAW}/daily_260d.csv", usecols=cols),
               pd.read_csv(f"{RAW}/daily_20261029.csv", usecols=cols)], ignore_index=True)
d["trade_date"] = d.trade_date.astype(int)
d = d.drop_duplicates(["ts_code","trade_date"])
dates = sorted(d.trade_date.unique())
d["op_r"]=d.open/d.pre_close-1; d["hi_r"]=d.high/d.pre_close-1
d["lo_r"]=d.low/d.pre_close-1; d["cl_r"]=d.pct_chg/100
def _lim(c):
    s=c.split(".")[-1]
    return 0.30 if s=="BJ" else (0.20 if c[:3] in ("300","301","688","689") else 0.10)
lm={c:_lim(c) for c in d.ts_code.unique()}
for r in ["op_r","hi_r","lo_r","cl_r"]:
    d[r]=np.clip(d[r], d.ts_code.map(lambda c:-lm[c]), d.ts_code.map(lambda c:lm[c]))
P={r:d.pivot_table(index="trade_date",columns="ts_code",values=r).reindex(dates) for r in ["op_r","hi_r","lo_r","cl_r"]}

def synth(members):
    mc=[c for c in members if c in P["cl_r"].columns]
    if len(mc)<5: return None,len(mc)
    mr={k:P[k][mc].mean(axis=1,skipna=True).values for k in ["op_r","hi_r","lo_r","cl_r"]}
    n=len(dates); close=np.empty(n); op=np.empty(n); hi=np.empty(n); lo=np.empty(n)
    close[0]=1000.; op[0]=hi[0]=lo[0]=1000.
    for t in range(1,n):
        f=lambda x:(0. if not np.isfinite(mr[x][t]) else mr[x][t])
        close[t]=close[t-1]*(1+f("cl_r")); op[t]=close[t-1]*(1+f("op_r"))
        hi[t]=close[t-1]*(1+f("hi_r")); lo[t]=close[t-1]*(1+f("lo_r"))
    dfi=pd.DataFrame({"time":pd.to_datetime([str(x) for x in dates],format="%Y%m%d"),
                      "open":op,"high":hi,"low":lo,"close":close})
    dfi["high"]=dfi[["high","open","close"]].max(axis=1)
    dfi["low"]=dfi[["low","open","close"]].min(axis=1)
    return dfi,len(mc)

def eval_board(dfi, base="struct"):
    df2,_=cf.normalize_input(dfi,name="board")
    ends=cf.get_endpoints(df2,base)
    close=df2["close"].values; low=df2["low"].values; n=len(close)
    zss=cc.detect_zhongshu(ends,cc.MIN_BI_GAP)
    m=cc.macd_on(df2,20,40,5)
    ev=cc.detect_buy_sell(ends,m["hist"].values,close,cc.MIN_BI_GAP)
    buys=sorted([e for e in ev if e["sig"]==1 and e["kind"] in ("B1","B2","B3")], key=lambda x:x["row"])
    zs_last=max(zss,key=lambda z:(z[1],z[0])) if zss else None
    zg=float(zs_last[3]) if zs_last is not None else None
    out={}
    cand_recent=[b for b in buys if (n-1-b["row"])<=WINDOW]
    for tag,sel in [("latest", buys[-1] if buys else None),
                    ("recent", cand_recent[-1] if cand_recent else None)]:
        if sel is None:
            out[tag]=None; continue
        r0=sel["row"]; kind=sel["kind"]
        seg_low=low[r0:]; seg_close=close[r0:]
        after_close=close[r0+1:] if r0+1<n else np.array([])
        if kind=="B3":
            invalid = bool(after_close.size and (after_close<=zg).any()) if zg is not None else bool(seg_close.min()<low[r0]*0.98)
            inv_desc="跌回中枢内(收盘≤ZG)" if invalid else ""
        else:
            invalid = bool((seg_low[1:] < low[r0]).any())
            inv_desc="跌破买点低点" if invalid else ""
        out[tag]={"kind":kind,"date":str(df2["time"].iloc[r0].date()),
                  "ago":int(n-1-r0),"row":r0,"px":round(float(close[r0]),2),
                  "low":round(float(low[r0]),2),"valid":(not invalid),"inv":inv_desc}
    out["n_bi"]=len(ends); out["n_zs"]=len(zss); out["zg"]=round(zg,2) if zg is not None else None
    out["last_close"]=round(float(close[-1]),2)
    return out

rk = pd.read_csv(f"{OUT}/nonneg_layer_rank.csv")
rows=[]
for _,row in rk.iterrows():
    lv,name,rank,score = row["层级"],row["名称"],int(row["名次"]),float(row["得分"])
    mem = con_members.get(name,set()) if lv=="概念" else (ind_members.get(name,set()) if lv=="行业" else theme_members.get(name,set()))
    dfi,nm = synth(mem)
    if dfi is None:
        rows.append({"层级":lv,"名称":name,"因子名次":rank,"因子得分":score,"成分池内数":nm,"状态":"成分不足"})
        continue
    r = eval_board(dfi)
    def pk(t):
        v=r[t]
        return {"bt":v["kind"],"bd":v["date"],"bago":v["ago"],"bp":v["px"],"blow":v["low"],
                "valid":v["valid"],"inv":v["inv"]} if v else {"bt":None,"bd":None,"bago":None,"bp":None,"blow":None,"valid":None,"inv":""}
    rr={"层级":lv,"名称":name,"因子名次":rank,"因子得分":score,"成分池内数":nm,"状态":"ok",
        "笔数":r["n_bi"],"中枢数":r["n_zs"],"中枢上沿ZG":r["zg"],"最新收盘":r["last_close"]}
    for tag in ["latest","recent"]:
        p=pk(tag)
        pre="最新买点" if tag=="latest" else "近40日买点"
        rr[pre+"类型"]=p["bt"]; rr[pre+"日期"]=p["bd"]; rr[pre+"距今天"]=p["bago"]
        rr[pre+"价位"]=p["bp"]; rr[pre+"低点"]=p["blow"]; rr[pre+"仍有效"]=p["valid"]; rr[pre+"失效原因"]=p["inv"]
    rows.append(rr)
out=pd.DataFrame(rows)
out.to_csv(f"{OUT}/chan_layer_scan_v2.csv", index=False, encoding="utf-8-sig")
print("[out]", f"{OUT}/chan_layer_scan_v2.csv", out.shape)

# 结论计数：仅"有效且新鲜"的机会
sr = out[out["状态"]=="ok"].copy()
for lv in ["行业","主题","概念"]:
    s=sr[sr["层级"]==lv]
    n_recent=s["近40日买点类型"].notna()
    n_recent_valid=n_recent & (s["近40日买点仍有效"]==True)
    print(f"== {lv} n={len(s)} | 近40日买点 {int(n_recent.sum())} | 其中仍有效 {int(n_recent_valid.sum())} | "
          f"被失效 {int((n_recent & (s['近40日买点仍有效']==False)).sum())}")
print()
print("== 失效的近40日买点（原表被错误计入机会的） ==")
bad=sr[(sr["近40日买点类型"].notna())&(sr["近40日买点仍有效"]==False)]
print(bad[["层级","名称","因子名次","近40日买点类型","近40日买点日期","近40日买点价位","中枢上沿ZG","最新收盘","近40日买点失效原因"]].to_string(index=False))
print()
print("== 仍有效的近40日买点（真机会） ==")
good=sr[(sr["近40日买点类型"].notna())&(sr["近40日买点仍有效"]==True)].sort_values(["层级","近40日买点距今天"])
print(good[["层级","名称","因子名次","近40日买点类型","近40日买点日期","近40日买点距今天","近40日买点价位","中枢上沿ZG","最新收盘"]].to_string(index=False))