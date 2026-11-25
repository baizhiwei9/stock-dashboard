# -*- coding: utf-8 -*-
import os, sys, json
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import D, load_days, pct_rank
days=[int(d) for d in load_days()]; LAST=days[-1]
md=pd.read_csv(os.path.join(D,"margin_detail.csv")); md["trade_date"]=md.trade_date.astype(int)
im=pd.read_csv(os.path.join(D,"industry_map.csv")); imap=dict(zip(im.ts_code,im.l1_name))
md["netm"]=md.rzmre-md.rzche
md["ind"]=md.ts_code.map(imap)
md=md.dropna(subset=["ind"])
piv=md.pivot_table(index="trade_date",columns="ts_code",values="netm").sort_index()
r20=piv.rolling(20).sum()
# industry 20d net buy series
indser={}
for ind,g in md.groupby("ind"):
    cols=[c for c in g.ts_code.unique() if c in r20.columns]
    indser[ind]=r20[cols].sum(axis=1)
IND=pd.DataFrame(indser)
p_ind={c:pct_rank(IND[c]) for c in IND.columns}
ind_last=IND.iloc[-1]/1e8  # 亿元
# ETF share 20d change percentile
etf=pd.read_csv(os.path.join(D,"etf_share.csv")); etf["trade_date"]=etf.trade_date.astype(int)
etf=etf[etf.trade_date<=LAST]   # 只取 <=基准日，避免上游超前份额混入基准日报告
sh=etf.pivot_table(index="trade_date",columns="ts_code",values="fd_share").sort_index()
chg20=sh.pct_change(20)*100
p_etf={c:pct_rank(chg20[c]) for c in chg20.columns}
etf_last=chg20.iloc[-1]
ETFMAP={"512480.SH":["电子"],"588200.SH":["电子"],"515880.SH":["通信"],"159819.SZ":["计算机"],
 "512000.SH":["非银金融"],"512800.SH":["银行"],"512170.SH":["医药生物"],"159992.SZ":["医药生物"],
 "516270.SH":["电力设备"],"159796.SZ":["电力设备"],"159928.SZ":["食品饮料"],"515170.SH":["食品饮料"],
 "515220.SH":["煤炭"],"512400.SH":["有色金属"]}
ETFNAME={"512480.SH":"半导体ETF","588200.SH":"科创芯片ETF","515880.SH":"通信ETF","159819.SZ":"人工智能ETF",
 "512000.SH":"券商ETF","512800.SH":"银行ETF","512170.SH":"医疗ETF","159992.SZ":"创新药ETF",
 "516270.SH":"新能源ETF","159796.SZ":"电池ETF","159928.SZ":"消费ETF","515170.SH":"食品饮料ETF",
 "515220.SH":"煤炭ETF","512400.SH":"有色ETF","515080.SH":"红利ETF","563020.SH":"红利低波ETF",
 "510300.SH":"沪深300ETF","588000.SH":"科创50ETF","159915.SZ":"创业板ETF"}
rows=[]
for etf_c,inds in ETFMAP.items():
    ind=inds[0]
    ef_p=p_etf.get(etf_c,np.nan); fi_p=p_ind.get(ind,np.nan)
    fd=etf_last.get(etf_c,np.nan); idir=ind_last.get(ind,np.nan)
    def lab(fi,ef):
        if np.isnan(fi) or np.isnan(ef): return "数据不足"
        if fi>60 and ef>60: return "双强(主升共振)"
        if ef>60 and fi<40: return "机构底仓"
        if fi>60 and ef<40: return "杠杆炒作(警惕回撤)"
        if fi<40 and ef<40: return "双弱(回避)"
        return "中性"
    rows.append({"industry":ind,"etf_code":etf_c,"etf_name":ETFNAME.get(etf_c,""),
      "fin_dir_20d_yi":round(float(idir),1),"fin_pct":round(float(fi_p),1),
      "etf_chg20_pct":round(float(fd),2),"etf_pct":round(float(ef_p),1),"label":lab(fi_p,ef_p)})
ID=pd.DataFrame(rows).sort_values("fin_pct",ascending=False)
ID.to_csv(os.path.join(D,"industry_div.csv"),index=False)
IND.to_csv(os.path.join(D,"industry_fin20.csv"))
chg20.to_csv(os.path.join(D,"etf_chg20.csv"))
print(ID.to_string())
json.dump({"p_ind":{k:(None if np.isnan(v) else round(v,1)) for k,v in p_ind.items()},
           "p_etf":{k:(None if np.isnan(v) else round(v,1)) for k,v in p_etf.items()}},
          open(os.path.join(D,"ind_pct.json"),"w"),ensure_ascii=False)
