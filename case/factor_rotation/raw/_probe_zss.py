# -*- coding: utf-8 -*-
import numpy as np, pandas as pd
BASE="/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ed3f1d6b1ffeo164XKTVu7vvVA"
RAW,OUT=f"{BASE}/raw",f"{BASE}/output"
src=open(f"{RAW}/chan_final_rank.py",encoding="utf-8").read()
ns={}; exec(src[:src.index("rk = pd.read_csv")],ns)
synth=ns["synth"]; con_members=ns["con_members"]; ind_members=ns["ind_members"]; theme_members=ns["theme_members"]
cf=ns["cf"]; cc=ns["cc"]
print("常量 MIN_BI_GAP=",cc.MIN_BI_GAP," MIN_SEG_BI=",cc.MIN_SEG_BI)
targets=[("概念","AI视频"),("概念","机器人概念"),("概念","人形机器人"),
         ("主题","AI软件与应用"),("主题","机器人与智能制造"),
         ("行业","银行"),("行业","路桥")]
for lv,name in targets:
    mem=con_members.get(name,set()) if lv=="概念" else (ind_members.get(name,set()) if lv=="行业" else theme_members.get(name,set()))
    dfi,nm=synth(mem)
    df2,_=cf.normalize_input(dfi,name=name)
    ends=cf.get_endpoints(df2,"struct")
    datev=df2["time"].dt.strftime("%Y-%m-%d").values
    for ms in (4,3):
        zss=cc.detect_zhongshu(ends,ms)
        zl=max(zss,key=lambda z:(z[1],z[0])) if zss else None
        ztxt=(f"{datev[int(zl[0])]}~{datev[int(zl[1])]} ZG={zl[3]:.1f} ZD={zl[2]:.1f}") if zl else "无"
        print(f"[{lv}]{name:8s} 笔={len(ends):2d} min_seg={ms}: 中枢数={len(zss):2d} 最近={ztxt}")
    print()
