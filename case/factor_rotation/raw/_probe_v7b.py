# -*- coding: utf-8 -*-
"""探针 v7b：对 S 档 10 板块 + 电机，给出买点当日与最新日的 MA10/MA20/MA40 状态。
用于判定「均线过滤」应在买点当日还是最新日评估。"""
import numpy as np, pandas as pd
BASE = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ed3f1d6b1ffeo164XKTVu7vvVA"
RAW, OUT = f"{BASE}/raw", f"{BASE}/output"
src = open(f"{RAW}/chan_final_rank.py", encoding="utf-8").read()
ns = {}; exec(src[:src.index("rk = pd.read_csv")], ns)
synth = ns["synth"]; con_members, ind_members, theme_members = ns["con_members"], ns["ind_members"], ns["theme_members"]
cf, cc = ns["cf"], ns["cc"]

v4 = pd.read_csv(f"{OUT}/chan_final_v4.csv")
targets = [(r["层级"], r["名称"], r["买点日期"]) for _, r in
           v4[v4["档位_极值口径"].isin(["S1", "S2", "S3"])].iterrows()]
targets.append(("概念", "电机", None))

for lv, name, bdate in targets:
    mem = con_members.get(name, set()) if lv == "概念" else (ind_members.get(name, set()) if lv == "行业" else theme_members.get(name, set()))
    dfi, nm = synth(mem)
    if dfi is None:
        print(f"{lv}/{name}: synth none"); continue
    df2, _ = cf.normalize_input(dfi, name=name)
    c = df2["close"].values; n = len(c); datev = df2["time"].dt.strftime("%Y-%m-%d").values
    s = pd.Series(c); ma10 = s.rolling(10).mean().values; ma20 = s.rolling(20).mean().values; ma40 = s.rolling(40).mean().values
    def at(idx, label):
        return (f"{label}: {datev[idx]} close={c[idx]:.1f} MA10={ma10[idx]:.1f} MA20={ma20[idx]:.1f} MA40={ma40[idx]:.1f} "
                f"站上40={'Y' if np.isfinite(ma40[idx]) and c[idx] > ma40[idx] else 'N'} MA10>MA20={'Y' if np.isfinite(ma20[idx]) and ma10[idx] > ma20[idx] else 'N'}")
    rows = [at(n - 1, "最新")]
    if bdate and str(bdate) != "nan" and str(bdate) in set(datev):
        i = list(datev).index(str(bdate)); rows.append(at(i, f"买点{bdate[5:]}"))
    print(f"--- {lv}/{name} ---")
    for r in rows:
        print("   ", r)