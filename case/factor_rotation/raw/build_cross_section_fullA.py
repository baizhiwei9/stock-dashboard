# -*- coding: utf-8 -*-
"""重建 raw/latest_cross_section_fullA.csv（全 A 池因子截面）——补齐此前的复现缺口。

背景：raw/latest_cross_section_fullA.csv 原先没有任何生成脚本（全工作区只有读取方），
使「因子合成 × 缠论买卖点」这条线无法随数据更新自动重算。本脚本补上该环节。

口径与 raw/factor_engine.py 的 build_cross_section 完全一致（同一批财务/预期/机构/
时间序列因子定义与算法），**唯一差异**：样本空间 universe6 = 全 A（当日有行情的全部
股票），而不是 factor_engine 的 HS300∪ZZ500∪ZZ1000∪ZZA500 四指数池。

用法：
    python raw/build_cross_section_fullA.py              # 截面日 = 最新周截面（与 factor_engine 同口径）
    python raw/build_cross_section_fullA.py 20261028     # 指定截面日
    python raw/build_cross_section_fullA.py --probe      # 只对比，不落盘
"""
import os
import sys

import numpy as np
import pandas as pd

BASE = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ed3f1d6b1ffeo164XKTVu7vvVA"
RAW = f"{BASE}/raw"
FE = f"{RAW}/factor_engine.py"

args = [a for a in sys.argv[1:]]
PROBE = "--probe" in args
args = [a for a in args if not a.startswith("--")]
WE = args[0] if args else None

# 复用 factor_engine.py 的全部数据装载与因子函数（只截断到 __main__ 之前，不执行其主流程）
src = open(FE, encoding="utf-8").read()
ns = {"__file__": FE, "__name__": "factor_engine"}
exec(src[:src.index('if __name__ == "__main__":')], ns)
daily = ns["daily"]

# 最新周截面（与 factor_engine 的 week_ends[-1] 完全一致）
dt = pd.to_datetime(daily["trade_date"])
week = dt.dt.to_period("W-FRI").rename("week")
weekly_last = daily.assign(week=week).groupby("week")["trade_date"].max().reset_index()
week_ends = sorted(weekly_last["trade_date"].unique())
we = str(WE) if WE else str(week_ends[-1])
print(f"[we] 截面日 {we}（week_ends[-1]={week_ends[-1]}，共 {len(week_ends)} 个周截面）")

# 全 A 样本空间：截面日当天有行情的全部股票（6 位代码，与 factor_engine 的 normalize 同口径）
# 说明：旧版 latest_cross_section_fullA.csv 的 5571 行恰为 20261028 当天有行情的 5571 只，
#       与该口径完全一致（不含 60 日观测数等额外过滤）。
norm = ns["normalize"]
day_codes = set(daily.loc[daily["trade_date"] == we, "ts_code"])
codes = {norm(c) for c in day_codes}
_orig4 = set(ns["universe6"])
ns["universe6"] = codes
print(f"[universe] 全 A 池 {len(codes)} 只（= 截面日 {we} 有行情的全部股票；原四指数池 {len(_orig4)} 只）")

xs = ns["build_cross_section"](we, week_ends)
xs = xs.drop(columns=[c for c in xs.columns if c == "index"], errors="ignore")
print(f"[xsec] 行 {len(xs)}  列 {len(xs.columns)}")
print(f"[xsec] 列 {list(xs.columns)}")

if PROBE:
    old = pd.read_csv(f"{RAW}/latest_cross_section_fullA.csv", dtype={"ts_code": str})
    print(f"\n[old] 行 {len(old)} 列 {len(old.columns)}")
    m = old[["ts_code", "close"]].merge(xs[["ts_code", "close"]], on="ts_code", how="outer",
                                        suffixes=("_old", "_new"))
    both = m.dropna()
    print(f"[对比] 共同代码 {len(both)}；close 完全一致 {(abs(both['close_old'] - both['close_new']) < 1e-6).sum()}")
    print(f"[对比] 仅旧有新无 {int(m['close_new'].isna().sum())}；仅新有旧无 {int(m['close_old'].isna().sum())}")
    ncols = [c for c in old.columns if c in xs.columns and c not in ("close", "ts_code")]
    diffs = []
    for c in ncols:
        mm = old[["ts_code", c]].merge(xs[["ts_code", c]], on="ts_code", how="inner",
                                       suffixes=("_old", "_new")).dropna()
        if not len(mm):
            continue
        a = pd.to_numeric(mm[c + "_old"], errors="coerce")
        b = pd.to_numeric(mm[c + "_new"], errors="coerce")
        ok = (a - b).abs() < 1e-6
        if ok.mean() < 0.999:
            diffs.append((c, round(float(ok.mean()), 4)))
    print(f"[对比] 列一致率 <99.9% 的字段：{diffs if diffs else '无'}")
    raise SystemExit(0)

out = f"{RAW}/latest_cross_section_fullA.csv"
xs.to_csv(out, index=False)
print(f"[saved] {out}  rows={len(xs)}")