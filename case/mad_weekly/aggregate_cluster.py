# -*- coding: utf-8 -*-
"""按行业与概念聚类：家数、平均/中位 MAD、平均现价偏离 MA40、MA40 上行占比、通过门槛家数。"""
import os
import sys
import csv
import json
import collections

WORK = os.path.dirname(os.path.abspath(__file__))
import numpy as np
import pandas as pd

uni = pd.read_csv(os.path.join(WORK, "universe_all.csv"), dtype={"ts_code": str})
ok = uni[uni.status == "ok"].copy()
ok["ma40_up"] = ok["ma40_up"].astype("boolean")
passed = ok[ok.above_ma40 & (ok.ma40_up == True)]
print("全样本 %d｜可算MAD %d｜通过门槛 %d" % (len(uni), len(ok), len(passed)))
BASE = float(ok.mad.mean())
print("全市场平均 MAD = %.4f（中位 %.4f）" % (BASE, float(ok.mad.median())))


def agg(df, keys, passed_df):
    g = df.groupby(keys, dropna=False)
    out = g.agg(家数=("mad", "size"), 平均MAD=("mad", "mean"), 中位MAD=("mad", "median"),
                标准差MAD=("mad", "std"), 平均现价高于MA40=("close_over_ma40_pct", "mean"),
                平均MA40五日斜率=("ma40_slope5_pct", "mean")).reset_index()
    p = passed_df.groupby(keys, dropna=False).agg(通过门槛家数=("mad", "size"), 通过股平均MAD=("mad", "mean")).reset_index()
    out = out.merge(p, on=keys, how="left")
    out["通过门槛家数"] = out["通过门槛家数"].fillna(0).astype(int)
    up = df.assign(_u=(df["ma40_up"] == True)).groupby(keys, dropna=False)["_u"].mean().reset_index(name="MA40上行占比")
    out = out.merge(up, on=keys, how="left")
    return out


# ---- 行业 ----
ind = agg(ok, ["industry"], passed).rename(columns={"industry": "行业"})
ind = ind.sort_values(["平均MAD", "家数"], ascending=False).reset_index(drop=True)
ind.insert(0, "序号", range(1, len(ind) + 1))
ind.to_csv(os.path.join(WORK, "industry_cluster.csv"), index=False, encoding="utf-8-sig")

# ---- 概念 ----
mem = pd.read_csv(os.path.join(WORK, "concept_members.csv"), dtype={"股票代码": str, "概念代码": str})
m = mem.merge(ok[["ts_code", "mad", "close_over_ma40_pct", "ma40_slope5_pct", "ma40_up", "above_ma40"]],
              left_on="股票代码", right_on="ts_code", how="inner")
con = agg(m, ["概念代码", "概念名称"], m[m.above_ma40 & (m.ma40_up == True)])
con = con.sort_values(["平均MAD", "家数"], ascending=False).reset_index(drop=True)
con.insert(0, "序号", range(1, len(con) + 1))
con.to_csv(os.path.join(WORK, "concept_cluster.csv"), index=False, encoding="utf-8-sig")

# 概念成员覆盖
mem_uni = mem[mem["股票代码"].isin(set(ok.ts_code))].groupby("概念名称")["股票代码"].nunique()
summary = {
    "行业类别数": int(ind.shape[0]),
    "行业类别_含股票": int((ind["家数"] > 0).sum()),
    "概念类别数": int(con.shape[0]),
    "概念类别_含股票": int((con["家数"] > 0).sum()),
    "全市场可算MAD家数": int(len(ok)),
    "全市场平均MAD": round(BASE, 4),
    "全市场MAD中位数": round(float(ok.mad.median()), 4),
    "平均家数_行业": round(float(ind["家数"].mean()), 1),
    "平均家数_概念": round(float(con["家数"].mean()), 1),
    "概念覆盖股票数": int(mem["股票代码"].nunique()),
    "有概念归属的可算MAD股票": int(m["股票代码"].nunique()),
    "行业平均MAD最高": ind.head(5)[["行业", "家数", "平均MAD"]].to_dict("records"),
    "行业平均MAD最低": ind.tail(5)[["行业", "家数", "平均MAD"]].to_dict("records"),
    "概念平均MAD最高": con.head(8)[["概念名称", "家数", "平均MAD", "通过门槛家数"]].to_dict("records"),
    "概念平均MAD最低": con.tail(5)[["概念名称", "家数", "平均MAD"]].to_dict("records"),
    "平均MAD高于全市场均值的概念数": int((con["平均MAD"] > BASE).sum()),
    "基准日": "20261030",
}
with open(os.path.join(WORK, "cluster_summary.json"), "w", encoding="utf-8") as f:
    json.dump(summary, f, ensure_ascii=False, indent=1, default=float)
print(json.dumps(summary, ensure_ascii=False, indent=1, default=float))
pd.set_option("display.width", 220)
print(ind.head(15).to_string(index=False))
print(con.head(15).to_string(index=False))