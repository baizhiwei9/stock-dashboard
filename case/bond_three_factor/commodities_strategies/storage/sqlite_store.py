# -*- coding: utf-8 -*-
"""SQLite 存储层：合约日线、主力连续、价差序列、统计量、信号与基本面。

设计要点：
- 所有表均以 (业务主键) 幂等 upsert，重复跑批不会产生重复行；
- 价差与统计量按 asof_date 快照保存，可回溯任意一天的分位与信号；
- 合约日线保留 source 字段，可核对某一行究竟来自 DataHub/AKShare/TuShare/Web。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS contract_daily (
    contract    TEXT NOT NULL,
    product     TEXT NOT NULL,
    trade_date  TEXT NOT NULL,
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL,
    settle      REAL,
    volume      REAL,
    oi          REAL,
    source      TEXT,
    fetched_at  TEXT,
    PRIMARY KEY (contract, trade_date)
);

CREATE TABLE IF NOT EXISTS continuous_daily (
    product         TEXT NOT NULL,
    trade_date      TEXT NOT NULL,
    close           REAL,
    adj_close       REAL,
    dominant        TEXT,
    oi              REAL,
    roll_flag       INTEGER DEFAULT 0,
    PRIMARY KEY (product, trade_date)
);

CREATE TABLE IF NOT EXISTS spread_def (
    spread_id   TEXT PRIMARY KEY,
    name        TEXT,
    type        TEXT,
    calc        TEXT,
    config_json TEXT,
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS spread_daily (
    spread_id    TEXT NOT NULL,
    trade_date   TEXT NOT NULL,
    leg_a        TEXT,
    leg_b        TEXT,
    leg_a_price  REAL,
    leg_b_price  REAL,
    value        REAL,
    calc         TEXT,
    roll_flag    INTEGER DEFAULT 0,
    roll_cost    REAL DEFAULT 0,
    PRIMARY KEY (spread_id, trade_date)
);

CREATE TABLE IF NOT EXISTS spread_stats (
    spread_id     TEXT NOT NULL,
    asof_date     TEXT NOT NULL,
    window        INTEGER,
    n_obs         INTEGER,
    mean          REAL,
    std           REAL,
    z             REAL,
    pct           REAL,
    adf_p         REAL,
    hurst         REAL,
    half_life     REAL,
    vol_pct       REAL,
    stationary    INTEGER,
    regime        TEXT,
    PRIMARY KEY (spread_id, asof_date)
);

CREATE TABLE IF NOT EXISTS hedge_signals (
    spread_id        TEXT NOT NULL,
    asof_date        TEXT NOT NULL,
    action           TEXT,
    direction        TEXT,
    score            REAL,
    z                REAL,
    pct              REAL,
    adf_p            REAL,
    hurst            REAL,
    vol_pct          REAL,
    fundamental_flag TEXT,
    reasons_json     TEXT,
    PRIMARY KEY (spread_id, asof_date)
);

CREATE TABLE IF NOT EXISTS fundamentals_daily (
    product          TEXT NOT NULL,
    trade_date       TEXT NOT NULL,
    basis_rate       REAL,
    basis_pct        REAL,
    warehouse        REAL,
    warehouse_chg_5d REAL,
    source           TEXT,
    PRIMARY KEY (product, trade_date)
);

CREATE TABLE IF NOT EXISTS cftc_cot_weekly (
    commodity    TEXT NOT NULL,
    week         TEXT NOT NULL,
    name_cn      TEXT,
    report_date  TEXT,
    oi           REAL,
    mm_long      REAL,
    mm_short     REAL,
    mm_net       REAL,
    d_mm_net     REAL,
    cot          REAL,
    dir          INTEGER,
    close        REAL,
    fetched_at   TEXT,
    PRIMARY KEY (commodity, week)
);

CREATE TABLE IF NOT EXISTS cftc_cot_episode (
    commodity    TEXT NOT NULL,
    asof_week    TEXT NOT NULL,
    start_week   TEXT,
    dir          INTEGER,
    held_weeks   INTEGER,
    cot          REAL,
    pnl_pct      REAL,
    action       TEXT,
    stage        TEXT,
    advice       TEXT,
    PRIMARY KEY (commodity, asof_week)
);

CREATE TABLE IF NOT EXISTS ingest_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT,
    stage      TEXT,
    target     TEXT,
    status     TEXT,
    rows       INTEGER,
    source     TEXT,
    message    TEXT
);

CREATE INDEX IF NOT EXISTS idx_contract_daily_product ON contract_daily(product, trade_date);
CREATE INDEX IF NOT EXISTS idx_spread_daily_date ON spread_daily(spread_id, trade_date);
CREATE INDEX IF NOT EXISTS idx_signals_date ON hedge_signals(asof_date);
CREATE INDEX IF NOT EXISTS idx_cftc_weekly_week ON cftc_cot_weekly(week);
"""

