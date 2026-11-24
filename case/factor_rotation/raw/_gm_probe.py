import pandas as pd, numpy as np
pd.set_option("display.width",200)

# ---- 1. 行情：pct_chg 复权口径累积 ----
d1=pd.read_csv("raw/daily_260d.csv",usecols=["ts_code","trade_date","pct_chg"],dtype={"ts_code":str})
d2=pd.read_csv("raw/daily_20261029.csv",usecols=["ts_code","trade_date","pct_chg"],dtype={"ts_code":str})
d=pd.concat([d1,d2]).drop_duplicates(["ts_code","trade_date"])
px=d.pivot(index="trade_date",columns="ts_code",values="pct_chg").sort_index()
print("行情截面:",px.shape[0],"个交易日",px.index[0],"~",px.index[-1],"股票数",px.shape[1])
R=(1+px/100.0)
def cumret(n,offset=0):
    s=R.iloc[len(R)-offset-n:len(R)-offset] if offset else R.iloc[-n:]
    return s.prod()-1
ret=pd.DataFrame({
 "r5":cumret(5),"r10":cumret(10),"r20":cumret(20),"r60":cumret(60),"r120":cumret(120),
 "r_mayjun":cumret(35,offset=80),   # 2026-05下旬~06末（半年报披露前）
 "r_sep":cumret(32,offset=0),       # 最近约1.5个月
})
ret.index.name="ts_code"

# ---- 2. 毛利率（同花顺业绩报表口径）----
def yjbb(f):
    x=pd.read_csv(f"raw/{f}.csv",dtype={"股票代码":str})
    x["code"]=x["股票代码"].str.zfill(6)
    x=x.set_index("code")
    return x
q3a,y0,q1,q2 = yjbb("yjbb_2025Q3"),yjbb("yjbb_2025A"),yjbb("yjbb_2026Q1"),yjbb("yjbb_2026Q2")
def num(s): return pd.to_numeric(s,errors="coerce")
gm=pd.DataFrame(index=q2.index)
gm["m_H1"]=num(q2["销售毛利率"])                       # 2026H1 累计毛利率
gm["rev_H1"]=num(q2["营业总收入-营业总收入"])
gm["m_Q1"]=num(q1["销售毛利率"])
gm["rev_Q1"]=num(q1["营业总收入-营业总收入"])
gm["m_9M25"]=num(q3a["销售毛利率"]); gm["rev_9M25"]=num(q3a["营业总收入-营业总收入"])
gm["m_FY25"]=num(y0["销售毛利率"]);   gm["rev_FY25"]=num(y0["营业总收入-营业总收入"])
gm["ind"]=q2["所处行业"]
gm["name"]=q2["股票简称"]
# 单季毛利率
gm["m_Q2sq"]=(gm["rev_H1"]*gm["m_H1"]-gm["rev_Q1"]*gm["m_Q1"])/(gm["rev_H1"]-gm["rev_Q1"])
gm["m_Q4sq25"]=(gm["rev_FY25"]*gm["m_FY25"]-gm["rev_9M25"]*gm["m_9M25"])/(gm["rev_FY25"]-gm["rev_9M25"])
gm["d_sq"]=gm["m_Q2sq"]-gm["m_Q1"]        # 单季环比（Q2单季 vs Q1单季）
gm["d_cum"]=gm["m_H1"]-gm["m_Q1"]         # 累计口径差（对照）
gm["d_25q4"]=gm["m_Q4sq25"]-gm["m_9M25"]  # 2025Q4单季 vs 前三季
# ts_code
sb=pd.read_csv("raw/stock_basic.csv",dtype={"symbol":str,"ts_code":str})
sb["symbol"]=sb["symbol"].str.zfill(6)
sb=sb.drop_duplicates("symbol").set_index("symbol")
gm["ts_code"]=[sb["ts_code"].get(c) for c in gm.index]
gm["ts_ind"]=[sb["industry"].get(c) for c in gm.index]
gm["sb_name"]=[sb["name"].get(c) for c in gm.index]
gm=gm[gm["ts_code"].notna()].set_index("ts_code")

