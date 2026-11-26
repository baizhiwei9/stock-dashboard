#!/usr/bin/env python3
"""股指期货升贴水监控 — 当季/下季双合约"""
import warnings; warnings.filterwarnings('ignore')
import numpy as np, pandas as pd
from datetime import datetime, timedelta
import tushare as ts

pro = ts.pro_api()

PRODUCTS = {
    'IF': {'idx': '000300.SH', 'name': '沪深300'},
    'IC': {'idx': '000905.SH', 'name': '中证500'},
    'IM': {'idx': '000852.SH', 'name': '中证1000'},
}
QUARTERS = [3,6,9,12]

def third_friday(y,m):
    d = datetime(y,m,1)
    f = d + timedelta(days=(4-d.weekday())%7)
    return (f+timedelta(weeks=2)).strftime('%Y%m%d')

def find_cq_nq(date_str):
    td = datetime.strptime(date_str,'%Y%m%d')
    cy,cm = td.year, td.month
    qs = []
    for off in range(0,30):
        y = cy+(cm-1+off)//12; m = (cm-1+off)%12+1
        if m in QUARTERS:
            if third_friday(y,m) >= date_str:
                qs.append(f'{y}{m:02d}')
            if len(qs)>=2: break
    return (qs[0],qs[1]) if len(qs)>=2 else (qs[0] if qs else None, None)

end = datetime.now().strftime('%Y%m%d')
start = (datetime.now()-timedelta(days=700)).strftime('%Y%m%d')

rows = []
for p, info in PRODUCTS.items():
    # spot
    sdf = pro.index_daily(ts_code=info['idx'], start_date=start, end_date=end)
    sdf = sdf.sort_values('trade_date').reset_index(drop=True)
    sdf['trade_date'] = sdf['trade_date'].astype(str)
    spot = dict(zip(sdf['trade_date'], sdf['close']))
    dates = sorted(spot.keys())

    # collect needed ym
    needed = set()
    for d in dates:
        cq,nq = find_cq_nq(d)
        if cq: needed.add(cq)
        if nq: needed.add(nq)

    # fetch futures
    fut = {}
    for ym in sorted(needed):
        tc = f'{p}{ym[2:]}.CFX'
        df = pro.fut_daily(ts_code=tc, start_date=start, end_date=end)
        if df is not None and len(df)>0:
            df = df.sort_values('trade_date').reset_index(drop=True)
            df['trade_date'] = df['trade_date'].astype(str)
            fut[ym] = df

    for which in ['cq','nq']:
        label = '当季' if which=='cq' else '下季'
        recs = []
        for d in dates:
            cq_ym, nq_ym = find_cq_nq(d)
            ym = cq_ym if which=='cq' else nq_ym
            if ym is None or ym not in fut: continue
            day = fut[ym][fut[ym]['trade_date']==d]
            if len(day)==0 or d not in spot: continue
            exp = third_friday(int(ym[:4]),int(ym[4:]))
            dte = (datetime.strptime(exp,'%Y%m%d')-datetime.strptime(d,'%Y%m%d')).days
            recs.append({
                'date':d, 'contract':f'{p}{ym[2:]}', 'F_close':day['close'].values[0],
                'S_close':spot[d], 'volume':day['vol'].values[0], 'oi':day['oi'].values[0], 'DTE':max(dte,0)
            })
        if not recs: continue
        df = pd.DataFrame(recs)

        # Panama Canal
        chg = df['contract']!=df['contract'].shift(1)
        cum=0; adjs=[]
        for i in range(len(df)):
            if i>0 and chg.iloc[i]: cum+=df.iloc[i-1]['F_close']-df.iloc[i]['F_close']
            adjs.append(cum)
        df['adj_F'] = df['F_close']+pd.Series(adjs)

        # basis rate
        df['basis_rate'] = (df['F_close']/df['S_close']-1)*(365.0/df['DTE'].clip(lower=1))
        df.loc[(df['volume']==0)|(df['oi']==0),'basis_rate']=np.nan
        df['delivery_week'] = df['DTE']<5
        df['br_clean'] = df['basis_rate'].where(~df['delivery_week'], np.nan)

        # rank_252
        df['rank_252'] = df['br_clean'].rolling(252,min_periods=60).apply(
            lambda x: pd.Series(x).rank(pct=True).iloc[-1] if len(x.dropna())>=30 else np.nan, raw=False)

        # z_score_90 (中位数去均值抗异常值)
        df['med90'] = df['br_clean'].rolling(90,min_periods=30).median()
        df['resid'] = df['br_clean']-df['med90']
        df['std90'] = df['resid'].rolling(90,min_periods=30).std()
        df['z90'] = df['resid']/df['std90']

        L = df.iloc[-1]
        r = L['rank_252']; z = L['z90']
        if pd.isna(r) or pd.isna(z): sig='数据不足'
        elif r<0.05 and z<-2.0: sig='🔴 极端恐慌(做多)'
        elif r<0.10 and z<-1.5: sig='🟠 深度贴水(观察)'
        elif r>0.95 and z>2.0: sig='🟣 异常收敛(做空)'
        elif r<0.25 and z<-0.5: sig='🟡 温和贴水'
        elif r>0.75 and z>0.5: sig='🟢 温和升水'
        else: sig='⚪ 中性'

        rows.append({
            '品种':p,'名称':info['name'],'类型':label,
            '合约':L['contract'],'DTE':L['DTE'],
            '期货':L['F_close'],'现货':L['S_close'],
            '价差':L['F_close']-L['S_close'],'年化基差率':L['basis_rate'],
            'rank_252':r,'z_score':z,'信号':sig
        })

# output
print(f"{'品种':<5} {'类型':<5} {'合约':<9} {'DTE':>4} {'价差':>8} {'年化基差率':>11} {'rank':>7} {'Z':>7} {'信号'}")
print('-'*75)
for r in rows:
    rk=f"{r['rank_252']:.4f}" if not pd.isna(r['rank_252']) else 'N/A'
    zs=f"{r['z_score']:+.2f}" if not pd.isna(r['z_score']) else 'N/A'
    print(f"{r['品种']:<5} {r['类型']:<5} {r['合约']:<9} {r['DTE']:>4} {r['价差']:>+8.1f} {r['年化基差率']*100:>+10.4f}% {rk:>7} {zs:>7} {r['信号']}")
