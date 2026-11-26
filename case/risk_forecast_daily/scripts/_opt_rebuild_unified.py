#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
期权指标历史全量重建（薄封装：只调 data_hub 统一接口，不自行拼数据源）
=====================================================================
链路：data_hub.collectors.option_metrics_history.get_option_metrics_history
      （Tushare 原始行情 + option_metrics 标准BS自算，全系统唯一口径）
      → 月度分批拉取落 CSV（防限流、可续跑）→ UPSERT 进 style_drift.daily_metrics

用法：
  python3 _opt_rebuild_unified.py pull 20250101 20261022   # 按月分批拉取→CSV
  python3 _opt_rebuild_unified.py load                      # 读CSV→灌库(UPSERT)
  python3 _opt_rebuild_unified.py verify 20261022           # 单日打印校验

纪律：本脚本不含任何 Tushare/AKShare/爬虫直连，只 import 统一接口。
"""
import os
import sys
import sqlite3
import pandas as pd

DATAHUB = "/Coze/Drive/蓁宝/所有对话/主对话/codeact/scripts"
sys.path.insert(0, DATAHUB)
from data_hub.collectors.option_metrics_history import get_option_metrics_history  # noqa:E402

DB = "/Coze/Drive/蓁宝/所有对话/主对话/市场情绪监控/data/options_cache/style_drift.db"
CSV = "/tmp/rfdtest/opt_rebuild_unified.csv"

COLS = ["trade_date", "underlying", "spot_price", "atm_iv", "put_25d_iv",
        "call_25d_iv", "iv_skew", "cboe_skew", "vol_pcr", "oi_pcr", "amt_pcr"]


def _month_chunks(start: str, end: str):
    """把区间切成 (月起, 月止) 列表，每月一批，降低单次时长与限流风险。"""
    s = pd.Timestamp(start)
    e = pd.Timestamp(end)
    chunks = []
    cur = s.replace(day=1)
    while cur <= e:
        nxt = (cur + pd.offsets.MonthBegin(1)) - pd.Timedelta(days=1)
        cs = max(cur, s)
        ce = min(nxt, e)
        chunks.append((cs.strftime("%Y%m%d"), ce.strftime("%Y%m%d")))
        cur = (cur + pd.offsets.MonthBegin(1))
    return chunks


def pull(start="20250101", end="20261022"):
    os.makedirs(os.path.dirname(CSV), exist_ok=True)
    # 续跑：已拉过的 (date,underlying) 不重复
    done = set()
    if os.path.exists(CSV):
        old = pd.read_csv(CSV, dtype={"trade_date": str, "underlying": str})
        done = set(zip(old["trade_date"], old["underlying"]))
        print(f"续跑：已有 {len(done)} 行")

    frames = []
    for cs, ce in _month_chunks(start, end):
        print(f"\n>>> 拉取 {cs} ~ {ce}", flush=True)
        try:
            df = get_option_metrics_history(cs, ce, verbose=True)
        except Exception as e:
            print(f"❌ 批次失败 {cs}~{ce}: {e}", flush=True)
            continue
        if df is None or df.empty:
            print("  空批次")
            continue
        df = df[df["data_source"].notna()] if "data_source" in df.columns else df
        new = df[[c for c in COLS if c in df.columns]].copy()
        new = new[[tuple(x) not in done for x in zip(new["trade_date"], new["underlying"])]]
        if len(new):
            frames.append(new)
            done.update(zip(new["trade_date"], new["underlying"]))
            new.to_csv(CSV, mode="a", header=not os.path.exists(CSV), index=False)
            print(f"  ✅ 新增 {len(new)} 行，累计 {len(done)}")
    print(f"\nSAVED {CSV} 总计 {len(done)} 行")


def load():
    if not os.path.exists(CSV):
        print("CSV 不存在，先 pull"); sys.exit(1)
    df = pd.read_csv(CSV, dtype={"trade_date": str, "underlying": str})
    df = df.drop_duplicates(subset=["trade_date", "underlying"], keep="last")
    print(f"待灌 {len(df)} 行，标的分布：")
    print(df.groupby("underlying").agg(n=("trade_date", "size"),
                                       s=("trade_date", "min"), e=("trade_date", "max")))
    conn = sqlite3.connect(DB)
    sql = """INSERT OR REPLACE INTO daily_metrics
        (trade_date,underlying,spot_price,atm_iv,put_25d_iv,call_25d_iv,
         iv_skew,cboe_skew,vol_pcr,oi_pcr,amt_pcr)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)"""
    rows = list(df[COLS].itertuples(index=False, name=None))
    conn.executemany(sql, rows)
    conn.commit()
    # 灌后体检
    print("\n=== 灌库后体检 ===")
    for r in conn.execute(
        "SELECT underlying,COUNT(*),MIN(trade_date),MAX(trade_date),"
        "ROUND(AVG(atm_iv),4),ROUND(MIN(atm_iv),4),ROUND(MAX(atm_iv),4) "
        "FROM daily_metrics GROUP BY underlying ORDER BY underlying"):
        print(r)
    conn.close()
    print("LOAD_DONE")


def verify(td="20261022"):
    df = get_option_metrics_history(td, td, verbose=False)
    pd.set_option("display.width", 280); pd.set_option("display.max_columns", 30)
    print(df[COLS].to_string(index=False))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "pull"
    if cmd == "pull":
        pull(sys.argv[2] if len(sys.argv) > 2 else "20250101",
             sys.argv[3] if len(sys.argv) > 3 else "20261022")
    elif cmd == "load":
        load()
    elif cmd == "verify":
        verify(sys.argv[2] if len(sys.argv) > 2 else "20261022")
