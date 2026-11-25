# -*- coding: utf-8 -*-
import os, json, pandas as pd
D=os.path.join(os.path.dirname(os.path.abspath(__file__)),"data")
days=json.load(open(os.path.join(D,"trade_days.json")))
print("trade_days:",len(days),days[0],"→",days[-1])
ms=pd.read_csv(os.path.join(D,"margin_summary.csv"))
print("margin_summary:",ms.shape,"dates",ms.trade_date.nunique(),"min",ms.trade_date.min(),"max",ms.trade_date.max(),"exch",sorted(ms.exchange_id.unique()))
md=pd.read_csv(os.path.join(D,"margin_detail.csv"))
print("margin_detail:",md.shape,"days",md.trade_date.nunique(),"codes",md.ts_code.nunique(),"null rzye",md.rzye.isna().sum())
db=pd.read_csv(os.path.join(D,"daily_basic.csv"))
print("daily_basic:",db.shape,"days",db.trade_date.nunique(),"codes",db.ts_code.nunique(),"null close",db.close.isna().sum(),"null circ_mv",db.circ_mv.isna().sum())
lc=pd.read_csv(os.path.join(D,"limit_counts.csv")); print("limit_counts:",lc.shape,"max",lc.trade_date.max())
etf=pd.read_csv(os.path.join(D,"etf_share.csv")); print("etf_share:",etf.shape,"codes",etf.ts_code.nunique(),"null fd_share",etf.fd_share.isna().sum(),"min",etf.trade_date.min(),"max",etf.trade_date.max())
im=pd.read_csv(os.path.join(D,"industry_map.csv")); print("industry_map:",im.shape,"industries",im.l1_name.nunique())
sb=pd.read_csv(os.path.join(D,"stock_basic.csv"))
# coverage of margin_detail codes in industry_map
latest=md[md.trade_date==md.trade_date.max()]
cov=latest.ts_code.isin(set(im.ts_code)).mean()
print("latest-day codes",len(latest),"industry cover %.1f%%"%(cov*100))
print("stock_basic codes",len(sb),"name cover of margin codes %.1f%%"%(latest.ts_code.isin(set(sb.ts_code)).mean()*100))
# last day per-dataset
print("LAST md day:",sorted(md.trade_date.unique())[-1],"| LAST db day:",sorted(db.trade_date.unique())[-1],"| LAST margin:",ms.trade_date.max())
