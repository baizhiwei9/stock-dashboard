# -*- coding: utf-8 -*-
"""探针 v8：对指定板块打印端点序列 + 中枢列表 + B3 触发轨迹，用于判定「超宽中枢/陈旧三买」。"""
import numpy as np, pandas as pd
BASE = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ed3f1d6b1ffeo164XKTVu7vvVA"
RAW = f"{BASE}/raw"
src = open(f"{RAW}/chan_final_rank.py", encoding="utf-8").read()
ns = {}; exec(src[:src.index("rk = pd.read_csv")], ns)
synth = ns["synth"]; con_members, ind_members, theme_members = ns["con_members"], ns["ind_members"], ns["theme_members"]
cf, cc, WINDOW = ns["cf"], ns["cc"], ns["WINDOW"]

TARGETS = [("行业", "银行"), ("概念", "培育钻石"), ("行业", "路桥"), ("行业", "焦炭加工")]

for lv, name in TARGETS:
    mem = (con_members.get(name, set()) if lv == "概念"
           else (ind_members.get(name, set()) if lv == "行业" else theme_members.get(name, set())))
    dfi, nm = synth(mem)
    df2, _ = cf.normalize_input(dfi, name=name)
    ends = cf.get_endpoints(df2, "struct")
    datev = df2["time"].dt.strftime("%Y-%m-%d").values
    close = df2["close"].values
    n = len(close)
    zss = cc.detect_zhongshu(ends, cc.MIN_BI_GAP)
    print("=" * 100)
    print(f"【{lv}/{name}】 n={n} 端点数={len(ends)} 中枢数={len(zss)}  最新={close[-1]:.2f}")
    print(f"  价格区间: close[{close.min():.1f}..{close.max():.1f}]  末10日close={np.round(close[-10:],1).tolist()}")
    print("  端点（序号: 行号 日期 类型 价）:")
    for k, (row, tp, px) in enumerate(ends):
        print(f"    e{k:>2}: r{row:>3} {datev[row]} {tp:<6} {px:>9.2f}")
    print("  中枢（端点区间 -> 行区间 -> 日期区间 -> ZD/ZG -> 跨度行）:")
    for (s, e_, zd, zg) in zss:
        rs, re_ = ends[s][0], ends[e_][0]
        print(f"    e[{s},{e_}]  r[{rs},{re_}]  {datev[rs]}~{datev[re_]}  ZD={zd:.2f} ZG={zg:.2f}  跨度={re_-rs+1}行")
    # B3 trace per zhongshu (old-style, no break)
    print("  B3 轨迹（旧口径，不 break，用于观察反复触发）:")
    for (s, e_, zd, zg) in zss:
        left = False; hits = []
        for idx in range(e_, len(ends)):
            row, tp, px = ends[idx]
            if tp == "top":
                if idx > 0 and max(px, ends[idx - 1][2]) > zg:
                    left = True
            elif tp == "bottom":
                if left and px > zg:
                    hits.append((row, datev[row], px)); left = False
        print(f"    中枢[{s},{e_}] ZG={zg:.2f}: B3候选={[(d,round(p,1)) for _,d,p in hits]}")