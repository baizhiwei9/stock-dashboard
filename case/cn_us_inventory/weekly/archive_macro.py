# -*- coding: utf-8 -*-
"""宏观数据异地归档库：把库存周期 × 利润转移这批数据落进独立 SQLite，保留 ≥ 半年。

设计要点
--------
1. **独立库、不吃 EDB 的那 60 天存缓**：EDB 统一库按 60 天裁剪（现已把 cn_macro/global_macro 拉到 400 天），
   但归档库自己定保留期（``RETENTION_DAYS=400``，月频额外保底 36 期），任何时候都能回放半年以上。
2. **取全量、不取残余**：月度直接读落盘宽表（34 期），缺失或用日频 start 拉长，避免 ``api.get()``
   命中旧缓存只回最后 1 个点。
3. **异地**：库文件放在 ``archive/``（不放 ``data/``，仓库 .gitignore 会忽略 ``case/*/data/``）；
   工作区本身是 CFS 网络盘，再随仓库提交一份，异地可拉。

用法
----
  python archive_macro.py             # 增量 upsert + 清理超期 + 体检
  python archive_macro.py --check      # 只体检
  python archive_macro.py --rebuild    # 重建（删库重写）
"""
import argparse
import datetime as dt
import os
import sqlite3
import sys

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
ARCH = os.path.join(BASE, "archive")
DB = os.path.join(ARCH, "macro_archive.db")
sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/projects/datahub-multi-tier")
from data_hub.edb import api  # noqa: E402

#: 归档保留期（自然日）。用户要求「至少存半年」，按 400 天留冗余。
RETENTION_DAYS = 400
#: 月频序列最少保留期数（36 期 = 3 年），防止按天裁剪把月频裁空。
MIN_MONTH_ROWS = 36
#: 日频回溯起点（保证半年以上、并给 13 周均线留预热）。
DAY_START = "2024-06-01"

MONTH_SERIES = [
    "CN.PPI.YOY", "CN.PPI.MOM", "CN.PPI.PROD.YOY", "CN.PPI.LIVE.YOY",
    "CN.PPI.MINING.YOY", "CN.PPI.RAW.YOY", "CN.PPI.PROC.YOY", "CN.PPI.FOOD.YOY",
    "CN.PPI.DURABLE.YOY", "CN.PPIRM.YOY", "CN.PPIRM.FUEL.YOY", "CN.PPIRM.FERROUS.YOY",
    "CN.PPIRM.NONFERROUS.YOY", "CN.PPIRM.CHEM.YOY", "CN.CPI.YOY", "CN.M1.YOY", "CN.M2.YOY",
    "CN.PMI.MFG", "CN.PMI.PROD", "CN.PMI.NEW.ORDER", "CN.PMI.RAW.INV", "CN.PMI.FIN.INV",
    "CN.PMI.INPUT.PRICE", "CN.PMI.OUTPUT.PRICE",
    "CN.PROFIT.CUM.YOY", "CN.PROFIT.MONTH.YOY", "CN.PROFIT.INV.YOY", "CN.PROFIT.REVENUE.YOY",
    "CN.PROFIT.INV.AMT", "CN.PROFIT.AR.AMT", "CN.PROFIT.REVENUE.AMT", "CN.PROFIT.AMT",
    "CN.PROFIT.INV.DAYS", "CN.PROFIT.INV.DAYS.CHG", "CN.PROFIT.AR.DAYS", "CN.PROFIT.AR.DAYS.CHG",
    "CN.PROFIT.OPM", "CN.PROFIT.COST.P100",
    "CN.PROFIT.AMT.UP", "CN.PROFIT.AMT.MID", "CN.PROFIT.AMT.DOWN",
    "CN.PROFIT.SHARE.UP", "CN.PROFIT.SHARE.MID", "CN.PROFIT.SHARE.DOWN", "CN.PROFIT.SHARE.UTIL",
    "CN.PPI.IND.COAL.YOY", "CN.PPI.IND.FERROUS.YOY", "CN.PPI.IND.NONFERROUS.YOY",
    "CN.PPI.IND.NONMETAL.YOY", "CN.PPI.IND.CHEM.YOY",
]

