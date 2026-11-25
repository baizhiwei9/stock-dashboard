# -*- coding: utf-8 -*-
"""首次/重建全量取数（一次性脚本）

全部数据经 DataHub/EDB 数据集接口取得（见 ``dh.py``），本脚本只负责把区间数据
落成工程使用的基础 CSV；日常增量由 ``run_daily.py`` 负责。
"""
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dh  # noqa: E402

import pandas as pd  # noqa: E402

DATA = os.path.join(HERE, "data")
os.makedirs(DATA, exist_ok=True)
START = "20260301"
END = "20261022"      # 基准日（两融数据最新日），窗口含 120 交易日
ETFS = ["512480.SH", "588200.SH", "515880.SH", "159819.SZ", "512000.SH", "512800.SH", "512170.SH",
        "159992.SZ", "516270.SH", "159796.SZ", "159928.SZ", "515170.SH", "515080.SH", "563020.SH",
        "515220.SH", "512400.SH", "510300.SH", "588000.SH", "159915.SZ"]
MD_KEEP = ["ts_code", "trade_date", "rzye", "rqye", "rzmre", "rzche", "rzrqye", "rqyl", "rqchl"]
DB_KEEP = ["ts_code", "trade_date", "close", "circ_mv", "total_mv", "float_share", "turnover_rate"]


def main():
    # 1) 交易日历
    days = dh.trade_calendar(START, END)
    json.dump([str(d) for d in days], open(os.path.join(DATA, "trade_days.json"), "w"))
    print("trade days:", len(days), days[0], "→", days[-1])

    # 2) 两融汇总（保留分交易所原始行，完整性校验留给 run_daily）
    ms = dh.margin_summary(START, END)
    ms.to_csv(os.path.join(DATA, "margin_summary.csv"), index=False)
    print("margin_summary rows", len(ms), "max date", ms.trade_date.max())

    # 3) 指数成交额
    dh.index_daily(["000001.SH", "399106.SZ"], START, END).to_csv(
        os.path.join(DATA, "index_amount.csv"), index=False)
    print("index_amount ok")

    # 4) 逐日明细（两融明细 / 每日指标）
    def bulk(fetch, keep, name):
        out = []
        with ThreadPoolExecutor(max_workers=4) as ex:
            futs = {ex.submit(fetch, str(d)): d for d in days}
            for k, fu in enumerate(as_completed(futs)):
                try:
                    df = fu.result()
                except Exception as e:
                    print("  ERR", futs[fu], str(e)[:100])
                    continue
                if df is not None and not df.empty:
                    out.append(df[[c for c in keep if c in df.columns]])
                if (k + 1) % 40 == 0:
                    print(f"  {name} {k + 1}/{len(days)}")
        if not out:
            print(f"{name}：无数据，跳过落盘")
            return
        df = pd.concat(out, ignore_index=True)
        df.to_csv(os.path.join(DATA, name), index=False)
        print(f"{name} done rows={len(df)} days={df.trade_date.nunique()}")

    bulk(dh.margin_detail_day, MD_KEEP, "margin_detail.csv")
    bulk(dh.daily_basic_day, DB_KEEP, "daily_basic.csv")

    # 5) ETF 份额
    try:
        etf = dh.etf_share(ETFS, START, END)
        etf.to_csv(os.path.join(DATA, "etf_share.csv"), index=False)
        print("etf_share rows", len(etf), "codes", etf.ts_code.nunique())
    except Exception as e:
        print("  ETF share ERR", str(e)[:120])

    # 6) 股票基础信息
    dh.stock_basic().to_csv(os.path.join(DATA, "stock_basic.csv"), index=False)

    # 7) 申万一级行业映射
    im = dh.industry_map()
    im.to_csv(os.path.join(DATA, "industry_map.csv"), index=False)
    print("industry_map rows", len(im), "industries", im.l1_name.nunique(),
          "missing", im.attrs.get("missing_l1"))
    print("ALL DONE")


if __name__ == "__main__":
    main()