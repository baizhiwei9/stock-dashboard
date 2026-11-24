# -*- coding: utf-8 -*-
import numpy as np, pandas as pd
BASE="/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ed3f1d6b1ffeo164XKTVu7vvVA"
RAW,OUT=f"{BASE}/raw",f"{BASE}/output"
src=open(f"{RAW}/chan_final_rank.py",encoding="utf-8").read()
ns={}; exec(src[:src.index("rk = pd.read_csv")],ns)
synth=ns["synth"]; con_members=ns["con_members"]; ind_members=ns["ind_members"]; theme_members=ns["theme_members"]
cf=ns["cf"]; cc=ns["cc"]
for lv,name in [("概念","AI视频"),("概念","机器人概念")]:
    mem=con_members.get(name,set())
    dfi,nm=synth(mem); df2,_=cf.normalize_input(dfi,name=name)
    ends=cf.get_endpoints(df2,"struct")
    datev=df2["time"].dt.strftime("%Y-%m-%d").values
    print("="*72); print(f"[{lv}]{name} 笔端点全览 (共{len(ends)})")
    for i,e in enumerate(ends):
        print(f"  {i:2d} row={e[0]:3d} {datev[e[0]]} {e[1]:6s} {e[2]:8.2f}")
    print("  3段窗口重叠(ZG>ZD)检测:")
    for i in range(0,len(ends)-3):
        segs=[(max(ends[i+k][2],ends[i+k+1][2]),min(ends[i+k][2],ends[i+k+1][2])) for k in range(3)]
        ZD=max(s[1] for s in segs); ZG=min(s[0] for s in segs)
        tag="OVERLAP" if ZG>ZD else "-"
        print(f"    i={i:2d} 段{i}~{i+3} 端点行{ends[i][0]}~{ends[i+3][0]} {datev[ends[i][0]]}~{datev[ends[i+3][0]]} ZD={ZD:.1f} ZG={ZG:.1f} {tag}")