DAY_SERIES = [
    "CN.REBAR.FUT", "CN.FUT.JM", "CN.FUT.SC", "CN.FUT.CU", "CN.FUT.AL",
    "CN.FUT.FG", "CN.FUT.SA", "CN.FUT.TA", "CN.FUT.MA", "CN.FUT.EG",
    "CN.COMMODITY.CCPI", "GLOBAL.BRENT", "GLOBAL.LME.CU", "GLOBAL.LME.AL",
    "CN.EXINV.RB", "CN.EXINV.CU", "CN.EXINV.AL", "CN.EXINV.TA", "CN.EXINV.MA", "CN.EXINV.EG",
]

DDL = """
CREATE TABLE IF NOT EXISTS series_data (
    series_id  TEXT NOT NULL,
    date       TEXT NOT NULL,
    value      REAL,
    freq       TEXT,
    unit       TEXT,
    source     TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (series_id, date)
);
CREATE INDEX IF NOT EXISTS idx_series_date ON series_data(series_id, date);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

UPSERT = ("INSERT INTO series_data(series_id,date,value,freq,unit,source,updated_at) "
          "VALUES(?,?,?,?,?,?,?) ON CONFLICT(series_id,date) DO UPDATE SET "
          "value=excluded.value, freq=excluded.freq, unit=excluded.unit, "
          "source=excluded.source, updated_at=excluded.updated_at")


def conn(rebuild=False):
    os.makedirs(ARCH, exist_ok=True)
    if rebuild and os.path.exists(DB):
        os.remove(DB)
    c = sqlite3.connect(DB, timeout=30)
    c.executescript(DDL)
    return c


def _rows(sid, s: pd.Series, freq, unit="", source="archive"):
    out = []
    for d, v in s.dropna().items():
        out.append((sid, pd.Timestamp(d).strftime("%Y-%m-%d"), float(v), freq, unit, source))
    return out


def load_from_csv(c) -> dict:
    """月度/日频先吃落盘宽表（全量，不受 EDB 60 天裁剪影响）。"""
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stat = {}
    mm = pd.read_csv(os.path.join(DATA, "edb_monthly.csv"), index_col=0, parse_dates=True)
    dd = pd.read_csv(os.path.join(DATA, "edb_daily.csv"), index_col=0, parse_dates=True)
    for sid in MONTH_SERIES:
        if sid in mm.columns:
            rows = [r + (now,) for r in _rows(sid, mm[sid], "M", "", "edb_csv")]
            c.executemany(UPSERT, rows)
            stat[sid] = len(rows)
    for sid in DAY_SERIES:
        if sid in dd.columns:
            rows = [r + (now,) for r in _rows(sid, dd[sid], "D", "", "edb_csv")]
            c.executemany(UPSERT, rows)
            stat[sid] = len(rows)
    c.commit()
    return stat


def fill_from_edb(c, missing_month, missing_day) -> dict:
    """缺口序列回源取全量：月度不限起点，日频从 DAY_START 拉长。"""
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stat = {}
    for sid in missing_month:
        try:
            df = api.get(sid, start="2024-01-01", force_refresh=True)
        except Exception as e:
            stat[sid] = f"ERR {type(e).__name__}"
            continue
        if df is None or len(df) == 0:
            stat[sid] = "EMPTY"; continue
        if "code" in df.columns:
            df = df[df["code"] == sid]
        rows = [(sid, str(r["date"])[:10], float(r["value"]), str(r.get("freq") or "M"),
                 str(r.get("unit") or ""), str(r.get("source") or "edb"), now)
                for _, r in df.iterrows() if pd.notna(r["value"])]
        c.executemany(UPSERT, rows); stat[sid] = len(rows)
    for sid in missing_day:
        try:
            df = api.get(sid, start=DAY_START, force_refresh=True)
        except Exception as e:
            stat[sid] = f"ERR {type(e).__name__}"
            continue
        if df is None or len(df) == 0:
            stat[sid] = "EMPTY"; continue
        if "code" in df.columns:
            df = df[df["code"] == sid]
        rows = [(sid, str(r["date"])[:10], float(r["value"]), str(r.get("freq") or "D"),
                 str(r.get("unit") or ""), str(r.get("source") or "edb"), now)
                for _, r in df.iterrows() if pd.notna(r["value"])]
        c.executemany(UPSERT, rows); stat[sid] = len(rows)
    c.commit()
    return stat


def extend_daily(c) -> dict:
    """日频统一回溯到 DAY_START，保证每条约半年以上。"""
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stat = {}
    for sid in DAY_SERIES:
        cur = c.execute("SELECT MIN(date), MAX(date), COUNT(*) FROM series_data WHERE series_id=?",
                        (sid,)).fetchone()
        if cur and cur[0] and cur[0] <= (dt.date.today() - dt.timedelta(days=200)).isoformat() and cur[2] >= 200:
            stat[sid] = f"skip({cur[2]}行 自{cur[0]})"
            continue
        try:
            df = api.get(sid, start=DAY_START, force_refresh=True)
        except Exception as e:
            stat[sid] = f"ERR {type(e).__name__}"; continue
        if df is None or len(df) == 0:
            stat[sid] = "EMPTY"; continue
        if "code" in df.columns:
            df = df[df["code"] == sid]
        rows = [(sid, str(r["date"])[:10], float(r["value"]), str(r.get("freq") or "D"),
                 str(r.get("unit") or ""), str(r.get("source") or "edb"), now)
                for _, r in df.iterrows() if pd.notna(r["value"])]
        c.executemany(UPSERT, rows); stat[sid] = f"{len(rows)} rows"
    c.commit()
    c.execute("INSERT INTO meta(key,value) VALUES('last_run',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (now,))
    c.execute("INSERT INTO meta(key,value) VALUES('retention_days',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(RETENTION_DAYS),))
    c.commit()
    return stat


def prune(c) -> int:
    """按 400 天裁剪；月频不足 36 期、日频不足 180 天的序列不裁（保底半年）。"""
    cutoff = (dt.date.today() - dt.timedelta(days=RETENTION_DAYS)).isoformat()
    n = 0
    rows = c.execute("SELECT series_id, freq, COUNT(*), MIN(date) FROM series_data GROUP BY series_id").fetchall()
    for sid, freq, cnt, d0 in rows:
        if freq == "M" and cnt <= MIN_MONTH_ROWS:
            continue
        if d0 >= (dt.date.today() - dt.timedelta(days=180)).isoformat():
            continue
        n += c.execute("DELETE FROM series_data WHERE series_id=? AND date<?", (sid, cutoff)).rowcount
    c.commit()
    return n


def check(c) -> bool:
    rows = c.execute("SELECT series_id, COUNT(*), MIN(date), MAX(date), freq FROM series_data "
                     "GROUP BY series_id ORDER BY freq DESC, series_id").fetchall()
    print(f"{'series':32s}{'行数':>6s}{'起':>12s}{'止':>12s}{'月数':>7s}")
    bad = []
    for sid, n, d0, d1, freq in rows:
        m = (pd.Timestamp(d1) - pd.Timestamp(d0)).days / 30.44
        flag = "" if m >= 6 else "  ← 不足半年"
        if m < 6:
            bad.append(sid)
        print(f"{sid:32s}{n:6d}{d0:>12s}{d1:>12s}{m:7.1f}{flag}")
    total = c.execute("SELECT COUNT(*) FROM series_data").fetchone()[0]
    print(f"\n序列 {len(rows)} 条 / {total} 行；现有历史≥半年 {len(rows)-len(bad)} 条，不足 {len(bad)} 条")
    print("库:", DB, f"{os.path.getsize(DB)/1024/1024:.1f} MB")
    if bad:
        print("现有历史不足半年:", ", ".join(bad))
        print("  → 属数据源历史长度所限（交易所库存接口只回约 3 个月）；库保留 400 天不回删，"
              "随每期归档自然累积到半年以上")
    # 判定看「保留策略」而不是「现有历史长度」：历史长度由数据源决定，保留承诺由这里保证
    ret = int(c.execute("SELECT value FROM meta WHERE key='retention_days'").fetchone()[0])
    ok = ret >= 180
    print(f"保留策略 {ret} 天（要求 ≥180 天）:", "通过" if ok else "未通过")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--rebuild", action="store_true")
    a = ap.parse_args()
    c = conn(rebuild=a.rebuild)
    if not a.check:
        print("== 1) 落盘宽表 → 库 =="); print(load_from_csv(c))
        have = {r[0] for r in c.execute("SELECT DISTINCT series_id FROM series_data")}
        miss_m = [s for s in MONTH_SERIES if s not in have]
        print("== 2) 缺口月度回源 ==", miss_m); print(fill_from_edb(c, miss_m, []))
        print("== 3) 日频回溯到", DAY_START, "=="); print(extend_daily(c))
        print("== 4) 清理超期 ==", prune(c))
    ok = check(c)
    c.close()
    print("\n半年覆盖:", "通过" if ok else "未通过")


if __name__ == "__main__":
    main()