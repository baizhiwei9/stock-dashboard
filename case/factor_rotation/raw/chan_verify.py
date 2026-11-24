# -*- coding: utf-8 -*-
"""抽查校验：用缠论技能公开 API analyze() 对若干板块复核 scan 记录，独立于 chan_scan.py 的底层调用路径。"""
import os, sys
import numpy as np, pandas as pd

BASE = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ed3f1d6b1ffeo164XKTVu7vvVA"
RAW = f"{BASE}/raw"; OUT = f"{BASE}/output"
CHAN = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/skills/user/chan-fenxing-ban"
sys.path.insert(0, CHAN)
from chan_fractal import analyze, normalize_input
import chan_fractal as cf, chan_core as cc

scan = pd.read_csv(f"{OUT}/chan_layer_scan.csv")

# 重建板块->成员（与 chan_scan.py 同源）
tm = pd.read_csv(f"{RAW}/theme_member_all.csv", dtype=str)
tm2 = pd.read_csv(f"{RAW}/theme_member_ai.csv", dtype=str)
tmall = pd.concat([tm[["board_name","con_code"]], tm2[["board_name","con_code"]]], ignore_index=True)
A = lambda cs: {c for c in cs if isinstance(c,str) and c.split(".")[-1] in ("SZ","SH","BJ")}
con = {bn: A(set(g.con_code)) for bn,g in tmall.groupby("board_name")}
sb = pd.read_csv(f"{RAW}/stock_basic.csv", dtype=str)
ind = {k: set(g.ts_code) for k,g in sb.groupby("industry")}
src = open(f"{RAW}/final_pick_fullA.py", encoding="utf-8").read()
seg = src[src.index("THEME_MAP = {"):src.index("# ---------- 数据 ----------")]
ns = {}; exec(seg, ns); TM = ns["THEME_MAP"]
theme = {th: set().union(*[con.get(c,set()) for c in cs]) for th,cs in TM.items()}

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
    if s=="BJ": return 0.30
    if c[:3] in ("300","301","688","689"): return 0.20
    return 0.10
lm={c:_lim(c) for c in d.ts_code.unique()}
for r in ["op_r","hi_r","lo_r","cl_r"]:
    d[r]=np.clip(d[r], d.ts_code.map(lambda c:-lm[c]), d.ts_code.map(lambda c:lm[c]))
P={r:d.pivot_table(index="trade_date",columns="ts_code",values=r).reindex(dates) for r in ["op_r","hi_r","lo_r","cl_r"]}
def synth(members):
    mc=[c for c in members if c in P["cl_r"].columns]
    if len(mc)<5: return None
    mr={k:P[k][mc].mean(axis=1,skipna=True).values for k in ["op_r","hi_r","lo_r","cl_r"]}
    n=len(dates); close=np.empty(n); op=np.empty(n); hi=np.empty(n); lo=np.empty(n)
    close[0]=1000.; op[0]=hi[0]=lo[0]=1000.
    for t in range(1,n):
        f=lambda x:(0. if not np.isfinite(mr[x][t]) else mr[x][t])
        close[t]=close[t-1]*(1+f("cl_r")); op[t]=close[t-1]*(1+f("op_r"))
        hi[t]=close[t-1]*(1+f("hi_r")); lo[t]=close[t-1]*(1+f("lo_r"))
    dfi=pd.DataFrame({"time":pd.to_datetime([str(x) for x in dates],format="%Y%m%d"),
                      "open":op,"high":hi,"low":lo,"close":close})
    dfi["high"]=dfi[["high","open","close"]].max(axis=1); dfi["low"]=dfi[["low","open","close"]].min(axis=1)
    return dfi

CHECK = [("行业","银行"), ("行业","半导体"), ("概念","电机"), ("概念","培育钻石"),
         ("主题","AI算力与芯片"), ("行业","铁路"), ("概念","白酒概念")]
print("name | scan最新买点/日期 | scan近40日 | scan ZG/收盘/突破 | analyze()最新买点")
for lv,nm in CHECK:
    mem = con.get(nm,set()) if lv=="概念" else (ind.get(nm,set()) if lv=="行业" else theme.get(nm,set()))
    dfi = synth(mem)
    r = scan[(scan["层级"]==lv)&(scan["名称"]==nm)].iloc[0]
    res = analyze(dfi, level="day", name=nm)
    ops = res.get("交易机会", [])
    buys=[o for o in ops if o.get("sig")==1 or str(o.get("类型","")).startswith("买") or o.get("kind") in ("B1","B2","B3")]
    lastop = ops[-1] if ops else None
    # 结构
    jg = res.get("结构", {})
    zs = jg.get("中枢", [])
    zglast = zs[-1].get("zg") if zs else None
    print(f"[{lv}] {nm}: scan=({r['最新买点类型']}@{r['最新买点日期']}, 近40={r['近40日买点类型']}, "
          f"ZG={r['中枢上沿ZG']}, close={r['最新收盘']}, brk={r['突破中枢']}) | "
          f"analyze 笔={len(jg.get('笔',[]))} 中枢={len(zs)} 末中枢zg={zglast} 末交易机会={lastop}")
print("\n[done]")