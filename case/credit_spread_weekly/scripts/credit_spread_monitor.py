#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
周度信用风险传导监控脚本
三层传导链路：L2货币信用链 → L3银行层 → L4外汇层

数据源：
  - FRED: SOFR, IORB, IG OAS, HY OAS, DXY(贸易加权)
  - 联网搜索兜底: CDS(CoreWeave/Oracle/SoftBank), KBE银行ETF, USDCNH
  - 回购压力: SOFR vs GC(广义担保品利率)

阈值体系（来自宏观流动性项目信用层 + 三层传导框架）：
  L2 货币信用链（传导起点）
    - SOFR-IORB 利差 > 15bp → 流动性危机前兆
    - 投资级(IG) OAS: 4周变动 > 50bp 或 单周跳升 > 20bp
    - 高收益(HY) OAS: 4周变动 > 100bp
    - 5Y CDS: CoreWeave > 1000bp, Oracle > 300bp, SoftBank > 400bp(观察)
    - 回购压力: SOFR-GC 利差走阔(>10bp预警)

  L3 银行层（传导中继）
    - 银行股ETF(KBE): 单周跌幅 > 7%
    - HY利差 + 银行股同步下跌 → 传导确认

  L4 外汇层（传导放大器）
    - DXY美元指数: 单周涨幅 > 2%
    - USDCNH: 单周波动 > 1.5% 或 突破关键位(7.0/7.2)

传导判定：
  单层触发 → 独立风险
  两层触发 → 传导中
  三层触发 → 系统性风险
"""
# ── 去云盘化：/Coze/Drive、/opt/coze/.fuse_drive、/app/data 三个旧根可重定向 ──
# 换机器时设 CASE_DRIVE_ROOT / CASE_FUSE_ROOT / CASE_APP_DATA，未设则原样返回。
def _cloud_translate(_f=__file__):
    import os as _o
    import sys as _s
    _d = _o.path.dirname(_o.path.abspath(_f))
    for _ in range(6):
        _c = _o.path.join(_d, "_lib")
        if _o.path.isfile(_o.path.join(_c, "cloud_paths.py")):
            if _c not in _s.path:
                _s.path.insert(0, _c)
            from cloud_paths import translate as _t
            return _t
        _d = _o.path.dirname(_d)
    return lambda p: p


_cp = _cloud_translate()

import os
import sys
import json
import sqlite3
import subprocess
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ─── 路径配置 ───
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "outputs"
CONFIG_DIR = BASE_DIR / "config"
DB_PATH = DATA_DIR / "credit_monitor.db"

DATA_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
CONFIG_DIR.mkdir(exist_ok=True)

# ─── FRED CLI 路径（从项目skill目录调用） ───
FRED_SKILL_DIR = Path(_cp("/Coze/Drive/信用利差预警/.skills/skill_fred-data-skill"))
FRED_WRAPPER = FRED_SKILL_DIR / "scripts" / "_cli_wrapper.py"

# ─── 数据缓存（FRED CLI超时/搜索不可用时的兜底数据） ───
CACHE_PATH = DATA_DIR / "data_cache.json"

_cache_data = None

def load_cache() -> dict:
    """加载数据缓存文件"""
    global _cache_data
    if _cache_data is not None:
        return _cache_data
    if CACHE_PATH.exists():
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                _cache_data = json.load(f)
            print(f"[INFO] 数据缓存已加载: {CACHE_PATH}", file=sys.stderr)
            return _cache_data
        except Exception as e:
            print(f"[WARN] 缓存加载失败: {e}", file=sys.stderr)
    _cache_data = {}
    return _cache_data


def cache_get_source(key: str) -> Optional[dict]:
    """从缓存中获取单个指标数据"""
    cache = load_cache()
    sources = cache.get("sources", {})
    return sources.get(key)


def cache_get_history(series_id: str) -> List[Tuple[str, float]]:
    """从缓存中获取历史序列"""
    cache = load_cache()
    history = cache.get("history", {})
    items = history.get(series_id, [])
    return [(item["date"], item["value"]) for item in items]


# ============================================================
#  数据库（状态/历史）
# ============================================================

def init_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS indicator_history (
        indicator_id TEXT,
        date TEXT,
        value REAL,
        source TEXT,
        fetched_at TEXT,
        PRIMARY KEY (indicator_id, date)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS alert_log (
        alert_id TEXT PRIMARY KEY,
        indicator_id TEXT,
        alert_date TEXT,
        trigger_value REAL,
        threshold TEXT,
        risk_level TEXT,
        layer TEXT,
        acknowledged INTEGER DEFAULT 0
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS weekly_reports (
        report_date TEXT PRIMARY KEY,
        report_path TEXT,
        generated_at TEXT,
        alert_count INTEGER DEFAULT 0
    )""")
    conn.commit()
    return conn


def save_indicator(conn, indicator_id: str, date: str, value: float, source: str):
    try:
        c = conn.cursor()
        c.execute("""INSERT OR REPLACE INTO indicator_history
            (indicator_id, date, value, source, fetched_at)
            VALUES (?, ?, ?, ?, ?)""",
            (indicator_id, date, value, source, datetime.now().isoformat()))
        conn.commit()
    except sqlite3.OperationalError as e:
        print(f"[WARN] DB写入失败({indicator_id}/{date}): {e}", file=sys.stderr)
        # 重试一次
        try:
            conn2 = sqlite3.connect(str(DB_PATH), timeout=10)
            conn2.execute("PRAGMA journal_mode=WAL")
            c2 = conn2.cursor()
            c2.execute("""INSERT OR REPLACE INTO indicator_history
                (indicator_id, date, value, source, fetched_at)
                VALUES (?, ?, ?, ?, ?)""",
                (indicator_id, date, value, source, datetime.now().isoformat()))
            conn2.commit()
            conn2.close()
        except Exception as e2:
            print(f"[WARN] DB重试也失败: {e2}", file=sys.stderr)


