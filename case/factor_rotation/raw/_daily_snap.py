# -*- coding: utf-8 -*-
"""日线数据统一读取入口：区间表 daily_260d.csv + 最新单日快照 daily_YYYYMMDD.csv。

背景：raw/fetch_all.py 只维护区间表 daily_260d.csv（260 个交易日）与 daily_basic_65d.csv；
最新一个交易日的行情另存为 raw/daily_YYYYMMDD.csv 单日快照。周报链路原先在三个脚本里
硬编码 `daily_20261029.csv`，数据一旦更新就会读到过期文件。本模块把「定位最新快照 +
合并去重」收敛成一个入口，链路脚本改为调用它即可自动跟随最新交易日。

陷阱：daily_260d.csv 也以数字开头，绝不能用 `daily_[0-9]*.csv` 通配匹配快照，
必须用 8 位日期的全串匹配（re.fullmatch）。见返回的 as_of 是否等于 expected。
"""
import glob
import os
import re

import pandas as pd

_SNAP_RE = re.compile(r"daily_(\d{8})\.csv")


def list_snapshots(raw_dir):
    """返回 raw_dir 下全部单日快照，按日期升序 [(YYYYMMDD, path), ...]。"""
    out = []
    for p in glob.glob(os.path.join(raw_dir, "daily_*.csv")):
        m = _SNAP_RE.fullmatch(os.path.basename(p))
        if m:
            out.append((m.group(1), p))
    return sorted(out)


def latest_snapshot(raw_dir):
    """返回最新单日快照 (YYYYMMDD, path)；没有则 ("", None)。"""
    snaps = list_snapshots(raw_dir)
    return snaps[-1] if snaps else ("", None)


def read_daily(raw_dir, usecols=None, dtypes=None, verbose=True):
    """合并「区间表 + 最新单日快照」返回 (df, as_of)。

    as_of = 合并后最大交易日（YYYYMMDD 字符串）；无数据时 as_of = ""。
    合并规则：区间表在前、快照在后，按 (ts_code, trade_date) 去重 keep="last"，
    保证同一交易日以快照为准。
    """
    snaps = list_snapshots(raw_dir)
    frames, srcs = [], []
    span = os.path.join(raw_dir, "daily_260d.csv")
    if os.path.exists(span):
        frames.append(pd.read_csv(span, usecols=usecols, dtype=dtypes))
        srcs.append("daily_260d.csv")
    if snaps:
        frames.append(pd.read_csv(snaps[-1][1], usecols=usecols, dtype=dtypes))
        srcs.append(os.path.basename(snaps[-1][1]))
    if not frames:
        raise FileNotFoundError(f"{raw_dir} 下既没有 daily_260d.csv 也没有单日快照 daily_YYYYMMDD.csv")
    df = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
    if {"ts_code", "trade_date"} <= set(df.columns):
        df = df.drop_duplicates(["ts_code", "trade_date"], keep="last")
        as_of = str(pd.to_datetime(df["trade_date"]).max().strftime("%Y%m%d"))
    else:
        as_of = ""
    if verbose:
        print(f"[daily] {'+'.join(srcs)} -> rows={len(df)} as_of={as_of or 'n/a'}")
    return df, as_of


def snap_span(raw_dir):
    """仅返回 (区间表路径, 最新快照路径, as_of)，不读全表——供只需日期的场景使用。"""
    snaps = list_snapshots(raw_dir)
    span = os.path.join(raw_dir, "daily_260d.csv")
    as_of = snaps[-1][0] if snaps else ("" if not os.path.exists(span) else "")
    return span, (snaps[-1][1] if snaps else None), as_of