_CONTRACT_COLS = ["contract", "product", "trade_date", "open", "high", "low", "close",
                  "settle", "volume", "oi", "source", "fetched_at"]


class SQLiteStore:
    def __init__(self, db_path: Path | str):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    # ---------------- 基础 ----------------
    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            cols = [r[1] for r in conn.execute("PRAGMA table_info(spread_daily)")]
            if "roll_cost" not in cols:
                conn.execute("ALTER TABLE spread_daily ADD COLUMN roll_cost REAL DEFAULT 0")

    def recent_empty(self, contract: str, days: int = 7) -> bool:
        """近期已确认取不到数据的合约，避免反复发起慢请求。"""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM ingest_log WHERE stage='fetch_contract' AND target=?"
                " AND status='empty' AND ts>=datetime('now', ?)",
                (contract, f"-{int(days)} days"),
            ).fetchone()
        return bool(row[0])

    def log(self, stage: str, target: str, status: str, rows: int = 0,
            source: str = "", message: str = "") -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO ingest_log(ts, stage, target, status, rows, source, message)"
                " VALUES (?,?,?,?,?,?,?)",
                (datetime.now().isoformat(timespec="seconds"), stage, target, status,
                 int(rows), source, message[:500]),
            )

    # ---------------- 合约日线 ----------------
    def upsert_contract_daily(self, df: pd.DataFrame, product: str, contract: str,
                              source: str) -> int:
        if df is None or df.empty:
            return 0
        work = df.copy()
        work["contract"] = contract
        work["product"] = product
        work["trade_date"] = pd.to_datetime(work["trade_date"]).dt.strftime("%Y-%m-%d")
        work["source"] = source
        work["fetched_at"] = datetime.now().isoformat(timespec="seconds")
        for col in ("open", "high", "low", "close", "settle", "volume", "oi"):
            if col not in work.columns:
                work[col] = None
        rows = [tuple(None if pd.isna(r[c]) else r[c] for c in _CONTRACT_COLS)
                for _, r in work[_CONTRACT_COLS].iterrows()]
        with self.connect() as conn:
            conn.executemany(
                "INSERT INTO contract_daily(contract,product,trade_date,open,high,low,close,"
                "settle,volume,oi,source,fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(contract,trade_date) DO UPDATE SET "
                "open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,"
                "settle=excluded.settle,volume=excluded.volume,oi=excluded.oi,"
                "source=excluded.source,fetched_at=excluded.fetched_at",
                rows,
            )
        return len(rows)

    def contract_coverage(self, contract: str) -> Dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT MIN(trade_date) mn, MAX(trade_date) mx, COUNT(*) n"
                " FROM contract_daily WHERE contract=?", (contract,)
            ).fetchone()
        return {"min": row["mn"], "max": row["mx"], "n": row["n"] or 0}

    def load_contract_daily(self, contract: str, start: Optional[str] = None,
                            end: Optional[str] = None) -> pd.DataFrame:
        sql = "SELECT trade_date,open,high,low,close,settle,volume,oi,source FROM contract_daily WHERE contract=?"
        params: List[Any] = [contract]
        if start:
            sql += " AND trade_date>=?"
            params.append(pd.to_datetime(start).strftime("%Y-%m-%d"))
        if end:
            sql += " AND trade_date<=?"
            params.append(pd.to_datetime(end).strftime("%Y-%m-%d"))
        sql += " ORDER BY trade_date"
        with self.connect() as conn:
            df = pd.read_sql_query(sql, conn, params=params)
        if not df.empty:
            df["trade_date"] = pd.to_datetime(df["trade_date"])
        return df

    # ---------------- 主力连续 ----------------
    def upsert_continuous(self, product: str, df: pd.DataFrame) -> int:
        if df is None or df.empty:
            return 0
        work = df.copy()
        work["product"] = product
        work["trade_date"] = pd.to_datetime(work["trade_date"]).dt.strftime("%Y-%m-%d")
        rows = [
            (product, r["trade_date"], _f(r.get("close")), _f(r.get("adj_close")),
             str(r.get("dominant") or ""), _f(r.get("oi")), int(r.get("roll_flag") or 0))
            for _, r in work.iterrows()
        ]
        with self.connect() as conn:
            conn.executemany(
                "INSERT INTO continuous_daily(product,trade_date,close,adj_close,dominant,oi,roll_flag)"
                " VALUES(?,?,?,?,?,?,?) ON CONFLICT(product,trade_date) DO UPDATE SET "
                "close=excluded.close,adj_close=excluded.adj_close,dominant=excluded.dominant,"
                "oi=excluded.oi,roll_flag=excluded.roll_flag",
                rows,
            )
        return len(rows)

    def load_continuous(self, product: str, start: Optional[str] = None,
                        end: Optional[str] = None) -> pd.DataFrame:
        sql = "SELECT trade_date,close,adj_close,dominant,oi,roll_flag FROM continuous_daily WHERE product=?"
        params: List[Any] = [product]
        if start:
            sql += " AND trade_date>=?"
            params.append(pd.to_datetime(start).strftime("%Y-%m-%d"))
        if end:
            sql += " AND trade_date<=?"
            params.append(pd.to_datetime(end).strftime("%Y-%m-%d"))
        sql += " ORDER BY trade_date"
        with self.connect() as conn:
            df = pd.read_sql_query(sql, conn, params=params)
        if not df.empty:
            df["trade_date"] = pd.to_datetime(df["trade_date"])
        return df

    # ---------------- 价差定义与序列 ----------------
    def upsert_spread_def(self, spread) -> None:
        cfg = {
            "id": spread.id, "name": spread.name, "type": spread.type, "calc": spread.calc,
            "board": spread.board, "relation_type": spread.relation_type,
            "product": spread.product, "months": spread.months, "years": spread.years,
            "contracts": spread.contracts, "note": spread.note,
        }
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO spread_def(spread_id,name,type,calc,config_json,updated_at)"
                " VALUES(?,?,?,?,?,?) ON CONFLICT(spread_id) DO UPDATE SET "
                "name=excluded.name,type=excluded.type,calc=excluded.calc,"
                "config_json=excluded.config_json,updated_at=excluded.updated_at",
                (spread.id, spread.name, spread.type, spread.calc,
                 json.dumps(cfg, ensure_ascii=False), datetime.now().isoformat(timespec="seconds")),
            )

    def upsert_spread_daily(self, spread_id: str, df: pd.DataFrame) -> int:
        if df is None or df.empty:
            return 0
        rows = [
            (spread_id, pd.to_datetime(r["trade_date"]).strftime("%Y-%m-%d"),
             str(r.get("leg_a") or ""), str(r.get("leg_b") or ""),
             _f(r.get("leg_a_price")), _f(r.get("leg_b_price")), _f(r.get("value")),
             str(r.get("calc") or ""), int(r.get("roll_flag") or 0), _f(r.get("roll_cost") or 0))
            for _, r in df.iterrows()
        ]
        with self.connect() as conn:
            conn.executemany(
                "INSERT INTO spread_daily(spread_id,trade_date,leg_a,leg_b,leg_a_price,"
                "leg_b_price,value,calc,roll_flag,roll_cost) VALUES(?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(spread_id,trade_date) DO UPDATE SET "
                "leg_a=excluded.leg_a,leg_b=excluded.leg_b,leg_a_price=excluded.leg_a_price,"
                "leg_b_price=excluded.leg_b_price,value=excluded.value,calc=excluded.calc,"
                "roll_flag=excluded.roll_flag,roll_cost=excluded.roll_cost",
                rows,
            )
        return len(rows)

    def load_spread_daily(self, spread_id: str, start: Optional[str] = None,
                          end: Optional[str] = None) -> pd.DataFrame:
        sql = ("SELECT trade_date,leg_a,leg_b,leg_a_price,leg_b_price,value,calc,roll_flag,roll_cost"
               " FROM spread_daily WHERE spread_id=?")
        params: List[Any] = [spread_id]
        if start:
            sql += " AND trade_date>=?"
            params.append(pd.to_datetime(start).strftime("%Y-%m-%d"))
        if end:
            sql += " AND trade_date<=?"
            params.append(pd.to_datetime(end).strftime("%Y-%m-%d"))
        sql += " ORDER BY trade_date"
        with self.connect() as conn:
            df = pd.read_sql_query(sql, conn, params=params)
        if not df.empty:
            df["trade_date"] = pd.to_datetime(df["trade_date"])
        return df

    # ---------------- 统计量 ----------------
    def save_stats(self, spread_id: str, asof_date: str, stats: Dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO spread_stats(spread_id,asof_date,window,n_obs,mean,std,z,pct,"
                "adf_p,hurst,half_life,vol_pct,stationary,regime) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(spread_id,asof_date) DO UPDATE SET window=excluded.window,"
                "n_obs=excluded.n_obs,mean=excluded.mean,std=excluded.std,z=excluded.z,"
                "pct=excluded.pct,adf_p=excluded.adf_p,hurst=excluded.hurst,"
                "half_life=excluded.half_life,vol_pct=excluded.vol_pct,"
                "stationary=excluded.stationary,regime=excluded.regime",
                (spread_id, str(asof_date), int(stats.get("window") or 0),
                 int(stats.get("n_obs") or 0), _f(stats.get("mean")), _f(stats.get("std")),
                 _f(stats.get("z")), _f(stats.get("pct")), _f(stats.get("adf_p")),
                 _f(stats.get("hurst")), _f(stats.get("half_life")), _f(stats.get("vol_pct")),
                 int(bool(stats.get("stationary"))), str(stats.get("regime") or "")),
            )

    def load_stats(self, spread_id: str, limit: int = 20) -> pd.DataFrame:
        with self.connect() as conn:
            df = pd.read_sql_query(
                "SELECT * FROM spread_stats WHERE spread_id=? ORDER BY asof_date DESC LIMIT ?",
                conn, params=[spread_id, limit],
            )
        return df

    # ---------------- 信号 ----------------
    def save_signal(self, spread_id: str, asof_date: str, decision) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO hedge_signals(spread_id,asof_date,action,direction,score,z,pct,"
                "adf_p,hurst,vol_pct,fundamental_flag,reasons_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(spread_id,asof_date) DO UPDATE SET action=excluded.action,"
                "direction=excluded.direction,score=excluded.score,z=excluded.z,pct=excluded.pct,"
                "adf_p=excluded.adf_p,hurst=excluded.hurst,vol_pct=excluded.vol_pct,"
                "fundamental_flag=excluded.fundamental_flag,reasons_json=excluded.reasons_json",
                (spread_id, str(asof_date), decision.action, decision.direction,
                 _f(decision.score), _f(decision.z), _f(decision.pct), _f(decision.adf_p),
                 _f(decision.hurst), _f(decision.vol_pct), decision.fundamental_flag,
                 json.dumps(decision.reasons, ensure_ascii=False)),
            )

    def latest_signals(self, asof: Optional[str] = None) -> pd.DataFrame:
        where, params = "", []
        if asof:
            where, params = "WHERE trade_date=?", [str(asof)]
        sql = f"""
            SELECT s.spread_id, d.name, h.asof_date, s.trade_date, s.leg_a, s.leg_b, s.value, s.calc,
                   st.pct, st.z, st.adf_p, st.hurst, st.vol_pct, st.n_obs,
                   h.action, h.direction, h.score, h.fundamental_flag, h.reasons_json
            FROM hedge_signals h
            JOIN spread_stats st ON st.spread_id=h.spread_id AND st.asof_date=h.asof_date
            JOIN spread_daily s ON s.spread_id=h.spread_id AND s.trade_date=h.asof_date
            JOIN spread_def d ON d.spread_id=h.spread_id
            {where.replace('trade_date', 'h.asof_date') if where else ''}
            ORDER BY h.asof_date DESC, ABS(COALESCE(st.z,0)) DESC
        """
        with self.connect() as conn:
            df = pd.read_sql_query(sql, conn, params=params)
        if not asof and not df.empty:
            df = (df.sort_values(["spread_id", "asof_date"])
                    .drop_duplicates("spread_id", keep="last")
                    .sort_values("asof_date", ascending=False))
        return df

    def signal_history(self, spread_id: str, limit: int = 250) -> pd.DataFrame:
        with self.connect() as conn:
            return pd.read_sql_query(
                "SELECT * FROM hedge_signals WHERE spread_id=? ORDER BY asof_date DESC LIMIT ?",
                conn, params=[spread_id, limit],
            )

    # ---------------- 基本面 ----------------
    def save_fundamentals(self, product: str, rows: Iterable[Dict[str, Any]], source: str) -> int:
        payload = [
            (product, str(r["trade_date"]), _f(r.get("basis_rate")), _f(r.get("basis_pct")),
             _f(r.get("warehouse")), _f(r.get("warehouse_chg_5d")), source)
            for r in rows
        ]
        if not payload:
            return 0
        with self.connect() as conn:
            conn.executemany(
                "INSERT INTO fundamentals_daily(product,trade_date,basis_rate,basis_pct,"
                "warehouse,warehouse_chg_5d,source) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(product,trade_date) DO UPDATE SET basis_rate=excluded.basis_rate,"
                "basis_pct=excluded.basis_pct,warehouse=excluded.warehouse,"
                "warehouse_chg_5d=excluded.warehouse_chg_5d,source=excluded.source",
                payload,
            )
        return len(payload)

    def load_fundamentals(self, product: str) -> pd.DataFrame:
        with self.connect() as conn:
            df = pd.read_sql_query(
                "SELECT * FROM fundamentals_daily WHERE product=? ORDER BY trade_date",
                conn, params=[product],
            )
        return df

    def load_contract_panel(self, product: str, start: Optional[str] = None,
                           end: Optional[str] = None) -> pd.DataFrame:
        """某品种全部合约的日线长表，用于滚动期限差构建。"""
        sql = ("SELECT contract,trade_date,close,oi,volume FROM contract_daily WHERE product=?")
        params: List[Any] = [product]
        if start:
            sql += " AND trade_date>=?"; params.append(pd.to_datetime(start).strftime("%Y-%m-%d"))
        if end:
            sql += " AND trade_date<=?"; params.append(pd.to_datetime(end).strftime("%Y-%m-%d"))
        sql += " ORDER BY trade_date, contract"
        with self.connect() as conn:
            df = pd.read_sql_query(sql, conn, params=params)
        if not df.empty:
            df["trade_date"] = pd.to_datetime(df["trade_date"])
        return df

    # ---------------- CFTC COT ----------------
    def upsert_cftc_cot(self, rows: Iterable[Dict[str, Any]]) -> int:
        """COT 周度面板幂等 upsert（主键 commodity+week）。"""
        payload = [
            (str(r["commodity"]), str(r["week"]), str(r.get("name_cn") or ""),
             str(r.get("report_date") or ""), _f(r.get("oi")), _f(r.get("mm_long")),
             _f(r.get("mm_short")), _f(r.get("mm_net")), _f(r.get("d_mm_net")),
             _f(r.get("cot")), int(r.get("dir") or 0), _f(r.get("close")),
             datetime.now().isoformat(timespec="seconds"))
            for r in rows
        ]
        if not payload:
            return 0
        with self.connect() as conn:
            conn.executemany(
                "INSERT INTO cftc_cot_weekly(commodity,week,name_cn,report_date,oi,mm_long,"
                "mm_short,mm_net,d_mm_net,cot,dir,close,fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(commodity,week) DO UPDATE SET name_cn=excluded.name_cn,"
                "report_date=excluded.report_date,oi=excluded.oi,mm_long=excluded.mm_long,"
                "mm_short=excluded.mm_short,mm_net=excluded.mm_net,d_mm_net=excluded.d_mm_net,"
                "cot=excluded.cot,dir=excluded.dir,close=excluded.close,"
                "fetched_at=excluded.fetched_at",
                payload,
            )
        return len(payload)

    def load_cftc_cot(self, commodity: Optional[str] = None,
                      start: Optional[str] = None, end: Optional[str] = None) -> pd.DataFrame:
        sql = "SELECT * FROM cftc_cot_weekly"
        params: List[Any] = []
        conds: List[str] = []
        if commodity:
            conds.append("commodity=?")
            params.append(commodity)
        if start:
            conds.append("week>=?")
            params.append(pd.to_datetime(start).strftime("%Y-%m-%d"))
        if end:
            conds.append("week<=?")
            params.append(pd.to_datetime(end).strftime("%Y-%m-%d"))
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY commodity, week"
        with self.connect() as conn:
            df = pd.read_sql_query(sql, conn, params=params)
        if not df.empty:
            df["week"] = pd.to_datetime(df["week"])
        return df

    def upsert_cftc_episode(self, rows: Iterable[Dict[str, Any]]) -> int:
        payload = [
            (str(r["commodity"]), str(r["asof_week"]), str(r.get("start_week") or ""),
             int(r.get("dir") or 0), int(r.get("held_weeks") or 0), _f(r.get("cot")),
             _f(r.get("pnl_pct")), str(r.get("action") or ""), str(r.get("stage") or ""),
             str(r.get("advice") or ""))
            for r in rows
        ]
        if not payload:
            return 0
        with self.connect() as conn:
            conn.executemany(
                "INSERT INTO cftc_cot_episode(commodity,asof_week,start_week,dir,held_weeks,"
                "cot,pnl_pct,action,stage,advice) VALUES(?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(commodity,asof_week) DO UPDATE SET start_week=excluded.start_week,"
                "dir=excluded.dir,held_weeks=excluded.held_weeks,cot=excluded.cot,"
                "pnl_pct=excluded.pnl_pct,action=excluded.action,stage=excluded.stage,"
                "advice=excluded.advice",
                payload,
            )
        return len(payload)

    def latest_cftc_episodes(self) -> pd.DataFrame:
        sql = """
            SELECT e.*, w.name_cn, w.close, w.mm_net, w.report_date
            FROM cftc_cot_episode e
            LEFT JOIN cftc_cot_weekly w
              ON w.commodity=e.commodity AND w.week=e.asof_week
            WHERE e.asof_week = (SELECT MAX(asof_week) FROM cftc_cot_episode x
                                 WHERE x.commodity=e.commodity)
            ORDER BY e.commodity
        """
        with self.connect() as conn:
            return pd.read_sql_query(sql, conn)

    # ---------------- 汇总 ----------------
    def table_counts(self) -> Dict[str, int]:
        out = {}
        with self.connect() as conn:
            for t in ("contract_daily", "continuous_daily", "spread_def", "spread_daily",
                      "spread_stats", "hedge_signals", "fundamentals_daily",
                      "cftc_cot_weekly", "cftc_cot_episode"):
                out[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        return out


def _f(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return float(v)