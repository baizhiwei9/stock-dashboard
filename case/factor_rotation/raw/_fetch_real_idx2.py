# -*- coding: utf-8 -*-
import sys, pandas as pd
BASE="/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ed3f1d6b1ffeo164XKTVu7vvVA"
sys.path.insert(0, f"{BASE}/raw")
from ts_session import api
CODES=["885795.TI","885937.TI","881277.TI","886068.TI","881155.TI","865107.TI","865053.TI","865006.TI","884014.TI",
       "865012.TI","881121.TI"]
frames=[]
for c in CODES:
    try:
        d=api("ths_daily", ts_code=c, start_date="20250901", end_date="20261029")
        if len(d): d["board"]=c; frames.append(d); print(c, len(d), d.trade_date.min(), d.trade_date.max())
        else: print(c, "EMPTY")
    except Exception as e:
        print(c, "FAIL", e)
out=pd.concat(frames, ignore_index=True)
out.to_csv(f"{BASE}/raw/_real_idx2.csv", index=False)
print("saved", out.shape, out.ts_code.nunique())
