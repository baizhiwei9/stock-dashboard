# -*- coding: utf-8 -*-
"""诊断 行业/玻璃 的买点判定：中枢、三买、最近走势。"""
import numpy as np, pandas as pd
BASE = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ed3f1d6b1ffeo164XKTVu7vvVA"
RAW = f"{BASE}/raw"
src = open(f"{RAW}/chan_final_rank.py", encoding="utf-8").read()
ns = {}
exec(src[:src.index("rk = pd.read_csv")], ns)
synth, con_members, ind_members, theme_members = ns["synth"], ns["con_members"], ns["ind_members"], ns["theme_members"]
cf, cc = ns["cf"], ns["cc"]
print("cc.MIN_BI_GAP =", getattr(cc, "MIN_BI_GAP", None), " MIN_SEG_BI =", getattr(cc, "MIN_SEG_BI", None))

name, lv = "玻璃", "行业"
mem = ind_members.get(name, set())
dfi, nm = synth(mem)
df2, _ = cf.normalize_input(dfi, name=name)
ends = cf.get_endpoints(df2, "struct")
m = cc.macd_on(df2, 20, 40, 5)
close = df2["close"].values; low = df2["low"].values; high = df2["high"].values
datev = df2["time"].dt.strftime("%Y-%m-%d").values
n = len(close)
print("bars", n, datev[0], "~", datev[-1])
print("\n笔端点(endpoint idx -> row/date/type/px):")
for i, (row, tp, px) in enumerate(ends):
    print(f"  #{i:2d} row={row:4d} {datev[row]} {tp:6s} px={px:.2f}")

print("\ncc.detect_zhongshu(ends, MIN_BI_GAP) ->")
for z in cc.detect_zhongshu(ends, cc.MIN_BI_GAP):
    s, e_, zd, zg = z
    print(f"  ep[{s}..{e_}] row {ends[s][0]}..{ends[e_][0]} dates {datev[ends[s][0]]}..{datev[ends[e_][0]]} ZD={zd:.2f} ZG={zg:.2f}")

print("\n最近 25 根 close/high/low:")
for r in range(n - 25, n):
    print(f"  row={r} {datev[r]} c={close[r]:.2f} h={high[r]:.2f} l={low[r]:.2f}")