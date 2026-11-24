import pandas as pd, numpy as np
df=pd.read_pickle("raw/_gm_panel.pkl")
bad=(df["d_sq"].abs()>40)|(df["m_Q2sq"].abs()>80)|(df["m_H1"]<-50)|(df["m_H1"]>100)
df=df[~bad].copy()
print("样本 %d  等权全市场: 近5日 %+.2f%%  近10日 %+.2f%%  近20日 %+.2f%%  近60日 %+.2f%%"%(
 len(df),df["r5"].mean()*100,df["r10"].mean()*100,df["r20"].mean()*100,df["r60"].mean()*100))
print("中位: 近20日 %+.2f%%  近60日 %+.2f%%"%(df["r20"].median()*100,df["r60"].median()*100))
print("上涨家数占比: 近5日 %.1f%%  近20日 %.1f%%"%((df["r5"]>0).mean()*100,(df["r20"]>0).mean()*100))
hi=df.nlargest(100,"r20"); lo=df.nsmallest(100,"r20")
for lb,s in [("近20日涨幅Top100",hi),("近20日跌幅Bottom100",lo)]:
    print(f"{lb}: 平均涨幅 {s['r20'].mean()*100:+.1f}% | 毛利率水平均值 {s['m_H1'].mean():.2f}% "
          f"中位 {s['m_H1'].median():.2f}% | 单季变动均值 {s['d_sq'].mean():+.2f}pct 中位 {s['d_sq'].median():+.2f}pct "
          f"| 改善占比 {(s['d_sq']>0).mean()*100:.1f}%")
print("\n涨幅Top100 行业分布:"); print(hi["ind"].value_counts().head(8).to_string())
print("\n涨幅Bottom100 行业分布:"); print(lo["ind"].value_counts().head(8).to_string())
# 分组：毛利率高/低 两组的近20日表现
q=pd.qcut(df["m_H1"],5,labels=["Q1低","Q2","Q3","Q4","Q5高"])
gg=df.groupby(q).agg(n=("r20","size"),近20日=("r20","mean"),近60日=("r60","mean"),近120日=("r120","mean"))
print("\n按H1毛利率五分组（毛利水平→收益）:"); print((gg[["n","近20日","近60日","近120日"]]*[1,100,100,100]).round(2).to_string())
q2=pd.qcut(df["d_sq"],5,labels=["Q1降","Q2","Q3","Q4","Q5升"])
gg2=df.groupby(q2).agg(n=("r20","size"),近20日=("r20","mean"),近60日=("r60","mean"),近120日=("r120","mean"),平均变动=("d_sq","mean"))
print("\n按单季毛利率变动五分组（变动→收益）:"); print((gg2[["n","近20日","近60日","近120日"]]*[1,100,100,100]).round(2).join(gg2["平均变动"].round(2)).to_string())
# 行业内五分位（中性化）
df["dec_in"]=df.groupby("ind")["r20"].rank(pct=True)
df["grp"]=pd.cut(df["dec_in"],[0,0.2,0.4,0.6,0.8,1.0],labels=["行业内后20%","20-40%","40-60%","60-80%","行业内前20%"])
gi=df.groupby("grp",observed=True).agg(n=("r20","size"),毛利水平=("m_H1","mean"),单季变动=("d_sq","mean"),改善占比=("d_sq",lambda s:(s>0).mean()))
gi["改善占比"]=(gi["改善占比"]*100).round(1)
print("\n行业内按近20日涨幅相对位次分组:"); print(gi.round(2).to_string())
