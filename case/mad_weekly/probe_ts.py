# -*- coding: utf-8 -*-
import sys, time
sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5e9bb55354ffe9Rg1AoYf1k3lwu")
from ts_http import fetch

def t(api, params, fields=""):
    t0 = time.time()
    try:
        f, items = fetch(api, params, fields)
        print(f"{api} {params} -> rows={len(items)} cols={f} t={time.time()-t0:.1f}s")
        return f, items
    except Exception as e:
        print(f"{api} {params} -> FAIL {type(e).__name__}: {e} t={time.time()-t0:.1f}s")
        return None, None

for d in ("20261030", "20261029", "20261031"):
    f, items = t("daily", {"trade_date": d})
    if items:
        print("   sample:", items[0])
        break

t("adj_factor", {"trade_date": "20261030"})
t("stk_factor", {"trade_date": "20261030"}, fields="ts_code,trade_date,close_hfq,ma_bfq_20,ma_hfq_20")
t("daily_basic", {"trade_date": "20261030"}, fields="ts_code,close,total_mv,circ_mv")
t("stock_basic", {"exchange": "", "list_status": "L"}, fields="ts_code,name,industry,list_date")