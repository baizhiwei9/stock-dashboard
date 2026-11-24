# -*- coding: utf-8 -*-
import sys, os
import pandas as pd, numpy as np
BASE="/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ed3f1d6b1ffeo164XKTVu7vvVA"
sys.path.insert(0, f"{BASE}/raw")
from ts_session import api

codes={"885795.TI":"国产航母","885937.TI":"培育钻石","881277.TI":"电机","886068.TI":"AI视频"}
out=[]
for c,nm in codes.items():
    try:
        df=api("ths_daily", ts_code=c, start_date="20250901", end_date="20261029")
        df=df.sort_values("trade_date")
        print(f"\n=== {nm} {c} rows={len(df)} maxdate={df.trade_date.max()} ===")
        tail=df.tail(14)[["trade_date","close","pre_close","pct_change","high","low"]]
        print(tail.to_string(index=False))
        df["name"]=nm; out.append(df[["trade_date","name","close","pre_close","pct_change","high","low"]])
    except Exception as e:
        print(nm, "FAIL", e)
if out:
    pd.concat(out).to_csv(f"{BASE}/raw/_board_idx_probe.csv", index=False)
    print("\n[saved] raw/_board_idx_probe.csv")
