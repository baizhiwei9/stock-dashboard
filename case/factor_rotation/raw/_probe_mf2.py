import os,sys
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW=os.path.join(ROOT,"raw"); sys.path.insert(0,RAW)
import ts_session as t
for sd,ed in [("20250901","20250930"),("20251001","20251031"),("20260301","20260331")]:
    try:
        x=t.api("moneyflow", start_date=sd, end_date=ed)
        d=x["trade_date"].nunique() if len(x) else 0
        print(sd,ed,"rows=",len(x),"dates=",d, "min",x["trade_date"].min() if len(x) else "-","max",x["trade_date"].max() if len(x) else "-")
    except Exception as e:
        print(sd,ed,"ERR",str(e)[:120])