def get_history(conn, indicator_id: str, days: int = 60) -> List[Tuple[str, float]]:
    c = conn.cursor()
    c.execute("""SELECT date, value FROM indicator_history
        WHERE indicator_id = ? AND date >= ?
        ORDER BY date ASC""",
        (indicator_id, (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")))
    return [(row[0], row[1]) for row in c.fetchall()]


def get_last_value(conn, indicator_id: str) -> Optional[Tuple[str, float]]:
    c = conn.cursor()
    c.execute("""SELECT date, value FROM indicator_history
        WHERE indicator_id = ?
        ORDER BY date DESC LIMIT 1""", (indicator_id,))
    row = c.fetchone()
    return (row[0], row[1]) if row else None


def check_alert_exists(conn, alert_id: str) -> bool:
    c = conn.cursor()
    c.execute("SELECT 1 FROM alert_log WHERE alert_id = ?", (alert_id,))
    return c.fetchone() is not None


def record_alert(conn, alert_id: str, indicator_id: str, alert_date: str,
                 trigger_value: float, threshold: str, risk_level: str, layer: str):
    c = conn.cursor()
    c.execute("""INSERT OR IGNORE INTO alert_log
        (alert_id, indicator_id, alert_date, trigger_value, threshold, risk_level, layer)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (alert_id, indicator_id, alert_date, trigger_value, threshold, risk_level, layer))
    conn.commit()


# ============================================================
#  FRED 数据获取
# ============================================================

def fred_get_observations(series_id: str, start_date: str) -> List[Tuple[str, float]]:
    """从FRED获取时间序列，返回 [(date_str, value), ...]。失败时从缓存兜底。"""
    # 先尝试FRED CLI
    if FRED_WRAPPER.exists():
        env = os.environ.copy()
        try:
            result = subprocess.run(
                ["python3", str(FRED_WRAPPER), "call", "get-observations",
                 "--param", f"series_id={series_id}",
                 "--param", f"observation_start={start_date}"],
                capture_output=True, text=True, timeout=20, env=env,
                cwd=str(FRED_SKILL_DIR)
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                obs = data.get("observations", [])
                results = []
                for o in obs:
                    val = o.get("value", ".")
                    if val == "." or val == "":
                        continue
                    try:
                        results.append((o["date"], float(val)))
                    except (ValueError, KeyError):
                        continue
                if results:
                    return results
        except Exception as e:
            print(f"[WARN] FRED {series_id} 调用异常: {e}", file=sys.stderr)

    # FRED失败 → 从缓存兜底
    cached = cache_get_history(series_id)
    if cached:
        print(f"[INFO] FRED {series_id} 使用缓存数据({len(cached)}条)", file=sys.stderr)
    else:
        print(f"[WARN] FRED {series_id} 无缓存兜底", file=sys.stderr)
    return cached


# ============================================================
#  联网搜索兜底数据获取
# ============================================================

def search_web_value(query: str, pattern: str) -> Optional[Tuple[str, float, str]]:
    """
    用codeact SDK搜索网页获取数值。
    返回 (date_str, value, source_url) 或 None
    注意：本函数在CodeAct环境中运行时使用SDK，本地调试返回None
    """
    # 尝试导入CodeAct SDK
    try:
        from codeact_sdk import codeact_search_web  # type: ignore
        results = codeact_search_web(query)
        if not results:
            return None
        for r in results[:5]:
            text = r.get("snippet", "") + r.get("title", "")
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                try:
                    val = float(m.group(1).replace(",", "").replace("%", ""))
                    return (datetime.now().strftime("%Y-%m-%d"), val, r.get("url", ""))
                except (ValueError, IndexError):
                    continue
    except ImportError:
        pass
    return None


def fetch_cds_5y(company: str) -> Optional[Tuple[str, float, str]]:
    """获取5年期CDS利差（bps）"""
    query = f"{company} 5 year CDS spread bps 2026"
    pattern = r"([\d,]+)\s*(?:bps|bp)"
    result = search_web_value(query, pattern)
    if result:
        return result
    # 备用pattern
    query2 = f"{company} credit default swap spread"
    result = search_web_value(query2, pattern)
    if result:
        return result
    # 缓存兜底
    cache_key_map = {"CoreWeave": "COREWEAVE_CDS", "Oracle": "ORACLE_CDS", "SoftBank": "SOFTBANK_CDS"}
    cache_key = cache_key_map.get(company)
    if cache_key:
        cached = cache_get_source(cache_key)
        if cached:
            print(f"[INFO] CDS {company} 使用缓存: {cached['value']}bp ({cached['date']})", file=sys.stderr)
            return (cached["date"], cached["value"], cached.get("source", "数据缓存"))
    return None


def fetch_kbe_price() -> Optional[Tuple[str, float, str]]:
    """获取KBE银行ETF价格"""
    query = "KBE SPDR S&P Bank ETF price"
    pattern = r"\$?([\d.]+)"
    result = search_web_value(query, pattern)
    if result:
        return result
    # 缓存兜底
    cached = cache_get_source("KBE")
    if cached:
        print(f"[INFO] KBE 使用缓存: ${cached['value']} ({cached['date']})", file=sys.stderr)
        return (cached["date"], cached["value"], cached.get("source", "数据缓存"))
    return None


def fetch_dxy() -> Optional[Tuple[str, float, str]]:
    """获取美元指数DXY"""
    query = "US Dollar Index DXY live"
    pattern = r"DXY[:\s]*([\d.]+)"
    result = search_web_value(query, pattern)
    if result:
        return result
    result = search_web_value("dollar index today", r"([\d.]{2,3}\.\d+)")
    if result:
        return result
    # 缓存兜底
    cached = cache_get_source("DXY")
    if cached:
        print(f"[INFO] DXY 使用缓存: {cached['value']} ({cached['date']})", file=sys.stderr)
        return (cached["date"], cached["value"], cached.get("source", "数据缓存"))
    return None


def fetch_usdcnh() -> Optional[Tuple[str, float, str]]:
    """获取USDCNH离岸人民币汇率"""
    query = "USDCNH offshore yuan rate"
    pattern = r"USDCNH[:\s]*([\d.]+)"
    result = search_web_value(query, pattern)
    if result:
        return result
    result = search_web_value("美元兑离岸人民币", r"([67]\.\d+)")
    if result:
        return result
    # 缓存兜底
    cached = cache_get_source("USDCNH")
    if cached:
        print(f"[INFO] USDCNH 使用缓存: {cached['value']} ({cached['date']})", file=sys.stderr)
        return (cached["date"], cached["value"], cached.get("source", "数据缓存"))
    return None


def fetch_gc_repo_rate() -> Optional[Tuple[str, float, str]]:
    """获取广义担保品回购利率GC"""
    query = "general collateral repo rate GC today"
    pattern = r"([\d.]+)\s*%"
    result = search_web_value(query, pattern)
    if result:
        return result
    # 缓存兜底 (TGCR = Tri-Party General Collateral Rate)
    cached = cache_get_source("TGCR")
    if cached:
        print(f"[INFO] GC/TGCR 使用缓存: {cached['value']}% ({cached['date']})", file=sys.stderr)
        return (cached["date"], cached["value"], cached.get("source", "数据缓存"))
    return None


# ============================================================
#  IORB 利率（美联储准备金利率）
# ============================================================

def get_iorb_rate(date: str) -> Optional[float]:
    """
    获取IORB利率。
    IORB由美联储FOMC决定，变动频率低（每次议息会议可能调整）。
    从FRED获取，series_id: IORB
    """
    observations = fred_get_observations("IORB", "2024-01-01")
    if observations:
        # 取最新值
        observations.sort(key=lambda x: x[0])
        return observations[-1][1]
    # 搜索兜底
    result = search_web_value("IORB interest on reserve balances rate 2026",
                               r"([\d.]+)\s*%")
    if result:
        return result[1]
    # 缓存兜底
    cached = cache_get_source("IORB")
    if cached:
        print(f"[INFO] IORB 使用缓存: {cached['value']}%", file=sys.stderr)
        return cached["value"]
    return None


# ============================================================
#  指标计算工具
# ============================================================

def find_value_at_date(history: List[Tuple[str, float]], target_date: str) -> Optional[float]:
    """找到指定日期或之前最近的有效数据点"""
    for date, val in reversed(history):
        if date <= target_date:
            return val
    return None


def calc_weekly_change(history: List[Tuple[str, float]]) -> Optional[float]:
    """计算周变动（最新值 vs 5个交易日前）"""
    if len(history) < 6:
        return None
    latest_val = history[-1][1]
    # 找5个交易日前的值（约一周）
    week_ago_idx = max(0, len(history) - 6)
    week_ago_val = history[week_ago_idx][1]
    return latest_val - week_ago_val


def calc_4week_change(history: List[Tuple[str, float]]) -> Optional[float]:
    """计算4周变动（约20个交易日）"""
    if len(history) < 21:
        return None
    latest_val = history[-1][1]
    month_ago_idx = max(0, len(history) - 21)
    month_ago_val = history[month_ago_idx][1]
    return latest_val - month_ago_val


def calc_pct_change(history: List[Tuple[str, float]], weeks: int = 1) -> Optional[float]:
    """计算百分比变动（用于价格类指标）"""
    n = weeks * 5  # 每周约5个交易日
    if len(history) < n + 1:
        return None
    latest_val = history[-1][1]
    past_idx = max(0, len(history) - n - 1)
    past_val = history[past_idx][1]
    if past_val == 0:
        return None
    return (latest_val - past_val) / past_val * 100


# ============================================================
#  三层监控指标定义与采集
# ============================================================

class IndicatorResult:
    def __init__(self, indicator_id: str, name: str, layer: str,
                 current: Optional[float], unit: str = "",
                 weekly_change: Optional[float] = None,
                 change_4w: Optional[float] = None,
                 threshold: str = "",
                 triggered: bool = False,
                 risk_level: str = "低",
                 data_date: str = "",
                 source: str = "",
                 note: str = ""):
        self.indicator_id = indicator_id
        self.name = name
        self.layer = layer
        self.current = current
        self.unit = unit
        self.weekly_change = weekly_change
        self.change_4w = change_4w
        self.threshold = threshold
        self.triggered = triggered
        self.risk_level = risk_level
        self.data_date = data_date
        self.source = source
        self.note = note


def collect_l2_indicators(conn) -> List[IndicatorResult]:
    """L2 货币信用层数据采集与判断"""
    results = []

    # ── 1. SOFR-IORB 利差 ──
    sofr_data = fred_get_observations("SOFR", "2026-07-01")
    iorb_val = get_iorb_rate(datetime.now().strftime("%Y-%m-%d"))

    sofr_latest = None
    sofr_date = ""
    if sofr_data:
        sofr_data.sort(key=lambda x: x[0])
        sofr_date, sofr_latest = sofr_data[-1]
        for d, v in sofr_data:
            save_indicator(conn, "SOFR", d, v, "FRED")

    sofr_iorb_spread = None
    if sofr_latest is not None and iorb_val is not None:
        sofr_iorb_spread = (sofr_latest - iorb_val) * 100  # 转bp
        save_indicator(conn, "SOFR_IORB_SPREAD", sofr_date, sofr_iorb_spread, "FRED")

    spread_hist = get_history(conn, "SOFR_IORB_SPREAD", days=60)
    w_change = calc_weekly_change(spread_hist) if len(spread_hist) > 5 else None

    triggered = sofr_iorb_spread is not None and sofr_iorb_spread > 15
    risk = "严重" if triggered else ("中" if sofr_iorb_spread and sofr_iorb_spread > 10 else "低")

    results.append(IndicatorResult(
        indicator_id="SOFR_IORB",
        name="SOFR - IORB 利差",
        layer="L2",
        current=round(sofr_iorb_spread, 2) if sofr_iorb_spread is not None else None,
        unit="bp",
        weekly_change=round(w_change, 2) if w_change is not None else None,
        threshold="> 15bp",
        triggered=triggered,
        risk_level=risk,
        data_date=sofr_date,
        source="FRED (SOFR + IORB)" if sofr_latest and iorb_val else "无数据",
        note="2020年3月流动性危机前兆指标。IOER改IORB后沿用同一逻辑"
    ))

    # ── 2. 投资级信用利差 IG OAS ──
    ig_data = fred_get_observations("BAMLC0A0CM", "2026-07-01")
    ig_latest = None
    ig_date = ""
    if ig_data:
        ig_data.sort(key=lambda x: x[0])
        ig_date, ig_latest = ig_data[-1]
        for d, v in ig_data:
            save_indicator(conn, "IG_OAS", d, v, "FRED")

    ig_hist = get_history(conn, "IG_OAS", days=60)
    ig_wchange = calc_weekly_change(ig_hist) if ig_hist else None
    ig_4wchange = calc_4week_change(ig_hist) if ig_hist else None

    ig_triggered = False
    ig_risk = "低"
    if ig_latest is not None:
        # 单周跳升 > 20bp 或 4周变动 > 50bp
        w_jump = ig_wchange is not None and ig_wchange > 0.20  # 20bp = 0.20%
        m_jump = ig_4wchange is not None and ig_4wchange > 0.50  # 50bp = 0.50%
        ig_triggered = w_jump or m_jump
        if ig_triggered:
            ig_risk = "高"
        elif (ig_wchange and ig_wchange > 0.10) or (ig_4wchange and ig_4wchange > 0.25):
            ig_risk = "中"

    results.append(IndicatorResult(
        indicator_id="IG_OAS",
        name="投资级(IG) OAS利差",
        layer="L2",
        current=round(ig_latest, 2) if ig_latest is not None else None,
        unit="%",
        weekly_change=round(ig_wchange * 100, 1) if ig_wchange is not None else None,
        change_4w=round(ig_4wchange * 100, 1) if ig_4wchange is not None else None,
        threshold="4周变动>50bp 或 单周>20bp",
        triggered=ig_triggered,
        risk_level=ig_risk,
        data_date=ig_date,
        source="FRED ICE BofA US Corporate Index",
        note=("4周变动触发" if ig_4wchange and ig_4wchange > 0.50 else
              ("单周跳升触发" if ig_wchange and ig_wchange > 0.20 else "正常"))
    ))

    # ── 3. 高收益信用利差 HY OAS ──
    hy_data = fred_get_observations("BAMLH0A0HYM2", "2026-07-01")
    hy_latest = None
    hy_date = ""
    if hy_data:
        hy_data.sort(key=lambda x: x[0])
        hy_date, hy_latest = hy_data[-1]
        for d, v in hy_data:
            save_indicator(conn, "HY_OAS", d, v, "FRED")

    hy_hist = get_history(conn, "HY_OAS", days=60)
    hy_wchange = calc_weekly_change(hy_hist) if hy_hist else None
    hy_4wchange = calc_4week_change(hy_hist) if hy_hist else None

    hy_triggered = False
    hy_risk = "低"
    if hy_latest is not None:
        hy_triggered = hy_4wchange is not None and hy_4wchange > 1.00  # 100bp = 1.00%
        if hy_triggered:
            hy_risk = "严重"
        elif hy_4wchange and hy_4wchange > 0.50:
            hy_risk = "高"
        elif hy_wchange and hy_wchange > 0.25:
            hy_risk = "中"

    results.append(IndicatorResult(
        indicator_id="HY_OAS",
        name="高收益(HY) OAS利差",
        layer="L2",
        current=round(hy_latest, 2) if hy_latest is not None else None,
        unit="%",
        weekly_change=round(hy_wchange * 100, 1) if hy_wchange is not None else None,
        change_4w=round(hy_4wchange * 100, 1) if hy_4wchange is not None else None,
        threshold="4周变动 > 100bp",
        triggered=hy_triggered,
        risk_level=hy_risk,
        data_date=hy_date,
        source="FRED ICE BofA US High Yield Index",
        note="信用风险核心指标，4周走阔超100bp视为风险事件"
    ))

    # ── 4. 5年期 CDS（重点标的） ──
    cds_targets = [
        ("CoreWeave", "COREWEAVE_CDS", 1000, "严重"),
        ("Oracle", "ORACLE_CDS", 300, "高"),
        ("SoftBank", "SOFTBANK_CDS", 400, "中"),  # 观察项
    ]
    for company, cds_id, threshold, t_risk in cds_targets:
        cds_result = fetch_cds_5y(company)
        cds_val = None
        cds_date = ""
        cds_src = ""
        if cds_result:
            cds_date, cds_val, cds_src = cds_result
            save_indicator(conn, cds_id, cds_date, cds_val, "web_search")

        cds_hist = get_history(conn, cds_id, days=60)
        cds_wchange = calc_weekly_change(cds_hist) if cds_hist else None

        cds_triggered = cds_val is not None and cds_val > threshold
        risk = t_risk if cds_triggered else ("中" if cds_val and cds_val > threshold * 0.7 else "低")
        is_watch = "（观察项）" if company == "SoftBank" else ""

        results.append(IndicatorResult(
            indicator_id=cds_id,
            name=f"{company} 5Y CDS{is_watch}",
            layer="L2",
            current=round(cds_val, 0) if cds_val is not None else None,
            unit="bp",
            weekly_change=round(cds_wchange, 0) if cds_wchange is not None else None,
            threshold=f"> {threshold}bp",
            triggered=cds_triggered,
            risk_level=risk,
            data_date=cds_date,
            source=cds_src or "无数据",
            note="搜索兜底数据，非彭博实时报价，仅供参考" if cds_val else "搜索未获取到有效数据"
        ))

    # ── 5. 回购市场压力（SOFR vs GC） ──
    gc_result = fetch_gc_repo_rate()
    gc_val = None
    gc_date = ""
    if gc_result:
        gc_date, gc_val, gc_src = gc_result
        save_indicator(conn, "GC_REPO", gc_date, gc_val, "web_search")

    repo_spread = None
    repo_src = "无数据"
    if sofr_latest is not None and gc_val is not None:
        repo_spread = (sofr_latest - gc_val) * 100  # bp
        repo_src = "FRED SOFR + 搜索GC"
        save_indicator(conn, "SOFR_GC_SPREAD", sofr_date, repo_spread, repo_src)

    repo_hist = get_history(conn, "SOFR_GC_SPREAD", days=30)
    repo_wchange = calc_weekly_change(repo_hist) if len(repo_hist) > 5 else None

    repo_triggered = repo_spread is not None and repo_spread > 10
    repo_risk = "高" if repo_triggered else ("中" if repo_spread and repo_spread > 5 else "低")

    results.append(IndicatorResult(
        indicator_id="REPO_STRESS",
        name="回购压力(SOFR-GC利差)",
        layer="L2",
        current=round(repo_spread, 2) if repo_spread is not None else None,
        unit="bp",
        weekly_change=round(repo_wchange, 2) if repo_wchange is not None else None,
        threshold="走阔 > 10bp",
        triggered=repo_triggered,
        risk_level=repo_risk,
        data_date=sofr_date or gc_date,
        source=repo_src,
        note="利差走阔表示非担保品融资压力上升，抵押品链预警信号"
    ))

    return results


def collect_l3_indicators(conn) -> List[IndicatorResult]:
    """L3 银行层数据采集与判断"""
    results = []

    # ── 1. 银行股ETF(KBE) 单周跌幅 ──
    kbe_result = fetch_kbe_price()
    kbe_val = None
    kbe_date = ""
    kbe_src = ""
    if kbe_result:
        kbe_date, kbe_val, kbe_src = kbe_result
        save_indicator(conn, "KBE", kbe_date, kbe_val, "web_search")

    kbe_hist = get_history(conn, "KBE", days=60)
    kbe_wchange_pct = calc_pct_change(kbe_hist, weeks=1) if kbe_hist else None

    kbe_triggered = kbe_wchange_pct is not None and kbe_wchange_pct < -7
    kbe_risk = "严重" if kbe_triggered else ("高" if kbe_wchange_pct and kbe_wchange_pct < -4 else
                                              "中" if kbe_wchange_pct and kbe_wchange_pct < -2 else "低")

    results.append(IndicatorResult(
        indicator_id="KBE",
        name="银行股ETF(KBE)",
        layer="L3",
        current=round(kbe_val, 2) if kbe_val is not None else None,
        unit="USD",
        weekly_change=round(kbe_wchange_pct, 2) if kbe_wchange_pct is not None else None,
        threshold="单周跌幅 > 7%",
        triggered=kbe_triggered,
        risk_level=kbe_risk,
        data_date=kbe_date,
        source=kbe_src or "无数据",
        note="搜索兜底数据，非实时行情，仅供方向参考"
    ))

    # ── 2. HY+银行股联动（传导确认） ──
    # 此指标为合成指标，从L2和L3结果中推导
    hy_hist = get_history(conn, "HY_OAS", days=60)
    hy_4wchange = calc_4week_change(hy_hist) if hy_hist else None
    hy_up = hy_4wchange is not None and hy_4wchange > 1.00  # HY 4周走阔超100bp
    banks_down = kbe_wchange_pct is not None and kbe_wchange_pct < -3  # 银行股下跌

    transmission = hy_up and banks_down
    trans_risk = "严重" if transmission else ("中" if (hy_up or banks_down) else "低")

    results.append(IndicatorResult(
        indicator_id="HY_BANK_TRANSMISSION",
        name="HY-银行股传导确认",
        layer="L3",
        current=None,
        unit="-",
        threshold="HY 4周>100bp 且 银行股同步下跌",
        triggered=transmission,
        risk_level=trans_risk,
        data_date=kbe_date,
        source="合成指标(L2 HY + L3 KBE)",
        note=("信用风险向银行体系传导确认" if transmission else
              "仅HY走阔" if hy_up else
              "仅银行下跌" if banks_down else "无传导信号")
    ))

    return results


def collect_l4_indicators(conn) -> List[IndicatorResult]:
    """L4 外汇层数据采集与判断"""
    results = []

    # ── 1. 美元指数 DXY ──
    # 先试FRED贸易加权美元指数
    dxy_data = fred_get_observations("DTWEXBGS", "2026-07-01")  # 广义贸易加权美元指数
    dxy_latest = None
    dxy_date = ""
    dxy_src = "FRED (广义贸易加权)"
    if dxy_data:
        dxy_data.sort(key=lambda x: x[0])
        dxy_date, dxy_latest = dxy_data[-1]
        for d, v in dxy_data:
            save_indicator(conn, "DXY", d, v, "FRED")
    else:
        # 搜索兜底获取DXY
        dxy_result = fetch_dxy()
        if dxy_result:
            dxy_date, dxy_latest, dxy_src = dxy_result
            save_indicator(conn, "DXY", dxy_date, dxy_latest, "web_search")

    dxy_hist = get_history(conn, "DXY", days=60)
    dxy_wchange_pct = calc_pct_change(dxy_hist, weeks=1) if dxy_hist else None

    dxy_triggered = dxy_wchange_pct is not None and dxy_wchange_pct > 2.0
    dxy_risk = "高" if dxy_triggered else ("中" if dxy_wchange_pct and dxy_wchange_pct > 1.0 else "低")

    results.append(IndicatorResult(
        indicator_id="DXY",
        name="美元指数(DXY)",
        layer="L4",
        current=round(dxy_latest, 2) if dxy_latest is not None else None,
        unit="点",
        weekly_change=round(dxy_wchange_pct, 2) if dxy_wchange_pct is not None else None,
        threshold="单周涨幅 > 2%",
        triggered=dxy_triggered,
        risk_level=dxy_risk,
        data_date=dxy_date,
        source=dxy_src if dxy_latest else "无数据",
        note="美元走强=全球流动性收紧=风险资产承压"
    ))

    # ── 2. USDCNH 离岸人民币 ──
    # 先尝试FRED
    cnh_data = fred_get_observations("DEXCHUS", "2026-07-01")
    cnh_latest = None
    cnh_date = ""
    cnh_src = "FRED"
    if cnh_data:
        cnh_data.sort(key=lambda x: x[0])
        cnh_date, cnh_latest = cnh_data[-1]
        for d, v in cnh_data:
            save_indicator(conn, "USDCNY", d, v, "FRED")
    else:
        # 搜索兜底
        cnh_result = fetch_usdcnh()
        if cnh_result:
            cnh_date, cnh_latest, cnh_src = cnh_result
            save_indicator(conn, "USDCNH", cnh_date, cnh_latest, "web_search")

    cnh_hist = get_history(conn, "USDCNY", days=60) or get_history(conn, "USDCNH", days=60)
    cnh_wchange_pct = calc_pct_change(cnh_hist, weeks=1) if cnh_hist else None

    # 关键心理位检查
    key_levels = [7.0, 7.2]
    breakthrough = False
    breakthrough_level = ""
    if cnh_latest is not None:
        for level in key_levels:
            # 上破
            if cnh_latest > level:
                breakthrough = True
                breakthrough_level = f"突破{level}"

    vol_triggered = cnh_wchange_pct is not None and abs(cnh_wchange_pct) > 1.5
    cnh_triggered = vol_triggered or breakthrough

    cnh_risk = "高" if cnh_triggered else ("中" if breakthrough or (cnh_wchange_pct and abs(cnh_wchange_pct) > 0.8) else "低")

    results.append(IndicatorResult(
        indicator_id="USDCNH",
        name="USDCNH 离岸人民币",
        layer="L4",
        current=round(cnh_latest, 4) if cnh_latest is not None else None,
        unit="",
        weekly_change=round(cnh_wchange_pct, 2) if cnh_wchange_pct is not None else None,
        threshold="单周波动 > 1.5% 或 突破7.0/7.2",
        triggered=cnh_triggered,
        risk_level=cnh_risk,
        data_date=cnh_date,
        source=cnh_src if cnh_latest else "无数据",
        note=f"{breakthrough_level + '；' if breakthrough else ''}人民币大幅贬值=资本外流压力"
    ))

    return results


# ============================================================
#  传导判定
# ============================================================

def determine_transmission(all_indicators: List[IndicatorResult]) -> Tuple[str, str, List[str]]:
    """
    判定传导状态
    返回: (状态等级, 状态描述, 触发层列表)
    """
    triggered_by_layer = {"L2": False, "L3": False, "L4": False}
    triggered_names = {"L2": [], "L3": [], "L4": []}

    for ind in all_indicators:
        if ind.triggered:
            triggered_by_layer[ind.layer] = True
            triggered_names[ind.layer].append(ind.name)

    triggered_layers = [layer for layer in ["L2", "L3", "L4"] if triggered_by_layer[layer]]
    n = len(triggered_layers)

    if n == 0:
        return ("正常", "当前各层指标均未触发阈值，信用环境稳定", [])
    elif n == 1:
        layer = triggered_layers[0]
        layer_names = {"L2": "货币信用链", "L3": "银行层", "L4": "外汇层"}
        desc = f"独立风险 — {layer_names[layer]}触发：" + "、".join(triggered_names[layer])
        return ("独立风险", desc, triggered_layers)
    elif n == 2:
        # 判断上下游关系
        has_l2 = triggered_by_layer["L2"]
        has_l3 = triggered_by_layer["L3"]
        has_l4 = triggered_by_layer["L4"]

        if has_l2 and has_l3:
            direction = "L2→L3（信用风险向银行体系传导）"
        elif has_l3 and has_l4:
            direction = "L3→L4（银行压力外溢至外汇市场）"
        elif has_l2 and has_l4:
            direction = "L2→L4（信用风险直接通过汇率渠道传导，跳过银行层）"
        else:
            direction = "两层同时触发"

        triggers = []
        for layer in ["L2", "L3", "L4"]:
            if triggered_by_layer[layer]:
                triggers.extend(triggered_names[layer])
        desc = f"传导中 — {direction}。触发指标：" + "、".join(triggers)
        return ("传导中", desc, triggered_layers)
    else:  # n == 3
        triggers = []
        for layer in ["L2", "L3", "L4"]:
            triggers.extend(triggered_names[layer])
        desc = "系统性风险 — 三层全部触发！L2→L3→L4完整传导链形成，建议立即启动避险预案。触发指标：" + "、".join(triggers)
        return ("系统性风险", desc, triggered_layers)


# ============================================================
#  报告生成
# ============================================================

def risk_emoji(level: str) -> str:
    return {"低": "🟢", "中": "🟡", "高": "🟠", "严重": "🔴"}.get(level, "⚪")


def format_value(val: Optional[float], unit: str = "") -> str:
    if val is None:
        return "无数据"
    return f"{val}{unit}"


def generate_report(conn, all_indicators: List[IndicatorResult],
                    transmission_status: str, transmission_desc: str,
                    triggered_layers: List[str]) -> str:
    """生成周度监控报告（Markdown）"""
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")

    lines = []
    lines.append(f"# 信用风险传导周度监控 | {date_str}")
    lines.append("")

    # 总体状态
    status_emoji = {
        "正常": "🟢",
        "独立风险": "🟡",
        "传导中": "🟠",
        "系统性风险": "🔴"
    }.get(transmission_status, "⚪")

    lines.append(f"## 总体状态：{status_emoji} {transmission_status}")
    lines.append("")
    lines.append(f"> {transmission_desc}")
    lines.append("")

    # 各层指标表
    for layer in ["L2", "L3", "L4"]:
        layer_names = {"L2": "L2 货币信用链（传导起点）",
                       "L3": "L3 银行层（传导中继）",
                       "L4": "L4 外汇层（传导放大器）"}
        layer_indicators = [ind for ind in all_indicators if ind.layer == layer]

        lines.append(f"## {layer_names[layer]}")
        lines.append("")
        lines.append("| 指标 | 当前值 | 周变动 | 4周变动 | 阈值 | 状态 | 风险等级 | 数据日期 | 来源 |")
        lines.append("| --- | ---: | ---: | ---: | --- | :---: | :---: | --- | --- |")

        for ind in layer_indicators:
            current_str = format_value(ind.current, ind.unit)
            wchange_str = format_value(
                ind.weekly_change,
                "bp" if ind.unit == "bp" else ("%" if ind.unit in ("%", "点", "") else "%")
            ) if ind.weekly_change is not None else "-"
            change_4w_str = format_value(
                ind.change_4w, "bp"
            ) if ind.change_4w is not None else "-"
            status = "✅ 触发" if ind.triggered else "正常"
            risk = f"{risk_emoji(ind.risk_level)} {ind.risk_level}"
            date = ind.data_date or "-"
            source = ind.source or "-"

            lines.append(
                f"| {ind.name} | {current_str} | {wchange_str} | {change_4w_str} "
                f"| {ind.threshold} | {status} | {risk} | {date} | {source[:30]} |"
            )

        lines.append("")

    # 触发指标详解
    triggered = [ind for ind in all_indicators if ind.triggered]
    if triggered:
        lines.append("## ⚠️ 触发指标详解")
        lines.append("")
        for ind in triggered:
            lines.append(f"### {risk_emoji(ind.risk_level)} {ind.name}")
            lines.append("")
            lines.append(f"- **当前值**：{format_value(ind.current, ind.unit)}")
            lines.append(f"- **周变动**：{format_value(ind.weekly_change, 'bp' if ind.unit == 'bp' else '%') if ind.weekly_change is not None else '无数据'}")
            lines.append(f"- **阈值**：{ind.threshold}")
            lines.append(f"- **风险等级**：{ind.risk_level}")
            lines.append(f"- **数据日期**：{ind.data_date}")
            lines.append(f"- **数据来源**：{ind.source}")
            if ind.note:
                lines.append(f"- **说明**：{ind.note}")
            lines.append("")

    # 传导路径分析
    lines.append("## 传导路径分析")
    lines.append("")
    lines.append(f"**传导状态**：{status_emoji} {transmission_status}")
    lines.append("")

    if triggered_layers:
        path = " → ".join(triggered_layers)
        lines.append(f"**涉及层级**：{path}")
        lines.append("")

        # 详细传导逻辑
        if len(triggered_layers) == 1:
            lines.append("当前仅单层触发，尚未形成跨层传导。需持续观察是否向相邻层级蔓延。")
        elif len(triggered_layers) >= 2:
            lines.append("已出现跨层传导，请密切关注：")
            lines.append("")
            lines.append("1. **L2→L3**：信用利差走阔 → 银行资产质量担忧 → 银行股下跌")
            lines.append("2. **L3→L4**：银行风险 → 资本外流 → 本币贬值/美元走强")
            lines.append("3. **L2→L4**：信用风险 → 避险情绪 → 美元上涨")
            lines.append("")
            if len(triggered_layers) == 3:
                lines.append("🔴 **三层全触发 = 系统性风险**。建议：")
                lines.append("- 降低风险资产敞口")
                lines.append("- 增加美元现金/短债配置")
                lines.append("- 对冲汇率风险")
                lines.append("- 关注央行流动性投放信号")
    else:
        lines.append("各层指标均在正常区间，信用环境稳定。")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"*生成时间：{now.strftime('%Y-%m-%d %H:%M:%S')}*")
    lines.append("*数据说明：FRED数据为官方数据源（延迟1-2工作日），搜索兜底数据仅供参考，非实时行情。*")

    return "\n".join(lines)


# ============================================================
#  告警处理
# ============================================================

def process_alerts(conn, all_indicators: List[IndicatorResult],
                   transmission_status: str, transmission_desc: str) -> List[str]:
    """
    处理告警（去重 + 记录）
    返回新告警消息列表
    """
    new_alerts = []
    today = datetime.now().strftime("%Y-%m-%d")

    for ind in all_indicators:
        if not ind.triggered:
            continue

        alert_id = f"{ind.indicator_id}_{today}"
        if check_alert_exists(conn, alert_id):
            continue  # 今日已告警，去重

        record_alert(
            conn, alert_id, ind.indicator_id, today,
            ind.current or 0, ind.threshold, ind.risk_level, ind.layer
        )

        alert_msg = (
            f"🚨【信用风险告警】{ind.name}\n"
            f"当前值：{format_value(ind.current, ind.unit)}\n"
            f"阈值：{ind.threshold}\n"
            f"风险等级：{risk_emoji(ind.risk_level)} {ind.risk_level}\n"
            f"层级：{ind.layer}\n"
            f"数据日期：{ind.data_date}\n"
            f"数据来源：{ind.source}\n"
            f"说明：{ind.note}"
        )
        new_alerts.append(alert_msg)

    # 传导状态升级告警
    if transmission_status in ("传导中", "系统性风险"):
        trans_alert_id = f"TRANSMISSION_{transmission_status}_{today}"
        if not check_alert_exists(conn, trans_alert_id):
            record_alert(
                conn, trans_alert_id, "TRANSMISSION", today,
                0, transmission_status,
                "严重" if transmission_status == "系统性风险" else "高",
                "MULTI"
            )
            trans_msg = (
                f"⚠️【传导升级告警】{transmission_status}\n"
                f"{transmission_desc}"
            )
            new_alerts.append(trans_msg)

    return new_alerts


# ============================================================
#  主入口
# ============================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description="信用风险传导监控")
    parser.add_argument("mode", nargs="?", default="weekly",
                        choices=["weekly", "alert", "full"],
                        help="weekly=周报模式, alert=仅告警检查, full=周报+告警")
    parser.add_argument("--result-mode", default="auto",
                        choices=["display_only", "notify", "auto"],
                        help="结果输出模式（CodeAct SDK用）")
    args = parser.parse_args()

    conn = init_db()

    # 采集各层指标
    print("[INFO] 采集L2货币信用层数据...", file=sys.stderr)
    l2_indicators = collect_l2_indicators(conn)

    print("[INFO] 采集L3银行层数据...", file=sys.stderr)
    l3_indicators = collect_l3_indicators(conn)

    print("[INFO] 采集L4外汇层数据...", file=sys.stderr)
    l4_indicators = collect_l4_indicators(conn)

    all_indicators = l2_indicators + l3_indicators + l4_indicators

    # 传导判定
    transmission_status, transmission_desc, triggered_layers = determine_transmission(all_indicators)

    # 告警处理
    new_alerts = process_alerts(conn, all_indicators, transmission_status, transmission_desc)

    # 生成报告
    report = generate_report(conn, all_indicators, transmission_status,
                             transmission_desc, triggered_layers)

    # 保存报告
    date_str = datetime.now().strftime("%Y-%m-%d")
    report_path = OUTPUT_DIR / f"{date_str}_信用风险周度监控.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    # 结果输出
    output = {
        "status": transmission_status,
        "description": transmission_desc,
        "triggered_layers": triggered_layers,
        "triggered_count": sum(1 for ind in all_indicators if ind.triggered),
        "new_alerts": new_alerts,
        "report_path": str(report_path),
        "report_date": date_str,
    }

    # CodeAct SDK提交结果
    try:
        from codeact_sdk import submit_result  # type: ignore
        submit_result(output, result_mode=args.result_mode)
    except ImportError:
        # 非CodeAct环境，直接打印
        print(json.dumps(output, ensure_ascii=False, indent=2))

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
