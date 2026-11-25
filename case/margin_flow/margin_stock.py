# -*- coding: utf-8 -*-
import os, sys, json
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import D, load_days, pct_rank, run_len
days=[int(d) for d in load_days()]; LAST=days[-1]
md=pd.read_csv(os.path.join(D,"margin_detail.csv")); md["trade_date"]=md.trade_date.astype(int)
db=pd.read_csv(os.path.join(D,"daily_basic.csv"),usecols=["ts_code","trade_date","close","circ_mv"]); db["trade_date"]=db.trade_date.astype(int)
sb=pd.read_csv(os.path.join(D,"stock_basic.csv")); nmap=dict(zip(sb.ts_code,sb.name))
im=pd.read_csv(os.path.join(D,"industry_map.csv")); imap=dict(zip(im.ts_code,im.l1_name))
rzye=md.pivot_table(index="trade_date",columns="ts_code",values="rzye").sort_index()
rzche=md.pivot_table(index="trade_date",columns="ts_code",values="rzche").sort_index()
netm =md.pivot_table(index="trade_date",columns="ts_code",values="rzmre").sort_index()-rzche
cm  =db.pivot_table(index="trade_date",columns="ts_code",values="circ_mv").sort_index()
cl  =db.pivot_table(index="trade_date",columns="ts_code",values="close").sort_index()
lev = rzye/(cm*1e4)*100  # %
print("lev_stock dist(latest):", np.nanpercentile(lev.loc[LAST], [50,80,90,95,99]).round(2))
hi8 = lev.loc[LAST][lev.loc[LAST]>8]
print("stocks lev>8%:", len(hi8))
# 偿还加速倍数 = mean5/mean prior20
rz5 = rzche.rolling(5).mean()
rz20p = rzche.rolling(20).mean().shift(5)
accel = rz5/rz20p
# 近3日偿还收敛度
rz3 = rzche.rolling(3).mean(); conv = rz20p/rz3   # >1 converged
# 60日高点
cl60max = cl.rolling(60).max(); rz60max = rzye.rolling(60).max()
drawdown = (1-cl.loc[LAST]/cl60max.loc[LAST])          # 股价距60日高点回撤
rz_drop  = (1-rzye.loc[LAST]/rz60max.loc[LAST])        # 融资余额距60日高点回落
rollmin20 = cl.rolling(20).min(); newlow = cl.loc[LAST]<=rollmin20.loc[LAST]
codes=sorted(set(rzye.columns)|set(cl.columns))
S=pd.DataFrame(index=codes)
S["name"]=[nmap.get(c,"") for c in codes]
S["industry"]=[imap.get(c,"") for c in codes]
S["lev"]=lev.loc[LAST].reindex(codes).values
S["accel"]=accel.loc[LAST].reindex(codes).values
S["conv"]=conv.loc[LAST].reindex(codes).values
S["retrace"]=drawdown.reindex(codes).values
S["rz_drop"]=rz_drop.reindex(codes).values
S["newlow"]=newlow.reindex(codes).values
S["close"]=cl.loc[LAST].reindex(codes).values
S["rzye_yi"]=(rzye.loc[LAST].reindex(codes)/1e8).values
# 强平连锁 (market): 高杠杆群体偿还
grp = rzye/(cm*1e4)*100 > 8
grp_rep = rzche.where(grp).mean(axis=1)
grp_rep5 = grp_rep.rolling(5).mean()
p_grp = pct_rank(grp_rep5)
print("高杠杆群体偿还5日均值 percentile(120d):", round(p_grp,1))
# RISK POOL
risk = S[(S.lev>8) & (S.accel>1.3) & (S.newlow==True)].copy()
risk["score"]=risk.lev*risk.accel
risk=risk.sort_values("score",ascending=False)
print("\n=== 风险池(强平脆弱) 候选数:",len(risk))
print(risk.head(12)[["name","lev","accel","close","newlow"]].round(2).to_string())
# OPPORTUNITY POOL
bp = S[(S.retrace>0.30) & (S.rz_drop>0.20) & (S.conv>1.0)].copy()
bp["score"]=bp.retrace*bp.conv
bp=bp.sort_values("score",ascending=False)
print("\n=== 机会池(超跌反弹) 候选数:",len(bp))
print(bp.head(12)[["name","retrace","rz_drop","conv","close"]].round(3).to_string())
S.to_csv(os.path.join(D,"stock_metrics.csv"))
