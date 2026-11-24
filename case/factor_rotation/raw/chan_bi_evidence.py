# -*- coding: utf-8 -*-
"""逐笔取证：被点名板块在【修正配对】下、分别用 close 与 极值(low) 判定底背驰时的两段下跌笔明细。"""
import numpy as np, pandas as pd
BASE = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ed3f1d6b1ffeo164XKTVu7vvVA"
RAW = f"{BASE}/raw"
src = open(f"{RAW}/chan_final_rank.py", encoding="utf-8").read(); ns = {}
exec(src[:src.index("rk = pd.read_csv")], ns)
synth, con_members, ind_members, cf, cc = ns["synth"], ns["con_members"], ns["ind_members"], ns["cf"], ns["cc"]


def two_bi(ends, hist, plo, phi):
    """列出所有『同向两段笔』的背驰明细（修正配对）。"""
    out = []
    for i in range(3, len(ends)):
        if ends[i][1] != ends[i - 2][1]:
            continue
        r1a, r1b = ends[i - 3][0], ends[i - 2][0]
        r2a, r2b = ends[i - 1][0], ends[i][0]
        if r1b <= r1a or r2b <= r2a:
            continue
        s1 = hist[r1a:r1b + 1]; s2 = hist[r2a:r2b + 1]
        if len(s1) < 2 or len(s2) < 2:
            continue
        a1, a2 = abs(float(np.sum(s1))), abs(float(np.sum(s2)))
        if a1 <= 1e-12:
            continue
        if ends[i][1] == "bottom":
            l1 = float(np.min(plo[r1a:r1b + 1])); l2 = float(np.min(plo[r2a:r2b + 1]))
            out.append((r1a, r1b, r2a, r2b, l1, l2, a1, a2, l2 <= l1, a2 < a1 * 0.9))
    return out


for lv, bn in [("概念", "国产航母"), ("概念", "培育钻石"), ("概念", "电机"),
               ("行业", "石油开采"), ("行业", "航空"), ("行业", "电信运营"), ("行业", "矿物制品")]:
    mem = con_members[bn] if lv == "概念" else ind_members[bn]
    dfi, nm = synth(mem); df2, _ = cf.normalize_input(dfi, name="b")
    ends = cf.get_endpoints(df2, "struct"); h = cc.macd_on(df2, 20, 40, 5)["hist"].values
    low = df2["low"].values; close = df2["close"].values; dt = df2["time"].dt.strftime("%Y-%m-%d").values
    print(f"\n=== {lv}/{bn} 成分{nm} 最新{dt[-1]} 收{close[-1]:.2f} 低{low[-1]:.2f} ===")
    print("  末7端点:", [(ends[k][1][:3], dt[ends[k][0]], round(float(ends[k][2]), 1)) for k in range(max(0, len(ends) - 7), len(ends))])
    for tag, plo, phi in [("close口径", close, close), ("极值口径", low, low + 0)]:
        bi = two_bi(ends, h, plo, phi)
        if not bi:
            print(f"  [{tag}] 无同向两段笔背驰")
            continue
        for r1a, r1b, r2a, r2b, l1, l2, a1, a2, isnew, weak in bi[-2:]:
            print(f"  [{tag}] 前跌笔 {dt[r1a]}→{dt[r1b]} 段低{l1:.2f} 面积{a1:.1f} | 本跌笔 {dt[r2a]}→{dt[r2b]} 段低{l2:.2f} 面积{a2:.1f} "
                  f"→ 创新低={isnew} 面积弱10%={weak} ⇒ {'★B1成立' if (isnew and weak) else '不成立'}")