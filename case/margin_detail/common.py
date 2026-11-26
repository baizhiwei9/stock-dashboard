# -*- coding: utf-8 -*-
import os, json
import numpy as np, pandas as pd
D=os.path.join(os.path.dirname(os.path.abspath(__file__)),"data")
def load_days(): return json.load(open(os.path.join(D,"trade_days.json")))
def close_pivot():
    db=pd.read_csv(os.path.join(D,"daily_basic.csv"),usecols=["ts_code","trade_date","close"])
    db["trade_date"]=db["trade_date"].astype(int)
    p=db.pivot_table(index="trade_date",columns="ts_code",values="close").sort_index()
    return p
def pct_rank(series, window=120):
    """trailing-window percentile (0-100) of the LAST value"""
    s=series.dropna()
    if len(s)<2: return np.nan
    w=s.iloc[-window:] if len(s)>=window else s
    v=w.iloc[-1]
    return float((w<=v).mean()*100)
def run_len(mask):
    """number of consecutive True values ending at the last element"""
    m=list(mask)
    n=0
    for x in reversed(m):
        if bool(x): n+=1
        else: break
    return n
