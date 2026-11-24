# -*- coding: utf-8 -*-
"""T4-1：汇总股票级买点状态（缠论 + SMC）与实体级滚动统计"""
import pandas as pd

WORK = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5e6ceeb364ffeNZ3DY2SCHDLO20"
sig = pd.read_csv(WORK + "/chan_pool_signals.csv", dtype={"ts_code": str})
smc = pd.read_csv(WORK + "/smc_pool_live.csv", dtype={"ts_code": str})
pool = pd.read_csv(WORK + "/chan_smc_pool.csv", dtype={"ts_code": str})
ent = pd.read_csv(WORK + "/chan_smc_entities.csv", dtype=str)
NAME = dict(zip(pool.ts_code, pool.名称))

# 缠论：最近 30 个交易日内的买点，且其后无反向卖点
rows = []
for c, g in sig.groupby("ts_code"):
    buys = g[(g["方向"] == "买入") & (g["距末日K线"] <= 30)]
    sells = set(g[g["方向"] == "卖出"]["行号"])
    valid = [r for _, r in buys.iterrows() if not any(s > r["行号"] for s in sells)]
    if valid:
        b = max(valid, key=lambda r: r["行号"])
        rows.append({"ts_code": c, "名称": NAME.get(c, c), "缠论买点类型": b["类型"], "缠论买点日期": b["日期"],
                     "缠论买点价": b["价格"], "缠论距末日K线": int(b["距末日K线"]),
                     "缠论落在笔底": bool(b["落在笔底"]), "缠论向上笔已确认": bool(b["向上笔已确认"]),
                     "缠论向上笔确认日": b["向上笔确认日"], "缠论笔底价": b["笔底价"]})
chan = pd.DataFrame(rows)

smc_last = smc.sort_values("距末日K线").groupby("ts_code").first().reset_index()
smc_last = smc_last.rename(columns={
    "SMC类型": "SMC买点类型", "区间下沿ZD": "SMC区间下沿", "区间上沿ZG": "SMC区间上沿",
    "首次触碰日": "SMC首次触碰日", "买点日期": "SMC买点日期", "买点价(收盘)": "SMC买点价",
    "跌到价(触发日最低)": "SMC跌到价", "距末日K线": "SMC距末日K线",
    "BOS日期": "SMC_BOS日期", "区间形成日": "SMC区间形成日"})

m = pool[["ts_code", "名称", "行业", "所属入选实体数", "所属入选实体"]].merge(
    chan.drop(columns=["名称"]), on="ts_code", how="left").merge(
    smc_last[["ts_code", "SMC买点类型", "SMC区间下沿", "SMC区间上沿", "SMC_BOS日期", "SMC区间形成日",
              "SMC首次触碰日", "SMC买点日期", "SMC买点价", "SMC跌到价", "SMC距末日K线"]],
    on="ts_code", how="left")

def hit(r):
    a = pd.notna(r["缠论买点类型"]); b = pd.notna(r["SMC买点类型"])
    if a and b: return "缠论+SMC"
    if a: return "仅缠论"
    if b: return "仅SMC"
    return "无"

m["命中"] = m.apply(hit, axis=1)
m.to_csv(WORK + "/pool_buypoint_stock.csv", index=False, encoding="utf-8-sig")
print("池内 %d 只；命中分布：%s" % (len(m), m["命中"].value_counts().to_dict()))
print("缠论向上笔已确认 %d 只 / 待确认 %d 只" % (
    int((m["缠论向上笔已确认"] == True).sum()), int((m["缠论向上笔已确认"] == False).sum())))

ent_rows = []
for _, e in ent.iterrows():
    codes = [x.split("(")[1].rstrip(")") for x in str(e["合格股名单"]).split("、") if "(" in x]
    sub = m[m.ts_code.isin(codes)]
    ent_rows.append({"名称": e["名称"], "行业或概念": e["行业或概念"], "分数": e["分数"], "MAD": e["MAD"],
                     "MAD20": e["MAD20"], "本周等权": e["本周等权"], "合格股数": len(codes),
                     "有买点只数": int((sub["命中"] != "无").sum()),
                     "缠论买点只数": int(sub["缠论买点类型"].notna().sum()),
                     "SMC买点只数": int(sub["SMC买点类型"].notna().sum()),
                     "双命中只数": int((sub["命中"] == "缠论+SMC").sum()),
                     "有买点股票": "、".join(sub[sub["命中"] != "无"]["名称"].tolist())})
E = pd.DataFrame(ent_rows)
E.to_csv(WORK + "/pool_buypoint_entity.csv", index=False, encoding="utf-8-sig")
print("\n实体级 %d 个：有买点实体 %d 个；含双命中实体 %d 个" % (
    len(E), int((E["有买点只数"] > 0).sum()), int((E["双命中只数"] > 0).sum())))
print(E.sort_values(["双命中只数", "有买点只数"], ascending=False)[
    ["名称", "行业或概念", "分数", "合格股数", "有买点只数", "缠论买点只数", "SMC买点只数", "双命中只数"]].head(15).to_string(index=False))