# -*- coding: utf-8 -*-
"""红利与估值因子：tushare daily_basic（全市场，含股息率/估值/市值）+ dividend 明细。"""
import os
import sys

WORK = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5e9bb55354ffe9Rg1AoYf1k3lwu")
import pandas as pd
from ts_http import fetch_df, fetch_many

CODES = ["600825.SH", "601900.SH", "601098.SH", "000719.SZ", "301052.SZ",
         "600757.SH", "601928.SH", "300654.SZ", "601019.SH"]
TD = "20261030"

db = fetch_df("daily_basic", {"trade_date": TD},
              fields="ts_code,close,turnover_rate,pe,pe_ttm,pb,ps_ttm,dv_ratio,dv_ttm,total_mv,circ_mv")
db.to_csv(os.path.join(WORK, "daily_basic_%s.csv" % TD), index=False, encoding="utf-8-sig")
print("daily_basic rows=%d" % len(db))
sub = db[db.ts_code.isin(CODES)].merge(
    pd.DataFrame({"ts_code": CODES}), on="ts_code", how="right")
print(sub.to_string(index=False))

res = fetch_many("dividend", [{"ts_code": c} for c in CODES], workers=3, verbose=False)
frames = []
for c, d in zip(CODES, res):
    if isinstance(d, Exception):
        print("dividend FAIL", c, d)
        continue
    d = d.copy()
    d["code"] = c
    frames.append(d)
div = pd.concat(frames, ignore_index=True)
div.to_csv(os.path.join(WORK, "dividend_raw.csv"), index=False, encoding="utf-8-sig")
print("dividend rows=%d cols=%s" % (len(div), list(div.columns)))
imp = div[div.div_proc.astype(str).str.contains("实施", na=False)]
summary = imp.groupby("code").agg(实施次数=("end_date", "size"), 最近报告期=("end_date", "max"),
                                 近三年现金分红合计=("cash_div_tax", lambda s: s.tail(3).sum()))
print(summary.to_string())