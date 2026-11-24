import os,sys,time,json
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW=os.path.join(ROOT,"raw"); sys.path.insert(0,RAW); sys.path.insert(0,ROOT)
import ts_session as t
from ts_session import HTTP_URL, TOKEN, _S

def call(fields, **params):
    t0=time.time()
    r=_S.post(HTTP_URL, data=json.dumps({"api_name":"moneyflow","token":TOKEN,"params":params,"fields":fields}), timeout=90)
    j=r.json(); dt=time.time()-t0
    d=j.get("data") or {}
    return dt, j.get("code"), len(d.get("items",[]))

for tag, f in [("full",""),("ltd","ts_code,trade_date,net_mf_amount,buy_elg_amount,sell_elg_amount")]:
    for d in ["20250908","20250909","20250910"]:
        try:
            dt,code,n = call(f, trade_date=d)
            print(f"{tag} {d} code={code} rows={n} t={dt:.2f}s", flush=True)
        except Exception as e:
            print(tag,d,"ERR",str(e)[:80], flush=True)
        time.sleep(1.2)
