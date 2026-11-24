# -*- coding: utf-8 -*-
"""汇总：修正背驰配对 + 右侧确认后的三层修订名单与计数（修正 NaN 布尔陷阱）。"""
import pandas as pd, numpy as np, json
BASE="/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ed3f1d6b1ffeo164XKTVu7vvVA"
OUT=f"{BASE}/output"
pd.set_option("display.width", 240); pd.set_option("display.max_columns", 60)

v3=pd.read_csv(f"{OUT}/chan_confirm_v3_fixed_backshift.csv")
def tb(x): return x is True or (isinstance(x,str) and x.lower()=="true") or x==True
valid=v3["仍有效"].apply(lambda x: (x is True) or (isinstance(x,bool) and x))
c1=v3["C1"].apply(lambda x: x is True or x==True); c2=v3["C2"].apply(lambda x: x is True or x==True); c3=v3["C3"].apply(lambda x: x is True or x==True)
hbuy=v3["买点类型"].isin(["B1","B2","B3"])

def cls(i):
    if valid.iloc[i]:
        if c1.iloc[i] or c2.iloc[i]: return "A1"
        if c3.iloc[i]: return "A2"
        return "B"
    if hbuy.iloc[i]: return "D"
    if v3["修正后档位"].iloc[i]=="C": return "C"
    return "E"
v3["级别"]=[cls(i) for i in range(len(v3))]

print("===== 修订后三层计数 =====")
print("级别说明：A1=买点仍有效且当前转强(MA10>MA20 或 连涨≥3)；A2=买点仍有效、买点后曾走出向上笔但当前已走弱；\n"
      "          B=买点仍有效但右侧未确认；C=无近40日买点仅当前突破最近中枢上沿；D=近40日买点已失效；E=无买点")
for lv in ["行业","主题","概念"]:
    s=v3[v3["层级"]==lv]
    tot={k:int((s['级别']==k).sum()) for k in ["A1","A2","B","C","D","E"]}
    print(f"{lv}: 候选{len(s):3d}  形态买点有效(右侧已确认)={tot['A1']+tot['A2']}  {tot}")
print(f"合计: 候选{len(v3)}  A1={int((v3['级别']=='A1').sum())} A2={int((v3['级别']=='A2').sum())} "
      f"B={int((v3['级别']=='B').sum())} C={int((v3['级别']=='C').sum())} D={int((v3['级别']=='D').sum())} E={int((v3['级别']=='E').sum())}")

cols=["层级","名称","因子名次","买点类型","买点日期","买点距今天","最新收盘","MA10MA20差pct","末端连涨根数","C1","C2","C3"]
for tag,label in [("A1","【A1】买点仍有效 + 当前转强（MA10>MA20 或 连涨≥3）—— 修订后可关注"),
                  ("A2","【A2】买点仍有效 + 买点后曾走出向上笔，但当前已走弱（仅凭C3）"),
                  ("B","【B】买点仍有效但右侧未获任何确认"),
                  ("C","【C】无近40日买点，仅当前向上突破最近中枢上沿（三买雏形）")]:
    s=v3[v3["级别"]==tag].sort_values(["层级","因子名次"])
    print(f"\n===== {label}｜合计 {len(s)} 个 =====")
    if tag=="C":
        print(s[["层级","名称","因子名次","最新收盘","MA10MA20差pct"]].to_string(index=False))
    else:
        print(s[cols].to_string(index=False))

print("\n===== 点名板块 =====")
v2=pd.read_csv(f"{OUT}/chan_confirm_v2.csv"); v2=v2[v2["状态"]=="ok"].copy()
for nm in ["国产航母","培育钻石","电机","航空","石油开采","电信运营","矿物制品","银行","玻璃"]:
    a=v3[v3["名称"]==nm]; b=v2[v2["名称"]==nm]
    for _,r in a.iterrows():
        oldt=b["近40日买点类型"].astype(str).replace("nan","None").iloc[0]
        oldd=b["近40日买点日期"].astype(str).replace("nan","-").iloc[0]
        print(f"{r['层级']}/{nm}: v2={oldt}@{oldd} → v3={r['买点类型']}/{r['买点日期']} 级别={r['级别']} "
              f"MA10MA20={r['MA10MA20差pct']:+.2f}% 连涨={r['末端连涨根数']} C1={r['C1']} C2={r['C2']} C3={r['C3']}")

print("\n===== 背驰配对修正带来的买点变化（v2 vs v3）=====")
m=v2.merge(v3,on=["层级","名称"],how="inner",suffixes=("_v2","_v3"))
m["old"]=m["近40日买点类型"].astype(str).replace("nan","None")
m["new"]=m["买点类型"].astype(str).replace("nan","None")
ch=m[m["old"]!=m["new"]]
print(f"买点类型/有无发生变化: {len(ch)} / {len(m)} 个")
print("变化为『原有一买(B1)→修正后无买点』的:")
print(ch[(ch['old']=='B1')&(ch['new']=='None')][["层级","名称","近40日买点日期","级别","MA10MA20差pct"]].to_string(index=False))
print("\n变化为『其它』的（前30）:")
print(ch[(ch['old']=='B1')&(ch['new']=='None')==False][["层级","名称","old","new","近40日买点日期","买点日期"]].head(30).to_string(index=False))
v3.to_csv(f"{OUT}/chan_revise_v3_rank.csv",index=False,encoding="utf-8-sig")
print("\n[saved] output/chan_revise_v3_rank.csv")