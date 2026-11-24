# -*- coding: utf-8 -*-
import os, sys
WORK=os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0,"/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5e9bb55354ffe9Rg1AoYf1k3lwu")
import pandas as pd
from ts_http import fetch_many
CODES=["600825.SH","601900.SH","601098.SH","000719.SZ","301052.SZ","600757.SH","601928.SH","300654.SZ","601019.SH"]
res=fetch_many("fina_indicator",[{"ts_code":c,"period":"20260630"} for c in CODES],workers=3,verbose=False,
               fields="ts_code,end_date,eps,roe,roe_waa,grossprofit_margin,netprofit_margin,tr_yoy,or_yoy,netprofit_yoy,dt_netprofit_yoy,debt_to_assets,ocf_to_or,q_sales_yoy")
fr=[]
for c,d in zip(CODES,res):
    if isinstance(d,Exception): print("FAIL",c,d); continue
    if len(d)==0: print("EMPTY",c); continue
    fr.append(d)
t=pd.concat(fr,ignore_index=True)
t.to_csv(os.path.join(WORK,"fina_indicator_20260630.csv"),index=False,encoding="utf-8-sig")
pd.set_option("display.width",300); pd.set_option("display.max_columns",30)
print(t.to_string(index=False))
