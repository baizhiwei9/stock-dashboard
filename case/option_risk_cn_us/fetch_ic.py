# -*- coding: utf-8 -*-
"""抓取中证500股指期货(IC)月度合约历史，用于计算 IC 年化基差（对齐 IM 口径）
用法: python fetch_ic.py
"""
import os
import sys
import json
import pandas as pd
import ts_pin

# 原指向旧沙箱 workspace + ses_.../skills 目录（均已空/不存在）。
# ts_pin.py / ts_config.py / data/ 都在本 case 目录下。
BASE = os.path.dirname(os.path.abspath(__file__))
SES = BASE
PYLIBS = ""   # pylibs 已并入 py_deps / site-packages，见 case/_lib/paths.py
TSCFG = BASE   # ts_config.py 在本 case 目录
for p in (SES, TSCFG, PYLIBS):
    if p and p not in sys.path:
        sys.path.insert(0, p)

DATA = os.path.join(SES, "data")
TRADE_DATE = os.environ.get("TRADE_DATE", "20261021")
HIST_START = "20241001"

pro = ts_pin.init()


def main():
    fb = ts_pin.call(pro.fut_basic, exchange="CFFEX", fut_type="1")
    ic = fb[fb["symbol"].astype(str).str.match(r"^IC\d{4}$")].copy()
    ic = ic[ic["delist_date"].astype(str) >= "20250801"]
    ic.to_json(os.path.join(DATA, "fut_basic_ic_month.json"),
               orient="records", force_ascii=False)
    print("IC monthly contracts:", len(ic), list(ic["ts_code"])[:8])
    fr = []
    for code in ic["ts_code"].tolist():
        try:
            d = ts_pin.call(pro.fut_daily, ts_code=code, start_date=HIST_START,
                            end_date=TRADE_DATE)
        except Exception as e:
            print("fut_daily", code, "ERR", str(e)[:60]); continue
        if not len(d):
            continue
        d = d.sort_values("trade_date")
        d["code"] = code
        fr.append(d[["code", "trade_date", "close", "settle", "vol", "oi"]])
        print("ic", code, d.shape)
    if fr:
        pd.concat(fr, ignore_index=True).to_json(
            os.path.join(DATA, "fut_ic_month_hist.json"),
            orient="records", force_ascii=False)
        print("saved fut_ic_month_hist.json")


if __name__ == "__main__":
    main()