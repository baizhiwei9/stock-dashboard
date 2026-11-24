import os,sys
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW=os.path.join(ROOT,"raw")
sys.path.insert(0,RAW)
import ts_session as t
for dt in ["20250908","20260130","20260917"]:
    try:
        x=t.api("moneyflow",trade_date=dt)
        print(dt,"moneyflow rows=",len(x),"cols=",list(x.columns)[:12])
    except Exception as e:
        print(dt,"moneyflow ERR",e)
