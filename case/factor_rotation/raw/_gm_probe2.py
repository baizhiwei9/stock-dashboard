import pandas as pd, numpy as np
pd.set_option("display.width",220)
df0=pd.read_pickle("raw/_gm_panel.pkl")
print("样本:",len(df0))
# 稳健化：剔除单季毛利率/变动明显异常（负营收差额导致的分母异常）
bad=(df0["d_sq"].abs()>40)|(df0["m_Q2sq"].abs()>80)|(df0["m_H1"]<-50)|(df0["m_H1"]>100)
print("剔除异常毛利率样本:",int(bad.sum()))
print(df0.loc[bad,"sb_name"].head(15).tolist())
df=df0[~bad].copy()
print("最终样本:",len(df))

def ic(s,a,b,demean=None):
    x,y=s[a],s[b]; ok=x.notna()&y.notna(); x,y=x[ok],y[ok]
    if len(x)<50: return np.nan,0
    if demean is None: rx,ry=x.rank(pct=True),y.rank(pct=True)
    else: rx,ry=x.groupby(s.loc[ok,demean]).rank(pct=True),y.groupby(s.loc[ok,demean]).rank(pct=True)
    return np.corrcoef(rx,ry)[0,1],len(x)

print("\n=== 表1 截面 Spearman 相关（截至 2026-10-29，n=%d）==="%len(df))
rows=[]
for f,lab in [("m_H1","毛利率水平(H1)"),("m_Q2sq","Q2单季毛利率"),("d_sq","毛利率单季变动"),("d_cum","毛利率累计变动")]:
    for r,lab2 in [("r5","近5日"),("r10","近10日"),("r20","近20日"),("r60","近60日"),("r120","近120日"),("r_sep","9月以来"),("r_mayjun","5-6月(披露前)")]:
        a,_=ic(df,f,r); b,_=ic(df,f,r,demean="ind")
        rows.append((f,lab,lab2,a,b))
t1=pd.DataFrame(rows,columns=["字段","因子","收益窗口","原始","行业内"])
p=t1.pivot_table(index="收益窗口",columns="因子",values=["原始","行业内"])
order=["近5日","近10日","近20日","近60日","近120日","9月以来","5-6月(披露前)"]
print(t1.pivot(index="收益窗口",columns="因子",values="原始").reindex(order).round(3).to_string())
print("-- 行业内（同花顺行业中性）--")
print(t1.pivot(index="收益窗口",columns="因子",values="行业内").reindex(order).round(3).to_string())

print("\n=== 表2 按近20日涨幅十分位（稳健统计）===")
df["dec"]=pd.qcut(df["r20"],10,labels=False)+1
g=df.groupby("dec").agg(n=("r20","size"),
  涨幅中位=("r20","median"), 毛利水平均值=("m_H1","mean"), 毛利水平中位=("m_H1","median"),
  单季毛利均值=("m_Q2sq","mean"), 变动均值=("d_sq","mean"), 变动中位=("d_sq","median"),
  改善占比=("d_sq",lambda s:(s>0).mean()))
g["涨幅中位"]=(g["涨幅中位"]*100).round(2); g["改善占比"]=(g["改善占比"]*100).round(1)
print(g.round(2).to_string())
print("D10-D1: 毛利水平 %.2f / 单季变动(均值) %.2f / 单季变动(中位) %.2f / 改善占比 %.1f pct"%(
 g["毛利水平均值"].iloc[-1]-g["毛利水平均值"].iloc[0],
 g["变动均值"].iloc[-1]-g["变动均值"].iloc[0],
 g["变动中位"].iloc[-1]-g["变动中位"].iloc[0],
 g["改善占比"].iloc[-1]-g["改善占比"].iloc[0]))

print("\n=== 表3 全市场单季毛利率改善/恶化 两组的近20日涨幅 ===")
up,dn=df[df["d_sq"]>0],df[df["d_sq"]<=0]
print(f"改善组 n={len(up)} 平均近20日 {up['r20'].mean()*100:+.2f}% 中位 {up['r20'].median()*100:+.2f}%")
print(f"恶化组 n={len(dn)} 平均近20日 {dn['r20'].mean()*100:+.2f}% 中位 {dn['r20'].median()*100:+.2f}%")

print("\n=== 表4 近20日涨幅 Top30 中毛利率变动分布 ===")
t=df.nlargest(30,"r20")
t["r20"]=(t["r20"]*100).round(1)
print(t[["sb_name","ind","r20","m_H1","m_Q2sq","d_sq"]].round(2).to_string(index=False))
print("Top30 中 毛利率改善 %d 只 / 恶化 %d 只"%( (t["d_sq"]>0).sum(),(t["d_sq"]<=0).sum()))
print("全部样本 改善占比 %.1f%%"%((df["d_sq"]>0).mean()*100))

print("\n=== 表5 行业层面：近20日行业涨幅 vs 行业毛利率水平/变动 ===")
gi=df.groupby("ind").agg(n=("r20","size"),涨幅=("r20","mean"),毛利水平=("m_H1","mean"),
    单季变动=("d_sq","mean"),改善占比=("d_sq",lambda s:(s>0).mean()))
gi=gi[gi["n"]>=15]
gi["涨幅"]=(gi["涨幅"]*100).round(2); gi["改善占比"]=(gi["改善占比"]*100).round(1)
print("行业数:",len(gi))
print("行业涨幅 vs 行业毛利率水平: %.3f | vs 行业单季变动: %.3f | vs 改善占比: %.3f"%(
 gi["涨幅"].corr(gi["毛利水平"]), gi["涨幅"].corr(gi["单季变动"]), gi["涨幅"].corr(gi["改善占比"])))
print("\n-- 涨幅前10行业 --"); print(gi.sort_values("涨幅",ascending=False).head(10).round(2).to_string())
print("\n-- 涨幅后10行业 --"); print(gi.sort_values("涨幅").head(10).round(2).to_string())
