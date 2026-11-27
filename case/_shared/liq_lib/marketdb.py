#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""marketdb —— **兼容性垫片**（不是第二数据源）

原 ``bin/marketdb.py`` 是 SQLite 长表 ``data/market.db`` 的读写门面，
``l5_micro.py`` / ``liq_credit_weekly.py`` 都通过它取数。

迁移后数据统一走 DataHub，但这两个模块里的调用面很窄（实测只有
``M.get_series(sid, days=, ascending=)`` 一个函数）。与其改 20 处调用点，
不如在这里放一个**同名同签名的垫片**，把请求转给 ``liqdata``（DataHub + 归档）：

    get_series(sid, days=520, ascending=True) → [(date, value), ...]

⚠️ 这里**不建表、不写库、不抓外网**。``connect()`` 之类保留成会抛错的占位，
   防止哪天有人以为还能当 SQLite 用（比静默返回空更像话）。
⚠️ 序列名单由 ``liqdata.EDB_MAP`` 决定：不在名单里的 series_id 直接返回空，
   ``l5_micro`` 会把该指标渲染成「本周期暂缺」，这是预期行为（不编造）。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

_CACHE = {}


def _dump_path():
    """fetch 阶段落的整份序列快照（避免 render 再抓一遍外网）。

    为什么需要：liqdata.load_all() 一次要 4~8 分钟（含全市场 ETF 份额），
    而 fetch / render / dispatch 是**三个独立进程**，进程内缓存管不到跨阶段。
    所以 fetch 把结果写成 data/liq_series.json，后续阶段直接读这份。
    """
    d = (os.environ.get("CASE_DATA_DIR") or "").strip()
    if not d:
        return ""
    return os.path.join(d, "liq_series.json")


def _all(days=520):
    """整批取一次，按 days 截窗口。优先读 fetch 落的快照。"""
    key = "all"
    if key not in _CACHE:
        got = None
        p = _dump_path()
        if p and os.path.isfile(p):
            try:
                raw = json.load(open(p, encoding="utf-8"))
                got = {k: [(d, v) for d, v in rows]
                       for k, rows in (raw.get("series") or {}).items()}
                got["_missing"] = raw.get("missing") or []
                got["_archive"] = raw.get("archive") or {}
            except (OSError, ValueError):
                got = None
        if got is None:
            import liqdata
            got = liqdata.load_all()
        _CACHE[key] = got
    data = _CACHE[key]
    if not days:
        return data
    cut = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    out = {}
    for k, v in data.items():
        if k.startswith("_"):
            continue
        out[k] = [(d, x) for d, x in v if d >= cut]
    return out


def get_series(series_id, days=None, start=None, end=None, ascending=True, db=None):
    """→ [(date, value)]，默认升序。签名与 bin/marketdb.py 完全一致。"""
    rows = list((_all(days if days is not None else 5200) or {}).get(series_id) or [])
    if start:
        s = str(start)[:10]
        rows = [(d, v) for d, v in rows if d >= s]
    if end:
        e = str(end)[:10]
        rows = [(d, v) for d, v in rows if d <= e]
    rows.sort(key=lambda x: x[0], reverse=not ascending)
    return rows


def latest(series_id, db=None):
    r = get_series(series_id, days=3650, ascending=False)
    return r[0] if r else (None, None)


def stale_days(series_id, db=None):
    d, _v = latest(series_id)
    if not d:
        return 9999
    try:
        return (dt.date.today() - dt.date.fromisoformat(str(d)[:10])).days
    except ValueError:
        return 9999


def connect(db=None):
    raise RuntimeError(
        "marketdb 在 case/ 里是 DataHub 垫片，不再提供 SQLite 连接。"
        "取数请用 liqdata.load_all() / data_hub.edb；写库请走 DataHub 采集器。")


def init_db(*a, **k):
    return connect()


def save_series(*a, **k):
    raise RuntimeError("写入请走 DataHub 采集器，本垫片只读。")


def save_snapshot(*a, **k):
    raise RuntimeError("写入请走 DataHub，本垫片只读。")


def load_snapshot(*a, **k):
    return None


def log_run(*a, **k):
    return None


# ── 假连接（只服务 liq_credit_weekly.py 那一句 SQL） ──────────────────────
# 原脚本全篇只用一种查询：
#     select date,value from obs where series_id=? order by date
# 与其把 40 处 `c.execute(...)` 改成函数调用，不如给一个最小可用的连接对象。
# 它不是数据库：只认这一条 SELECT，其它 SQL 一律抛错（宁可炸也不静默给空）。
import re as _re

_SELECT_RE = _re.compile(
    r"^\s*select\s+date\s*,\s*value\s+from\s+obs\s+where\s+series_id\s*=\s*\?\s*order\s+by\s+date\s*$",
    _re.I)


class _ShimCursor(list):
    """list of (date, value) 元组，兼作游标（脚本只做列表操作）。"""

    def fetchall(self):
        return list(self)

    def fetchone(self):
        return self[0] if self else None


class _ShimConn:
    """最小连接：execute → _ShimCursor；commit/close 是空操作。"""

    def execute(self, sql, params=()):
        if not _SELECT_RE.match(str(sql)):
            raise RuntimeError(
                "marketdb 垫片只支持 `select date,value from obs where series_id=?`，"
                "收到的是：%s" % str(sql).strip()[:120])
        sid = params[0] if params else ""
        return _ShimCursor(get_series(sid, days=None, ascending=True))

    def executescript(self, *a, **k):
        raise RuntimeError("垫片不建表；表结构由 DataHub 管。")

    def commit(self):
        return None

    def close(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


def shim_connect(db=None):
    """给 liq_credit_weekly.py 的 ``con()`` 用：返回只读垫片连接。"""
    return _ShimConn()