df=ret.join(gm,how="inner")
print("合并后:",df.shape)
# 过滤：剔除 ST、北交所、上市不足120日、毛利率缺失
n0=len(df)
df["is_st"]=df["sb_name"].astype(str).str.contains("ST",na=False)
df=df[~df["is_st"]]
df=df[~df.index.str.endswith(".BJ")]
df=df[df["r120"].notna() & df["r20"].notna()]
print(f"过滤：ST/北交所/新股剔除 {n0}->{len(df)}")
dfn=df.dropna(subset=["m_H1","d_sq"]).copy()
print("毛利率可用样本:",len(dfn))

def ic(a,b,sub=None,demean=None):
    s=dfn if sub is None else dfn[sub]
    x,y=s[a],s[b]; ok=x.notna()&y.notna(); x,y=x[ok],y[ok]
    if len(x)<50: return np.nan,0
    rx,ry=(x.rank(pct=True),y.rank(pct=True)) if demean is None else (
        x.groupby(s.loc[ok,demean]).rank(pct=True),y.groupby(s.loc[ok,demean]).rank(pct=True))
    return np.corrcoef(rx,ry)[0,1],len(x)

print("\n=== 表1 截面 Spearman 相关（2026-10-29 截止）===")
rows=[]
for f in ["m_H1","d_sq","d_cum","m_Q2sq"]:
    for r in ["r5","r10","r20","r60","r120","r_sep","r_mayjun"]:
        a,na=ic(f,r); b,_=ic(f,r,demean="ind")
        rows.append((f,r,a,na,b))
t1=pd.DataFrame(rows,columns=["因子","收益窗口","原始相关","n","行业内相关"])
print(t1.to_string(index=False,float_format=lambda v:f"{v:+.3f}"))

print("\n=== 表2 按近20日涨幅分组（十分位）毛利率 ===")
dfn["dec"]=pd.qcut(dfn["r20"],10,labels=False)+1
g=dfn.groupby("dec").agg(n=("r20","size"),近20日涨幅=("r20","mean"),
   毛利率水平=("m_H1","mean"),单季毛利率=("m_Q2sq","mean"),毛利率单季变动=("d_sq","mean"),
   毛利率累计变动=("d_cum","mean"))
g["近20日涨幅"]=(g["近20日涨幅"]*100).round(2).astype(str)+"%"
print(g.round(2).to_string())
print("\n组间极差：毛利率水平 D10-D1 = %.2f pct；单季变动 D10-D1 = %.2f pct"%(
 g["毛利率水平"].iloc[-1]-g["毛利率水平"].iloc[0], g["毛利率单季变动"].iloc[-1]-g["毛利率单季变动"].iloc[0]))

print("\n=== 表3 近20日涨幅 Top20 的毛利率 ===")
top=dfn.nlargest(20,"r20")[["sb_name","ind","r20","m_H1","m_Q2sq","d_sq","d_cum"]]
top["r20"]=(top["r20"]*100).round(1)
print(top.rename(columns={"sb_name":"名称","ind":"行业","r20":"近20日%","m_H1":"H1毛利率","m_Q2sq":"Q2单季毛利率","d_sq":"单季变动","d_cum":"累计变动"}).round(2).to_string(index=False))

print("\n=== 表4 2025Q4单季毛利率变动 vs 2026年至今涨幅 ===")
d2_=dfn.dropna(subset=["d_25q4"])
for r in ["r20","r60","r120"]:
    x,y=d2_["d_25q4"],d2_[r]
    rk=np.corrcoef(x.rank(pct=True),y.rank(pct=True))[0,1]
    rn=np.corrcoef(x.groupby(d2_["ind"]).rank(pct=True),y.groupby(d2_["ind"]).rank(pct=True))[0,1]
    print(f"  {r}: 原始 {rk:+.3f} | 行业内 {rn:+.3f}  (n={len(d2_)})")
dfn.to_pickle("raw/_gm_panel.pkl")
