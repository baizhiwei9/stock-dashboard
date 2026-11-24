# -*- coding: utf-8 -*-
"""行业轮动机会风险加权汇总（三方法：TDA结构风险 / 开源时序轮动 / STMOM 21日动量）
规则：每个方法的机会信号权重=1、风险信号权重=1，对申万一级行业汇总。
输出：
  1) l1_combined.csv —— 一级行业：机会分/风险分/净分/横截面动量排名/方法明细
  2) l3_drilldown.csv —— 机会一级下的申万三级下钻（TDA 机会+机会观察映射）
  3) combined.json —— 供 HTML 渲染
"""
import json
import os

import numpy as np
import pandas as pd

BASE = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace"
TDA_RUN = os.path.join(BASE, "tda_run")
REPORT = os.path.join(BASE, "report_run")

# ---------------- 1. 申万分类映射：三级指数码 -> 一级行业名 ----------------
l1_meta = json.load(open(os.path.join(TDA_RUN, "L1_meta.json")))
l3_meta = json.load(open(os.path.join(TDA_RUN, "L3_meta.json")))
l1_by_code = {m["industry_code"]: m["industry_name"] for m in l1_meta}
l3_index2l1 = {}
for m in l3_meta:
    head = m["industry_code"][:2] + "0000"
    l3_index2l1[m["index_code"]] = l1_by_code.get(head, "未知")
# L2 也建映射(机会观察中有 L2/L1 单元)——用 results 里 name/level 反查
l2_meta = json.load(open(os.path.join(TDA_RUN, "L2_meta.json")))
l2_index2l1 = {}
for m in l2_meta:
    head = m["industry_code"][:2] + "0000"
    l2_index2l1[m["index_code"]] = l1_by_code.get(head, "未知")
L1_NAME_SET = {m["industry_name"] for m in l1_meta}


def tda_code_to_l1(code, level):
    """results 中 code 可能是 L3/L2/L1 指数码，映射到一级行业名。"""
    if level == "L3":
        return l3_index2l1.get(code, "未知")
    if level == "L2":
        return l2_index2l1.get(code, "未知")
    if level == "L1":
        # L1 指数码如 801010.SI -> 一级名：从 sw_l1_daily.csv 或 L1_meta index_code
        for m in l1_meta:
            if m["index_code"] == code:
                return m["industry_name"]
    return "未知"


# ---------------- 2. TDA 信号（三级/二级/一级粒度 -> 一级票数） ----------------
tda = json.load(open(os.path.join(TDA_RUN, "results.json")))
tda_l1_opp, tda_l1_risk = {}, {}
tda_l3_opp_list, tda_l3_risk_list = [], []
for ind in tda["industries"]:
    l1 = tda_code_to_l1(ind.get("code", ""), ind.get("level", ""))
    if l1 == "未知":
        continue
    lbl = ind.get("label", "")
    nm = ind.get("name", "")
    lv = ind.get("level", "")
    if lbl in ("机会", "机会观察"):
        tda_l1_opp[l1] = tda_l1_opp.get(l1, 0) + 1
        tda_l3_opp_list.append({"name": nm, "l1": l1, "label": lbl, "level": lv})
    elif lbl in ("风险", "风险观察"):
        tda_l1_risk[l1] = tda_l1_risk.get(l1, 0) + 1
        tda_l3_risk_list.append({"name": nm, "l1": l1, "label": lbl, "level": lv})

# ---------------- 3. 开源时序轮动信号（申万一级 30 行业） ----------------
open_rows = pd.read_csv(os.path.join(REPORT, "行业轮动数据明细_20260928.csv"))
open_opp = set(open_rows.loc[open_rows["标记"].str.contains("★"), "行业"])
open_risk = set(open_rows.loc[open_rows["标记"].str.contains("🔴"), "行业"])
print("开源机会:", sorted(open_opp))
print("开源风险:", sorted(open_risk))

# ---------------- 4. STMOM 21日动量信号（行业ETF B池 -> 申万一级） ----------------
ETF2L1 = {
    "银行ETF华宝": "银行", "银行ETF汇添富": "银行",
    "房地产ETF南方": "房地产", "军工ETF国泰": "国防军工",
    "医药ETF易方达": "医药生物", "食品饮料ETF华夏": "食品饮料",
    "消费ETF汇添富": "食品饮料", "生物医药ETF汇添富": "医药生物",
    "生物医药ETF国泰": "医药生物", "传媒ETF广发": "传媒",
    "芯片ETF国泰": "电子", "芯片ETF广发": "电子", "芯片ETF华夏": "电子",
    "通信ETF华夏": "通信", "光伏ETF华泰柏瑞": "电力设备",
    "环保ETF广发": "环保", "新能源ETF南方": "电力设备",
    "新能源车ETF博时": "汽车", "新能源车ETF华夏": "汽车",
    "有色金属ETF南方": "有色金属",
}
st = json.load(open(os.path.join(BASE, "ses_gffe5f330c6707ffeqvFIsPha0gz5nv",
                                 "daily_momentum_out", "signal_2026-09-28.json")))
