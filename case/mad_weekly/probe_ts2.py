# -*- coding: utf-8 -*-
import sys, time
sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5e9bb55354ffe9Rg1AoYf1k3lwu")
from ts_http import fetch

def t(api, params, fields=""):
    t0 = time.time()
    try:
        f, items = fetch(api, params, fields)
        print(f"{api} {params} -> rows={len(items)} t={time.time()-t0:.1f}s")
        return f, items
    except Exception as e:
        print(f"{api} {params} -> FAIL {type(e).__name__}: {e}")
        return None, None

f, items = t("stk_factor", {"trade_date": "20250701"}, fields="ts_code,trade_date,close_hfq,ma_hfq_20")
if items:
    print("  sample:", items[0])

f, items = t("stk_factor", {"trade_date": "20251215"}, fields="ts_code,trade_date,close_hfq")
if items:
    print("  sample:", items[0])

# cross-check: 600519.SH 后复权 close 两种口径
f1, i1 = t("daily", {"ts_code": "600519.SH", "start_date": "20250701", "end_date": "20250703"})
f2, i2 = t("adj_factor", {"ts_code": "600519.SH", "start_date": "20250701", "end_date": "20250703"})
print("daily:", i1)
print("adjf:", i2)