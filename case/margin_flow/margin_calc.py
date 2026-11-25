# -*- coding: utf-8 -*-
import os, sys, json
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import D, load_days, close_pivot, pct_rank, run_len
days=[int(d) for d in load_days()]; LAST=days[-1]
ms=pd.read_csv(os.path.join(D,"margin_summary.csv")); ms["trade_date"]=ms.trade_date.astype(int)
m2=ms[ms.exchange_id.isin(["SSE","SZSE"])].groupby("trade_date")[["rzye","rqye","rzmre","rzche","rzrqye"]].sum().sort_index()
db=pd.read_csv(os.path.join(D,"daily_basic.csv"),usecols=["ts_code","trade_date","close","circ_mv"])
db["trade_date"]=db.trade_date.astype(int)
db["mk"]=db.ts_code.str.split(".").str[1]
cm=db[db.mk.isin(["SH","SZ"])].groupby("trade_date").circ_mv.sum()
idx=pd.read_csv(os.path.join(D,"index_amount.csv")); idx["trade_date"]=idx.trade_date.astype(int)
amt=idx[idx.ts_code.isin(["000001.SH","399106.SZ"])].groupby("trade_date").amount.sum()
amp=pd.read_csv(os.path.join(D,"index_main.csv")); amp["trade_date"]=amp.trade_date.astype(int)
def s_idx(code):
    s=amp[amp.ts_code==code].sort_values("trade_date").set_index("trade_date").close; return s
s300=s_idx("000300.SH"); s500=s_idx("000905.SH")
lev=(m2.rzye/(cm*1e4))*100   # %
turn=(m2.rzmre/(amt*1000))*100  # %
netbuy=m2.rzmre-m2.rzche
tot=pd.DataFrame({"rzye":m2.rzye,"rqye":m2.rqye,"rzmre":m2.rzmre,"rzche":m2.rzche,"rzrqye":m2.rzrqye,
  "circ_mv_yi":cm*1e4/1e8,"lev_pct":lev,"turn_pct":turn,"netbuy":netbuy})
tot["dod_pct"]=m2.rzye.pct_change()*100
for k in [5,10,20]: tot[f"chg{k}_pct"]=m2.rzye.pct_change(k)*100
tot["netbuy20"]=netbuy.rolling(20).sum()
L=tot.loc[LAST]
print("=== MARKET ===")
print(f"date={LAST} 融资余额={m2.rzye.loc[LAST]/1e8:.0f}亿 融券余额={m2.rqye.loc[LAST]/1e8:.0f}亿 两融余额={m2.rzrqye.loc[LAST]/1e8:.0f}亿")
print(f"流通市值={cm.loc[LAST]*1e4/1e8:.0f}亿 杠杆率={L.lev_pct:.3f}% 成交占比={L.turn_pct:.2f}%")
print(f"日环比={L.dod_pct:+.3f}% 5日={L.chg5_pct:+.3f}% 10日={L.chg10_pct:+.3f}% 20日={L.chg20_pct:+.3f}%")
print(f"20日净买入={L.netbuy20/1e8:.1f}亿")
print("lev tail:"); print(tot.lev_pct.tail(6).round(3).to_string())
print("turn tail:"); print(tot.turn_pct.tail(6).round(3).to_string())
print("dod tail:"); print(tot.dod_pct.tail(6).round(3).to_string())
# percentiles 120
p_lev=pct_rank(tot.lev_pct); p_net=pct_rank(tot.netbuy20); p_turn=pct_rank(tot.turn_pct)
print(f"percentiles(120d): lev={p_lev:.1f} netbuy20={p_net:.1f} turn={p_turn:.1f}")
# accelerate outflow
below = tot.dod_pct < -0.3
run_below=run_len(below)
accel = run_below>=2 and abs(tot.dod_pct.iloc[-1]) > abs(tot.dod_pct.iloc[-2])
print("dod<-0.3 run:",run_below,"accel:",accel)
# 9%->6% retreat: check when turn last below 6
print("turn last:",tot.turn_pct.iloc[-1],"min60:",tot.turn_pct.iloc[-60:].min())

print("\n=== DIAG ===")
print("netbuy tail(亿):"); print((tot.netbuy.tail(10)/1e8).round(0).to_string())
print("rzye max(亿) & date:", m2.rzye.max()/1e8, m2.rzye.idxmax(), "| now:", m2.rzye.loc[LAST]/1e8, "| drop from peak %:", (m2.rzye.loc[LAST]/m2.rzye.max()-1)*100)
print("lev max/last:", tot.lev_pct.max(), tot.lev_pct.loc[LAST])
print("lev 60d tail:"); print(tot.lev_pct.tail(60).round(3).describe().to_string())
print("turn min over 120:", tot.turn_pct.iloc[-120:].min().round(3), "max:", tot.turn_pct.iloc[-120:].max().round(3))
print("dod<-0.3 in last 20:", below.tail(20).sum())
print("last 8 dod%:", tot.dod_pct.tail(8).round(3).tolist())
print("chg5 last 10:", tot.chg5_pct.tail(10).round(2).tolist())
