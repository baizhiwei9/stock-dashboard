# -*- coding: utf-8 -*-
import sys, time, json
sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5e9bb55354ffe9Rg1AoYf1k3lwu")
from ts_http import fetch, fetch_df

for tp in ("N", "ST", "S", "R"):
    t0=time.time()
    try:
        f, items = fetch("ths_index", {"exchange": "A", "type": tp})
        print("ths_index type=%s rows=%d t=%.1fs cols=%s" % (tp, len(items), time.time()-t0, f))
        if items:
            print("   ", items[0])
    except Exception as e:
        print("ths_index type=%s FAIL %s: %s" % (tp, type(e).__name__, e))

try:
    f, items = fetch("ths_index", {"exchange": "A"})
    print("ths_index no-type rows=%d cols=%s" % (len(items), f))
    codes = [r[0] for r in items][:3]
    print("sample codes", codes)
    for c in codes[:2]:
        fa, ia = fetch("ths_member", {"ts_code": c})
        print("ths_member", c, "rows", len(ia), "cols", fa)
        if ia:
            print("   ", ia[0])
except Exception as e:
    print("ths_member FAIL", type(e).__name__, e)
