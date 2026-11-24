import pandas as pd, numpy as np
df=pd.read_pickle("raw/_gm_panel.pkl")
bad=(df["d_sq"].abs()>40)|(df["m_Q2sq"].abs()>80)|(df["m_H1"]<-50)|(df["m_H1"]>100)
df=df[~bad].copy()
d1=pd.read_csv("raw/daily_260d.csv",usecols=["ts_code","trade_date","pct_chg"],dtype={"ts_code":str})
d2=pd.read_csv("raw/daily_20261029.csv",usecols=["ts_code","trade_date","pct_chg"],dtype={"ts_code":str})
d=pd.concat([d1,d2]).drop_duplicates(["ts_code","trade_date"])
px=d.pivot(index="trade_date",columns="ts_code",values="pct_chg").sort_index()
R=(1+px/100.0); dates=list(px.index)
cols=[c for c in df.index if c in px.columns]
df=df.loc[cols]
def fwd(i,n):
    if i+1+n>len(R): return None
    return R.iloc[i+1:i+1+n][cols].prod()-1
def ic(a,b,ind=None):
    ok=a.notna()&b.notna()&(b.abs()<200)
    x,y=a[ok],b[ok]
    if len(x)<200: return np.nan
    if ind is None: return np.corrcoef(x.rank(pct=True),y.rank(pct=True))[0,1]
    g=ind[ok]
    return np.corrcoef(x.groupby(g).rank(pct=True),y.groupby(g).rank(pct=True))[0,1]
print("== 因子口径：2026-05-01~07-31 用 2026Q1 累计毛利率；2026-08 起用 2026H1 累计毛利率 ==")
print(f"{'截面日':<10}{'用口径':<8}{'毛利率水平IC_5d':>16}{'IC_20d':>9}{'行业内IC_20d':>14}{'变动IC_20d':>12}{'n':>7}")
# 每周一个截面（每5个交易日）
dates=[str(x) for x in dates]
idx=[i for i,t in enumerate(dates) if t>="20260506" and i%5==0]
res=[]
for i in idx:
    t=dates[i]
    if t<="20260731": f,dq=df["m_Q1"],df["m_Q1"]-df["m_Q4sq25"]
    else:             f,dq=df["m_H1"],df["d_sq"]
    r5,r20=fwd(i,5),fwd(i,20)
    if r20 is None: r20=pd.Series(np.nan,index=df.index)
    if r5 is None: r5=pd.Series(np.nan,index=df.index)
    r20=r20.reindex(df.index); r5=r5.reindex(df.index)
    a=ic(f,r5); b=ic(f,r20); c=ic(f,r20,df["ind"]); e=ic(dq,r20)
    lab="2026Q1" if t<="20260731" else "2026H1"
    n=int((f.notna()&r20.notna()).sum())
    res.append(dict(截面日=t,口径=lab,IC5=a,IC20=b,行业内IC20=c,变动IC20=e,n=n))
    print(f"{t:<10}{lab:<8}{a:>+16.3f}{b:>+9.3f}{c:>+14.3f}{e:>+12.3f}{n:>7d}")
t=pd.DataFrame(res)
print("\n汇总：水平IC_5d 均值 %+.3f（正%s/%s 次）| 水平IC_20d 均值 %+.3f（正%s/%s）| 行业内IC_20d 均值 %+.3f"%(
 t["IC5"].mean(),(t["IC5"]>0).sum(),t["IC5"].notna().sum(),t["IC20"].mean(),(t["IC20"]>0).sum(),t["IC20"].notna().sum(),t["行业内IC20"].mean()))
