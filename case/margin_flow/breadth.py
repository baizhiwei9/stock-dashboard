# -*- coding: utf-8 -*-
import os, sys, json
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import D, load_days, close_pivot, pct_rank, run_len
days=[int(d) for d in load_days()]
close=close_pivot()
close=close.reindex(days).astype(float)
dates=[int(d) for d in close.index]
N=close.notna().sum(axis=1)                       # universe per day
ret=close.pct_change()
up=(ret>0).sum(axis=1); down=(ret<0).sum(axis=1); flat=(ret==0).sum(axis=1)
ad_ratio=(up/down).replace([np.inf,-np.inf],np.nan)
ad_diff=up-down
ma20=close.rolling(20).mean(); ma60=close.rolling(60).mean()
v20=close.notna()&ma20.notna(); v60=close.notna()&ma60.notna()
r20=((close>ma20)&v20).sum(axis=1)/v20.sum(axis=1)*100
r60=((close>ma60)&v60).sum(axis=1)/v60.sum(axis=1)*100
rmax20=close.rolling(20).max(); rmin20=close.rolling(20).min()
rmax60=close.rolling(60).max(); rmin60=close.rolling(60).min()
c20=close.rolling(20).count()>=20; c60=close.rolling(60).count()>=60
nh20=((close>=rmax20)&c20).sum(axis=1); nl20=((close<=rmin20)&c20).sum(axis=1)
nh60=((close>=rmax60)&c60).sum(axis=1); nl60=((close<=rmin60)&c60).sum(axis=1)
nh_nl_diff=nh20-nl20
df=pd.DataFrame({"date":dates,"universe":N.values,"up":up.values,"down":down.values,"flat":flat.values,
    "ad_ratio":ad_ratio.values,"ad_diff":ad_diff.values,"r20":r20.values,"r60":r60.values,
    "nh20":nh20.values,"nl20":nl20.values,"nh60":nh60.values,"nl60":nl60.values,"nhnl_diff":nh_nl_diff.values}).set_index("date")
df.to_csv(os.path.join(D,"breadth_series.csv"))
last=int(df.index[-1]); L=df.loc[last]
def band_ad(x):
    if pd.isna(x): return "NA"
    if x>2.0: return ">2.0 亢奋"
    if x>=1.5: return "1.5~2.0 强势"
    if x>=0.8: return "0.8~1.5 中性"
    if x>=0.5: return "0.5~0.8 偏弱"
    return "<0.5 系统性风险"
def band_r20(x):
    if pd.isna(x): return "NA"
    if x>60: return ">60% 健康"
    if x>=40: return "40~60% 中性"
    if x>=30: return "30~40% 偏弱"
    if x>=20: return "20~30% 破位"
    return "<20% 极端压缩"
def band_r60(x):
    if pd.isna(x): return "NA"
    if x>50: return ">50% 中期健康"
    if x>=30: return "30~50% 中性偏弱"
    return "<30% 中期向下"
runs={"ad_ratio_lt0.5":int(run_len(df.ad_ratio<0.5)),"ad_ratio_lt0.8":int(run_len(df.ad_ratio<0.8)),
      "r20_lt30":int(run_len(df.r20<30)),"nl20_gt800":int(run_len(df.nl20>800))}
trig=[]; risk_trig=[]
def _add(msg, risk=True):
    trig.append(msg)
    if risk: risk_trig.append(msg)
if runs["ad_ratio_lt0.5"]>=3: _add(f"涨跌比<0.5连续第{runs['ad_ratio_lt0.5']}天")
if df.ad_ratio.iloc[-1]<0.3: _add(f"涨跌比<0.3单日极端恐慌({df.ad_ratio.iloc[-1]:.2f})")
if runs["ad_ratio_lt0.8"]>=5: _add(f"涨跌比<0.8连续第{runs['ad_ratio_lt0.8']}天")
if runs["r20_lt30"]>=5: _add(f"站上20MA比例<30%连续第{runs['r20_lt30']}天")
if runs["nl20_gt800"]>=5: _add(f"创20日新低>800家连续第{runs['nl20_gt800']}天")
if L.nl20>1000: _add(f"创20日新低{L.nl20:.0f}家>1000(扩散性风险)")
elif L.nl20>500: _add(f"创20日新低{L.nl20:.0f}家>500(局部风险)")
if L.r20>60: _add("站上20MA比例>60%(健康)", risk=False)
if L.r60<30: _add("站上60MA比例<30%(中期向下)")
p={
 "ad_ratio":pct_rank(df.ad_ratio),"r20":pct_rank(df.r20),"r60":pct_rank(df.r60),
 "nl20":pct_rank(df.nl20),"nhnl_diff":pct_rank(df.nhnl_diff)}
comp=(p["ad_ratio"]+p["r20"]+p["r60"]+(100-p["nl20"])+p["nhnl_diff"])/5
# index divergence
idf=pd.read_csv(os.path.join(D,"index_main.csv")); idf["trade_date"]=idf.trade_date.astype(int)
def idx_pct(code,win=60):
    s=idf[idf.ts_code==code].sort_values("trade_date").set_index("trade_date").close
    w=s.iloc[-win:] if len(s)>=win else s
    return float((w<=w.iloc[-1]).mean()*100), s
hs300_pct,s300=idx_pct("000300.SH"); zz500_pct,s500=idx_pct("000905.SH")
max_pct=max(hs300_pct,zz500_pct)
divergence="无"
if max_pct>60 and comp<40 and (max_pct-comp)>20: divergence="宽度背离（指数假强势）"
# bottom divergence: index new 60d low but breadth not new low
idx_newlow = (s300.iloc[-1]<=s300.iloc[-60:].min()) or (s500.iloc[-1]<=s500.iloc[-60:].min())
ad_newlow = df.ad_ratio.iloc[-1]<=df.ad_ratio.iloc[-60:].min()
r20_newlow = df.r20.iloc[-1]<=df.r20.iloc[-60:].min()
if idx_newlow and (not ad_newlow or not r20_newlow): divergence="底背离（宽度拒绝恶化）"
if comp>=80: level="🟢扩张"; wstate="扩张"
elif comp<20: level="🔴收缩"; wstate="极端收缩"
elif comp<40: level="🔴收缩"; wstate="收缩"
else: level="🟡中性"; wstate="中性"
out={"last_date":last,"universe":int(L.universe),"up":int(L.up),"down":int(L.down),"flat":int(L.flat),
 "ad_ratio":round(float(L.ad_ratio),3),"ad_ratio_band":band_ad(L.ad_ratio),"ad_diff":int(L.ad_diff),
 "r20":round(float(L.r20),2),"r20_band":band_r20(L.r20),"r60":round(float(L.r60),2),"r60_band":band_r60(L.r60),
 "nh20":int(L.nh20),"nl20":int(L.nl20),"nh60":int(L.nh60),"nl60":int(L.nl60),
 "nl20_pct_of_universe":round(float(L.nl20/L.universe*100),2),"nh_nl_diff":int(L.nhnl_diff),
 "runs":runs,"triggers":trig,"risk_triggers":risk_trig,"percentiles":{k:round(v,1) for k,v in p.items()},
 "composite_pct":round(comp,1),"level":level,"wstate":wstate,
 "index_pct":{"hs300":round(hs300_pct,1),"zz500":round(zz500_pct,1)},
 "divergence":divergence}
json.dump(out, open(os.path.join(D,"breadth_result.json"),"w"), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False, indent=1))
print("\nlast 8 days:")
print(df.tail(8)[["up","down","ad_ratio","r20","r60","nl20","nh20","nhnl_diff"]].to_string())