stom_opp, stom_risk = {}, {}
for x in st["B_top10"]:
    l1 = ETF2L1.get(x["name"])
    if l1:
        stom_opp[l1] = stom_opp.get(l1, 0) + 1
for x in st["B_bottom10"]:
    l1 = ETF2L1.get(x["name"])
    if l1:
        stom_risk[l1] = stom_risk.get(l1, 0) + 1
print("STMOM 机会覆盖:", sorted(stom_opp.keys()))
print("STMOM 风险覆盖:", sorted(stom_risk.keys()))

# ---------------- 5. 一级行业横截面动量排名（用 sw_l1_daily 最新截面） ----------------
l1 = pd.read_csv(os.path.join(TDA_RUN, "sw_l1_daily.csv"), parse_dates=["date"])
l1w = l1.pivot_table(index="date", columns="name", values="close").sort_index()
last = l1w.index[-1]
ret = l1w.pct_change()
mom20 = (l1w.loc[last] / l1w.loc[last - 20 * pd.Timedelta(days=40):].iloc[-21] - 1) if len(l1w) > 21 else np.nan
# 直接用 shift 更稳
mom20 = l1w / l1w.shift(20) - 1
mom60 = l1w / l1w.shift(60) - 1
strength = ret.clip(lower=0).abs().rolling(14).sum() / ret.abs().rolling(14).sum()
dnow = l1w.loc[last]
m20 = mom20.loc[last]
m60 = mom60.loc[last]
st_now = strength.loc[last]


def zscore_s(s):
    return (s - s.mean()) / s.std()


score = (zscore_s(m20) + zscore_s(m60) + zscore_s(st_now)) / 3
mom_rank = score.rank(ascending=False, method="min").astype(int)
mom_order = score.sort_values(ascending=False)
print("最新交易日:", last.date(), "| 一级行业数:", len(l1w.columns))
print("横截面动量 Top5:", list(mom_order.index[:5]))
print("横截面动量 Bottom5:", list(mom_order.index[-5:]))

# ---------------- 6. 汇总一级表 ----------------
all_l1 = set(open_opp) | set(open_risk) | set(tda_l1_opp) | set(tda_l1_risk) \
    | set(stom_opp) | set(stom_risk)
rows = []
for l1n in sorted(all_l1, key=lambda x: -mom_rank.get(x, 99)):
    opp = (1 if l1n in open_opp else 0) + (1 if l1n in tda_l1_opp else 0) \
        + (1 if l1n in stom_opp else 0)
    risk = (1 if l1n in open_risk else 0) + (1 if l1n in tda_l1_risk else 0) \
        + (1 if l1n in stom_risk else 0)
    det = []
    for src, s, w in (("开源", l1n in open_opp, l1n in open_risk),
                      ("TDA", l1n in tda_l1_opp, l1n in tda_l1_risk),
                      ("STMOM", l1n in stom_opp, l1n in stom_risk)):
        if s and w:
            det.append(f"{src}:对冲")
        elif s:
            det.append(f"{src}:机会")
        elif w:
            det.append(f"{src}:风险")
        else:
            det.append(f"{src}:-")
    rows.append({
        "一级行业": l1n,
        "机会分": opp,
        "风险分": risk,
        "净分": opp - risk,
        "横截面动量排名": int(mom_rank.get(l1n, 99)),
        "20日动量%": round(float(m20.get(l1n, np.nan)) * 100, 2),
        "60日动量%": round(float(m60.get(l1n, np.nan)) * 100, 2),
        "方法明细": " / ".join(det),
    })
comb = pd.DataFrame(rows).sort_values(["净分", "机会分", "横截面动量排名"],
                                      ascending=[False, False, True])
comb.to_csv(os.path.join(REPORT, "l1_combined.csv"), index=False, encoding="utf-8-sig")
print("\n==== 一级汇总(前15) ====")
print(comb.head(15).to_string(index=False))

# ---------------- 7. 三级下钻：机会一级下的 TDA 三级机会/观察 ----------------
drill = pd.DataFrame(tda_l3_opp_list + tda_l3_risk_list)
if len(drill):
    drill["动量排名"] = drill["l1"].map(mom_rank)
    drill = drill.sort_values(["动量排名", "label"])
    drill.to_csv(os.path.join(REPORT, "l3_drilldown.csv"), index=False, encoding="utf-8-sig")
    print("\n==== 三级下钻明细(前20) ====")
    print(drill.head(20).to_string(index=False))

# ---------------- 8. 合并 JSON ----------------
json.dump({
    "asof": str(last.date()),
    "l1_rows": comb.to_dict("records"),
    "l3_drill": drill.to_dict("records") if len(drill) else [],
    "tda_level": tda.get("tda_latest", {}),
    "tda_contrib": tda.get("contrib", []),
}, open(os.path.join(REPORT, "combined.json"), "w"), ensure_ascii=False, default=str)
print("\n[OK] 输出已写入 report_run/")