# -*- coding: utf-8 -*-
"""探针：核实第1层（两融）新增监控项的可计算值与阈值命中情况。"""
import os, sys, json
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import D, load_days

days = [int(d) for d in load_days()]
LAST = days[-1]
print("LAST", LAST, "n_days", len(days), "first", days[0])

# ---- 市场级 30 日增速 ----
ms = pd.read_csv(os.path.join(D, "margin_summary.csv")); ms["trade_date"] = ms.trade_date.astype(int)
m2 = (ms[ms.exchange_id.isin(["SSE", "SZSE"])]
      .groupby("trade_date")[["rzye", "rzmre", "rzche", "rqye", "rzrqye"]].sum().sort_index())
for k in (5, 10, 20, 30, 60):
    print(f"rzye chg{k}_pct @last =", round(float(m2.rzye.pct_change(k).iloc[-1] * 100), 4))

# ---- 个股面板 ----
md = pd.read_csv(os.path.join(D, "margin_detail.csv")); md["trade_date"] = md.trade_date.astype(int)
sb = pd.read_csv(os.path.join(D, "stock_basic.csv")); nmap = dict(zip(sb.ts_code, sb.name))
db = pd.read_csv(os.path.join(D, "daily_basic.csv"),
                 usecols=["ts_code", "trade_date", "circ_mv", "total_mv"]); db["trade_date"] = db.trade_date.astype(int)
print("db cols ok; rows", len(db))

rz = md.pivot_table(index="trade_date", columns="ts_code", values="rzye").sort_index()
rce = md.pivot_table(index="trade_date", columns="ts_code", values="rzche").sort_index()
bcm = db.pivot_table(index="trade_date", columns="ts_code", values="circ_mv").sort_index()
btm = db.pivot_table(index="trade_date", columns="ts_code", values="total_mv").sort_index()
slev = rz / (bcm * 1e4) * 100

# 权重股（按最新 total_mv 前 15）
tm_last = btm.iloc[-1].dropna().sort_values(ascending=False)
top = tm_last.head(15)
print("\n== 权重股前15（total_mv 亿元, 融资余额亿元, 融资占流通市值%, 5日增速%, 10日增速%, 60日回撤%）==")
rows = []
for c in top.index:
    if c not in rz.columns:
        continue
    v = btm.iloc[-1][c] / 1e8
    rzv = rz[c].iloc[-1] / 1e8
    lev = slev[c].iloc[-1]
    c5 = rz[c].pct_change(5).iloc[-1] * 100
    c10 = rz[c].pct_change(10).iloc[-1] * 100
    mx60 = rz[c].iloc[-60:].max()
    drop60 = (1 - rz[c].iloc[-1] / mx60) * 100 if mx60 and mx60 > 0 else np.nan
    rows.append((c, nmap.get(c, ""), v, rzv, lev, c5, c10, drop60))
    print(f"  {c} {nmap.get(c,''):<8} totmv {v:9.1f} | rzye {rzv:8.2f} | slev {lev:6.3f}% | c5 {c5:+7.2f}% | c10 {c10:+7.2f}% | dd60 {drop60:6.2f}%")

# ---- 行业（申万一级）自高点回撤 ----
im = pd.read_csv(os.path.join(D, "industry_map.csv")); imap = dict(zip(im.ts_code, im.l1_name))
ind_series = {}
for c in rz.columns:
    l1 = imap.get(c)
    if not l1:
        continue
    ind_series.setdefault(l1, []).append(rz[c])
print("\n== 行业融资余额自高点回撤（全窗口 max 与 60日 max）==")
ind_rows = []
for l1, cols in ind_series.items():
    agg = pd.concat(cols, axis=1).sum(axis=1)
    mx = agg.max(); dropp = (1 - agg.iloc[-1] / mx) * 100 if mx > 0 else np.nan
    mx60 = agg.iloc[-60:].max(); drop60 = (1 - agg.iloc[-1] / mx60) * 100 if mx60 > 0 else np.nan
    ind_rows.append((l1, agg.iloc[-1] / 1e8, mx / 1e8, dropp, drop60))
for r in sorted(ind_rows, key=lambda x: -x[3]):
    flagp = "【45~70】【去杠杆信号】" if 45 <= r[3] <= 70 else ""
    print(f"  {r[0]:<10} now {r[1]:9.1f}亿 peak {r[2]:9.1f}亿 | 自高点回撤 {r[3]:6.2f}% | 60日回撤 {r[4]:6.2f}% {flagp}")

# ---- 半导体个股回撤 45~70% ----
semi = sb[sb.industry.astype(str).str.contains("半导体", na=False)].ts_code.tolist()
print("\n半导体个股数", len(semi))
hits = []
for c in semi:
    if c not in rz.columns:
        continue
    mx = rz[c].max()
    if mx is None or mx <= 0 or pd.isna(mx):
        continue
    dropp = (1 - rz[c].iloc[-1] / mx) * 100
    if 45 <= dropp <= 70:
        hits.append((c, nmap.get(c, ""), rz[c].iloc[-1] / 1e8, dropp))
print("半导体 45~70% 命中数", len(hits))
for h in sorted(hits, key=lambda x: -x[3])[:20]:
    print(f"  {h[0]} {h[1]:<8} rzye {h[2]:7.2f}亿 回撤 {h[3]:6.2f}%")

# ---- ETF 份额周变动（5交易日），行业/主题部分 ----
esh = pd.read_csv(os.path.join(D, "etf_share.csv")); esh["trade_date"] = esh.trade_date.astype(int)
esh = esh[esh.trade_date <= LAST]
shp = esh.pivot_table(index="trade_date", columns="ts_code", values="fd_share").sort_index()
chg5 = shp.pct_change(5) * 100
print("\n== ETF 份额周变动（5交易日，<=%d）==" % LAST)
for c in shp.columns:
    print(f"  {c} {chg5[c].iloc[-1]:+7.2f}%")