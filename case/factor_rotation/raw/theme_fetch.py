# -*- coding: utf-8 -*-
"""概念热度监控：数据抓取
1) ths_daily  7 个板块指数日线（机器人链 + 特斯拉线）
2) index_daily 中证全指 000985.CSI 作为相对强弱基准
3) ths_hot   近 60 个交易日同花顺概念板块热榜（含热股榜单 concept 标签）
输出：raw/theme_board_daily.csv、raw/theme_bench_daily.csv、raw/theme_hot_daily.csv
"""
import sys, os, time
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ts_session import api  # noqa: E402

RAW = os.path.dirname(os.path.abspath(__file__))
END = "20261027"

META = pd.read_csv(os.path.join(RAW, "theme_boards_meta.csv"), dtype={"board": str})
BOARDS = list(dict(zip(META["key"], META["board"])).items())

# ---- 1. 板块指数日线 ----
parts = []
for key, code in BOARDS:
    df = api("ths_daily", ts_code=code, start_date="20240101", end_date=END)
    df["board_key"] = key
    parts.append(df)
    print(f"[ths_daily] {key} {code}: {df.shape} {df.trade_date.min()}~{df.trade_date.max()}", flush=True)
    time.sleep(1.0)
pd.concat(parts, ignore_index=True).to_csv(os.path.join(RAW, "theme_board_daily.csv"), index=False)

# ---- 2. 基准指数 ----
bench = api("index_daily", ts_code="000985.CSI", start_date="20240101", end_date=END)
bench.to_csv(os.path.join(RAW, "theme_bench_daily.csv"), index=False)
print(f"[index_daily] 000985.CSI: {bench.shape}", flush=True)
time.sleep(1.0)

# ---- 3. 近 60 个交易日概念板块热榜 ----
cal = pd.read_csv(os.path.join(RAW, "trade_cal.csv"), dtype=str)
days = sorted([d.replace("-", "") for d in cal["trade_date"] if d.replace("-", "") <= END])[-60:]
rows = []
for i, d in enumerate(days):
    try:
        df = api("ths_hot", trade_date=d)
    except Exception as e:
        print(f"[ths_hot] {d} 失败: {str(e)[:60]}", flush=True)
        continue
    if not len(df) or "data_type" not in df.columns:
        print(f"[ths_hot] {d} 空", flush=True)
        continue
    df = df[df["data_type"].isin(["概念板块", "行业板块"])].copy()
    if len(df):
        rows.append(df)
    print(f"[ths_hot] {d}: {len(df)} 条 ({i+1}/{len(days)})", flush=True)
    time.sleep(0.9)
hot = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
hot.to_csv(os.path.join(RAW, "theme_hot_daily.csv"), index=False)
print(f"[saved] theme_hot_daily.csv rows={len(hot)} 覆盖交易日={hot['trade_date'].nunique() if len(hot) else 0}")