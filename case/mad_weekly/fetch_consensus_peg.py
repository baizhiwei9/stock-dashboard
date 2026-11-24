# -*- coding: utf-8 -*-
"""用 TuShare report_rc（机构盈利预测）聚合一致预期，计算 PEG 并筛选 PEG≤1.5。

口径：
- 一致预期净利润：同一年度（quarter=YYYYQ4）各机构取"最近一份报告"，再取机构间中位数；
- 2027 增速 = 一致预期 2027 归母净利 / 2026 归母净利 − 1；
- 三年复合 = (一致预期 2029 / 2026)^(1/3) − 1（缺 2029 时用 2028 记两年复合并标注）；
- PEG = iFind PE(TTM) ÷ 增速(%)。
"""
import os, sys, json
import numpy as np
import pandas as pd

WORK = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5e6ceeb364ffeNZ3DY2SCHDLO20"
sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5e9bb55354ffe9Rg1AoYf1k3lwu")
from ts_http import fetch_many

pool = pd.read_csv(WORK + "/narrow_pool.csv", dtype={"ts_code": str})
V = pd.read_csv(WORK + "/valuation_growth.csv", dtype={"ts_code": str})
CODES = list(pool.ts_code)

RAW = WORK + "/consensus_report_rc.csv"
if os.path.exists(RAW):
    big = pd.read_csv(RAW, dtype={"ts_code": str})
    print("复用已有原始响应：%d 行" % len(big), flush=True)
else:
    res = fetch_many("report_rc", [{"ts_code": c, "start_date": "20260101", "end_date": "20261102"} for c in CODES],
                     workers=4, tag="report_rc")
    frames, fail = [], []
    for c, d in zip(CODES, res):
        if isinstance(d, Exception) or d is None or len(d) == 0:
            fail.append(c); continue
        frames.append(d)
    big = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    big.to_csv(RAW, index=False, encoding="utf-8-sig")
    json.dump({"fail": fail, "codes": len(CODES)}, open(WORK + "/consensus_fetch_meta.json", "w", encoding="utf-8"), ensure_ascii=False)
    print("取数：成功 %d 只，失败/无数据 %d 只 %s" % (len(frames), len(fail), fail[:8]), flush=True)

big = big.copy()
big["year"] = big["quarter"].astype(str).str.extract(r"^(\d{4})Q4$")[0]
big = big[big["year"].notna() & big["np"].notna() & (big["np"] > 0)]
big["report_date"] = big["report_date"].astype(str)

rows = []
for c, g in big.groupby("ts_code"):
    latest = g.sort_values("report_date").groupby(["year", "org_name"]).tail(1)   # 每机构每年取最近一份
    cons = latest.groupby("year")["np"].median()
    d = {"ts_code": c, "一致预期报告数": int(g.shape[0]), "机构数": int(g.org_name.nunique())}
    for y in ("2026", "2027", "2028", "2029"):
        d["NP" + y] = cons.get(y, np.nan)
    d["增速2027"] = (d["NP2027"] / d["NP2026"] - 1) * 100 if d["NP2026"] and d["NP2026"] > 0 and pd.notna(d["NP2027"]) else np.nan
    if pd.notna(d["NP2029"]) and d["NP2026"] > 0:
        d["三年复合"] = ((d["NP2029"] / d["NP2026"]) ** (1 / 3) - 1) * 100; d["复合口径"] = "2026→2029 三年"
    elif pd.notna(d["NP2028"]) and d["NP2026"] > 0:
        d["三年复合"] = ((d["NP2028"] / d["NP2026"]) ** (1 / 2) - 1) * 100; d["复合口径"] = "2026→2028 两年"
    else:
        d["三年复合"] = np.nan; d["复合口径"] = ""
    rows.append(d)

C = pd.DataFrame(rows)
m = pool.merge(C, on="ts_code", how="left").merge(
    V[["ts_code", "pe_ttm", "fore_np_yoy", "fore_pe_fy2"]], on="ts_code", how="left")
m["PEG_2027_ts"] = m["pe_ttm"] / m["增速2027"]
m["PEG_复合_ts"] = m["pe_ttm"] / m["三年复合"]
m["PE口径"] = "iFind PE(TTM) 2026-10-30"

def verdict(r):
    if pd.isna(r["pe_ttm"]): return "PE缺失"
    if r["pe_ttm"] <= 0: return "亏损(PE<0)"
    if pd.isna(r["增速2027"]): return "无一致预期"
    if r["增速2027"] <= 0: return "增速为负"
    return "通过(≤1.5)" if r["PEG_2027_ts"] <= 1.5 else "未通过(>1.5)"

m["PEG结论"] = m.apply(verdict, axis=1)
m.to_csv(WORK + "/consensus_peg.csv", index=False, encoding="utf-8-sig")

bp = m[m["命中"] != "无"]
print("\n有买点 %d 只（TuShare 一致预期覆盖 %d 只）" % (len(bp), int(bp["增速2027"].notna().sum())))
print("结论分布:", bp["PEG结论"].value_counts().to_dict())
ok = bp[bp["PEG结论"] == "通过(≤1.5)"].sort_values("PEG_2027_ts")
print("\n=== PEG≤1.5（按 PEG 升序）%d 只" % len(ok))
for i, (_, r) in enumerate(ok.iterrows(), 1):
    print("  %2d. %-8s %-2s PE %7.2f | 2027增速 %+7.1f%% | PEG %6.3f | 三年复合 %s | 买点 %s | 报告 %s 份/%s 家" % (
        i, r["名称"], r["命中"], r["pe_ttm"], r["增速2027"], r["PEG_2027_ts"],
        ("%+.1f%%" % r["三年复合"]) if pd.notna(r["三年复合"]) else "—", r["命中"], int(r["一致预期报告数"]), int(r["机构数"])))