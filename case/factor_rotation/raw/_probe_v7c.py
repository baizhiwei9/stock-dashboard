# -*- coding: utf-8 -*-
"""探针 v7c：查看 银行/培育钻石 的全部中枢明细与价格范围，判断是否存在"超宽中枢"。"""
import numpy as np, pandas as pd
BASE = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ed3f1d6b1ffeo164XKTVu7vvVA"
RAW, OUT = f"{BASE}/raw", f"{BASE}/output"
src = open(f"{RAW}/chan_final_rank.py", encoding="utf-8").read()
ns = {}; exec(src[:src.index("rk = pd.read_csv")], ns)
synth = ns["synth"]; con_members, ind_members, theme_members = ns["con_members"], ns["ind_members"], ns["theme_members"]
cf, cc = ns["cf"], ns["cc"]
print("MIN_BI_GAP =", cc.MIN_BI_GAP)
for lv, name in [("行业", "银行"), ("概念", "培育钻石"), ("行业", "路桥"), ("行业", "焦炭加工")]:
    mem = con_members.get(name, set()) if lv == "概念" else ind_members.get(name, set())
    dfi, nm = synth(mem)
    df2, _ = cf.normalize_input(dfi, name=name)
    ends = cf.get_endpoints(df2, "struct")
    close = df2["close"].values; n = len(close)
    datev = df2["time"].dt.strftime("%Y-%m-%d").values
    zss = cc.detect_zhongshu(ends, cc.MIN_BI_GAP)
    print(f"\n=== {lv}/{name}  n={n} 行  {datev[0]}~{datev[-1]}  端点数={len(ends)}  中枢数={len(zss)} ===")
    for s, e_, zd, zg in zss:
        rs, re = int(ends[s][0]), int(ends[e_][0])
        w = close[rs:re + 1]
        print(f"  中枢 端点[{s},{e_}] 行[{rs},{re}] 日期[{datev[rs]}~{datev[re]}] 跨度={re-rs+1}行 "
              f"ZD={zd:.2f} ZG={zg:.2f} 区间内收盘 min/max={w.min():.1f}/{w.max():.1f}")
    print(f"  收盘区间: {close.min():.1f} ~ {close.max():.1f}; 最新={close[-1]:.1f}")