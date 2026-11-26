#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
信用风险三层传导周度监控 — 纯Python零Token版本
================================================
三层链路：L2货币信用链 → L3银行层 → L4外汇层

数据源（全部纯HTTP，零LLM/零Token）：
  FRED公开CSV: SOFR, IORB, IG OAS, HY OAS, DXY(贸易加权), DEXCHUS
  搜索兜底(网页解析): CDS(CoreWeave/Oracle/SoftBank), KBE, GC, USDCNH

告警方式：
  正常周度报告 → 飞书群（AI股票消息）卡片推送
  触发阈值告警 → 邮件告警 + 飞书卡片
  幂等：同一天同一指标只告警一次
"""

import os
import sys
import csv
import io
import json
import time
import sqlite3
import smtplib
import logging
import requests
import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from pathlib import Path

# ========= 路径配置 =========
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "outputs"
CONFIG_DIR = BASE_DIR / "config"
DATA_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
CONFIG_DIR.mkdir(exist_ok=True)

DB_PATH = DATA_DIR / "credit_monitor.db"
CACHE_PATH = DATA_DIR / "fred_cache.json"

# ========= 日志 =========
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)


# ========= FRED 数据采集 =========
FRED_BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv?id="

FRED_SERIES = {
    "SOFR":       "SOFR",          # 担保隔夜融资利率
    "IORB":       "IORB",          # 准备金余额利率
    "IG_OAS":     "BAMLC0A0CM",    # ICE BofA US Corporate OAS
    "HY_OAS":     "BAMLH0A0HYM2",  # ICE BofA US HY OAS
    "DXY":        "DTWEXBGS",      # 广义贸易加权美元指数
    "USDCNY":     "DEXCHUS",       # 美元/人民币
}


def fred_fetch(series_id: str, days: int = 60) -> list:
    """从FRED公开CSV拉取时间序列，返回[(date_str, value_float), ...]新→旧"""
    url = FRED_BASE + series_id
    try:
        r = requests.get(url, timeout=20, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        r.raise_for_status()
        reader = csv.reader(io.StringIO(r.text))
        header = next(reader, None)
        rows = []
        for row in reader:
            if len(row) < 2:
                continue
            d, v = row[0].strip(), row[1].strip()
            if v in ("", ".", "NA", "null", "NaN"):
                continue
            try:
                rows.append((d, float(v)))
            except ValueError:
                continue
        # 确保日期排序（新→旧）
        rows.sort(key=lambda x: x[0], reverse=True)
        return rows[:days]
    except Exception as e:
        log.warning(f"FRED拉取失败 {series_id}: {e}")
        return []


def latest_value(rows: list):
    """返回 (date, value) 或 (None, None)"""
    return rows[0] if rows else (None, None)


def value_n_days_ago(rows: list, days: int):
    """返回n个交易日前的(date, value)；不足返回(None, None)"""
    # 简单按列表索引取第N个有效交易日
    if len(rows) > days:
        return rows[days]
    return (None, None)


# ========= 网页兜底数据采集（零LLM，正则/HTML解析） =========
def search_cds_markit(entity_id: str) -> float:
    """从Markit或公开CDS报价页解析5Y CDS价格（bps）
    兜底：尝试多个公开源，失败返回None"""
    sources = [
        # worldgovernmentbonds CDS页面
        f"https://www.worldgovernmentbonds.com/credit-default-swap/{entity_id}/",
    ]
    # 简化实现：先返回None，由更具体的抓取函数处理
    return None


def fetch_kbe_price() -> dict:
    """获取KBE银行ETF价格与周变动，数据源：Nasdaq官方API"""
    try:
        # Nasdaq历史数据API（公开，无需key）
        from_date = (datetime.date.today() - datetime.timedelta(days=45)).strftime("%Y-%m-%d")
        url = f"https://api.nasdaq.com/api/quote/KBE/historical?assetclass=etf&fromdate={from_date}&limit=30"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        }
        r = requests.get(url, timeout=20, headers=headers)
        r.raise_for_status()
        d = r.json()
        rows = d.get("data", {}).get("tradesTable", {}).get("rows", [])
        if not rows:
            return {"value": None, "date": None, "week_change": None, "source": "无数据(Nasdaq返回空)"}
        # 解析日期格式: 10/16/2026
        pairs = []
        for row in rows:
            try:
                ds = row["date"]  # MM/DD/YYYY
                dt = datetime.datetime.strptime(ds, "%m/%d/%Y").strftime("%Y-%m-%d")
                close_str = row["close"].replace("$", "").replace(",", "")
                close = float(close_str)
                pairs.append((dt, close))
            except (KeyError, ValueError):
                continue
        pairs.sort(key=lambda x: x[0], reverse=True)
        if not pairs:
            return {"value": None, "date": None, "week_change": None, "source": "无数据(解析失败)"}
        cur_date, cur_val = pairs[0]
        # 5个交易日前 ≈ 1周
        if len(pairs) >= 6:
            _, week_ago_val = pairs[5]
            week_pct = (cur_val - week_ago_val) / week_ago_val * 100
        else:
            week_pct = None
        return {
            "value": round(cur_val, 2),
            "date": cur_date,
            "week_change": round(week_pct, 2) if week_pct is not None else None,
            "source": "Nasdaq (KBE ETF)"
        }
    except Exception as e:
        log.warning(f"KBE价格获取失败: {e}")
        return {"value": None, "date": None, "week_change": None, "source": f"无数据({e})"}


def fetch_cds_from_wgb(company_slug: str) -> dict:
    """企业CDS公开报价获取（彭博/路透付费数据，免费源不稳定）
    尝试多个公开源，失败返回None（标无数据）"""
    # 尝试1: cdssheet.com (公开CDS快照)
    sources = [
        f"https://cdssheet.com/cds/{company_slug}",
        f"https://www.creditflux.com/Credit-Default-Swaps/{company_slug}",
    ]
    import re
    for url in sources:
        try:
            r = requests.get(url, timeout=10, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            if r.status_code != 200:
                continue
            text = r.text
            # 查找5年CDS值（bps），常见格式："5Y 1040" / "5-year: 1040 bps"
            patterns = [
                r'5[Yy].*?(\d{2,5})\s*bps',
                r'5[Yy]ear.*?(\d{2,5})',
                r'"5[yY]"\s*:\s*"?(\d{2,5})',
                r'5y.*?(\d{3,5})',
            ]
            for pat in patterns:
                m = re.search(pat, text, re.IGNORECASE | re.DOTALL)
                if m:
                    val = float(m.group(1))
                    if 10 <= val <= 10000:  # 合理范围
                        date_m = re.search(r'as of\s+(\d{4}-\d{2}-\d{2})', text)
                        date_str = date_m.group(1) if date_m else None
                        return {"value": val, "date": date_str, "source": f"公开CDS源 ({company_slug})"}
        except Exception:
            continue
    # 所有源失败
    return {"value": None, "date": None, "source": "无数据(企业CDS为付费数据，免费源不可用)"}


# ========= 数据库（幂等告警） =========
def db_init():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
    CREATE TABLE IF NOT EXISTS alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        alert_date TEXT NOT NULL,
        indicator_key TEXT NOT NULL,
        indicator_name TEXT NOT NULL,
        current_value TEXT,
        threshold TEXT,
        risk_level TEXT,
        layer TEXT,
        triggered_at TEXT NOT NULL,
        UNIQUE(alert_date, indicator_key)
    )
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_date TEXT NOT NULL,
        indicator_key TEXT NOT NULL,
        value TEXT,
        data_date TEXT,
        source TEXT
    )
    """)
    conn.commit()
    return conn


def should_alert(conn, indicator_key: str, alert_date: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM alerts WHERE alert_date=? AND indicator_key=?",
        (alert_date, indicator_key)
    )
    return cur.fetchone() is None


def record_alert(conn, indicator_key, indicator_name, current_value, threshold, risk_level, layer, alert_date):
    try:
        conn.execute(
            """INSERT INTO alerts
               (alert_date, indicator_key, indicator_name, current_value, threshold, risk_level, layer, triggered_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (alert_date, indicator_key, indicator_name, str(current_value), str(threshold),
             risk_level, layer, datetime.datetime.now().isoformat())
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def record_history(conn, indicator_key, value, data_date, source, run_date):
    conn.execute(
        "INSERT INTO history (run_date, indicator_key, value, data_date, source) VALUES (?, ?, ?, ?, ?)",
        (run_date, indicator_key, str(value), data_date, source)
    )
    conn.commit()


# ========= 飞书推送 =========
def load_config():
    """加载配置文件，不存在则创建模板"""
    cfg_path = CONFIG_DIR / "config.json"
    if not cfg_path.exists():
        default = {
            "feishu_webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/6d34534a-af18-48ba-be77-a30c524734a8",
            "email": {
                "smtp_server": "smtp.163.com",
                "smtp_port": 465,
                "sender": "songshang951@163.com",
                "password": "",
                "receiver": "410119229@qq.com"
            },
            "alert_thresholds": {
                "sofr_iorb_bp": 15,
                "ig_4w_bp": 50,
                "ig_1w_bp": 20,
                "hy_4w_bp": 100,
                "coreweave_cds_bp": 1000,
                "oracle_cds_bp": 300,
                "softbank_cds_bp": 400,
                "repo_pressure_bp": 10,
                "kbe_weekly_drop_pct": -7,
                "dxy_weekly_rise_pct": 2,
                "usdcnh_weekly_move_pct": 1.5,
                "usdcnh_key_levels": [7.0, 7.2]
            }
        }
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(default, f, indent=2, ensure_ascii=False)
        log.warning(f"配置文件已创建模板: {cfg_path}，请填写邮箱授权码")
        return default
    with open(cfg_path, "r", encoding="utf-8") as f:
        return json.load(f)


def push_feishu_card(webhook: str, title: str, markdown: str, template: str = "blue") -> bool:
    """推送飞书交互式卡片"""
    if not webhook:
        log.warning("飞书webhook未配置，跳过推送")
        return False
    # 超过2800字截断
    if len(markdown) > 2800:
        markdown = markdown[:2780] + "\n\n...(内容过长已截断)"
    card = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": template,
                "title": {"tag": "plain_text", "content": title}
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": markdown}}
            ]
        }
    }
    try:
        r = requests.post(webhook, json=card, timeout=15)
        d = r.json()
        if d.get("code") == 0 or d.get("StatusCode") == 0 or d.get("status_code") == 0:
            log.info(f"飞书推送成功: {title}")
            return True
        else:
            log.warning(f"飞书推送失败: {d}")
            return False
    except Exception as e:
        log.warning(f"飞书推送异常: {e}")
        return False


# ========= 邮件告警 =========
def send_alert_email(cfg: dict, subject: str, body_html: str) -> bool:
    """发送告警邮件"""
    ec = cfg.get("email", {})
    if not ec.get("password"):
        log.warning("邮件密码未配置，跳过邮件告警")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = Header(ec.get("sender", ""), "utf-8")
        msg["To"] = ec.get("receiver", "")
        msg["Subject"] = Header(subject, "utf-8")
        html_part = MIMEText(body_html, "html", "utf-8")
        msg.attach(html_part)

        server = smtplib.SMTP_SSL(ec.get("smtp_server", "smtp.163.com"),
                                   ec.get("smtp_port", 465),
                                   timeout=30)
        server.login(ec["sender"], ec["password"])
        server.sendmail(ec["sender"], [ec["receiver"]], msg.as_string())
        server.quit()
        log.info(f"告警邮件已发送: {subject}")
        return True
    except Exception as e:
        log.warning(f"邮件发送失败: {e}")
        return False


# ========= 指标计算与判定 =========
def risk_level(triggered: bool, severity: str = "low") -> str:
    if triggered:
        mapping = {"low": "🟡 中", "medium": "🟠 高", "high": "🔴 严重"}
        return mapping.get(severity, "🟡 中")
    return "🟢 低"


def compute_metrics(cfg: dict) -> dict:
    """采集并计算所有指标，返回结构化结果"""
    results = {
        "l2": {}, "l3": {}, "l4": {},
        "triggered": {"l2": [], "l3": [], "l4": []},
        "triggered_count_by_layer": {"l2": 0, "l3": 0, "l4": 0}
    }
    thresholds = cfg["alert_thresholds"]

    # ---- FRED 数据 ----
    log.info("正在获取FRED数据...")
    fred_data = {}
    for key, sid in FRED_SERIES.items():
        rows = fred_fetch(sid, days=60)
        fred_data[key] = rows
        log.info(f"  {key} ({sid}): {len(rows)}条, 最新={latest_value(rows)}")

    # --- L2.1 SOFR-IORB 利差 ---
    sofr_date, sofr_val = latest_value(fred_data.get("SOFR", []))
    iorb_date, iorb_val = latest_value(fred_data.get("IORB", []))
    if sofr_val is not None and iorb_val is not None:
        spread_bp = round((sofr_val - iorb_val) * 100, 1)
        triggered = spread_bp > thresholds["sofr_iorb_bp"]
        sev = "high" if spread_bp > 25 else ("medium" if spread_bp > 15 else "low")
        results["l2"]["sofr_iorb"] = {
            "name": "SOFR - IORB 利差",
            "value": f"{spread_bp}bp",
            "raw_value": spread_bp,
            "week_change": None,
            "month_change": None,
            "threshold": f"> {thresholds['sofr_iorb_bp']}bp",
            "triggered": triggered,
            "risk_level": risk_level(triggered, sev),
            "data_date": sofr_date,
            "source": "FRED (SOFR + IORB)"
        }
        if triggered:
            results["triggered"]["l2"].append("sofr_iorb")
            results["triggered_count_by_layer"]["l2"] += 1
    else:
        results["l2"]["sofr_iorb"] = {
            "name": "SOFR - IORB 利差",
            "value": "无数据",
            "raw_value": None,
            "threshold": f"> {thresholds['sofr_iorb_bp']}bp",
            "triggered": False,
            "risk_level": "⚪ 无数据",
            "data_date": None,
            "source": "无数据"
        }

    # --- L2.2 IG OAS ---
    ig_rows = fred_data.get("IG_OAS", [])
    if ig_rows:
        ig_date, ig_val = ig_rows[0]
        # 5个交易日 ≈ 1周，20个交易日 ≈ 4周
        ig_1w_val = ig_rows[5][1] if len(ig_rows) > 5 else None
        ig_4w_val = ig_rows[20][1] if len(ig_rows) > 20 else None
        ig_1w_chg = round((ig_val - ig_1w_val) * 100, 1) if ig_1w_val else None   # bp
        ig_4w_chg = round((ig_val - ig_4w_val) * 100, 1) if ig_4w_val else None   # bp
        triggered_1w = ig_1w_chg is not None and ig_1w_chg > thresholds["ig_1w_bp"]
        triggered_4w = ig_4w_chg is not None and ig_4w_chg > thresholds["ig_4w_bp"]
        triggered = triggered_1w or triggered_4w
        sev = "high" if triggered_4w else ("medium" if triggered_1w else "low")
        results["l2"]["ig_oas"] = {
            "name": "投资级(IG) OAS利差",
            "value": f"{ig_val:.2f}%",
            "raw_value": ig_val,
            "week_change": f"{ig_1w_chg:+}bp" if ig_1w_chg is not None else "无数据",
            "month_change": f"{ig_4w_chg:+}bp" if ig_4w_chg is not None else "无数据",
            "raw_week_change_bp": ig_1w_chg,
            "raw_month_change_bp": ig_4w_chg,
            "threshold": f"4周>{thresholds['ig_4w_bp']}bp / 单周>{thresholds['ig_1w_bp']}bp",
            "triggered": triggered,
            "risk_level": risk_level(triggered, sev),
            "data_date": ig_date,
            "source": "FRED (ICE BofA US Corporate)"
        }
        if triggered:
            results["triggered"]["l2"].append("ig_oas")
            results["triggered_count_by_layer"]["l2"] += 1
    else:
        results["l2"]["ig_oas"] = {
            "name": "投资级(IG) OAS利差",
            "value": "无数据", "raw_value": None,
            "threshold": f"4周>{thresholds['ig_4w_bp']}bp / 单周>{thresholds['ig_1w_bp']}bp",
            "triggered": False, "risk_level": "⚪ 无数据",
            "data_date": None, "source": "无数据"
        }

    # --- L2.3 HY OAS ---
    hy_rows = fred_data.get("HY_OAS", [])
    if hy_rows:
        hy_date, hy_val = hy_rows[0]
        hy_1w_val = hy_rows[5][1] if len(hy_rows) > 5 else None
        hy_4w_val = hy_rows[20][1] if len(hy_rows) > 20 else None
        hy_1w_chg = round((hy_val - hy_1w_val) * 100, 1) if hy_1w_val else None
        hy_4w_chg = round((hy_val - hy_4w_val) * 100, 1) if hy_4w_val else None
        triggered = hy_4w_chg is not None and hy_4w_chg > thresholds["hy_4w_bp"]
        # 风险等级即使未触发也评估
        if triggered:
            sev = "high"
        elif hy_4w_chg is not None and hy_4w_chg > thresholds["hy_4w_bp"] * 0.5:
            sev = "medium"
        else:
            sev = "low"
        # 未触发但接近警戒时显示风险等级偏高
        if not triggered and hy_4w_chg is not None and hy_4w_chg > 50:
            risk_str = "🟠 高"
        elif not triggered and hy_4w_chg is not None and hy_4w_chg > 30:
            risk_str = "🟡 中"
        else:
            risk_str = risk_level(triggered, sev)
        results["l2"]["hy_oas"] = {
            "name": "高收益(HY) OAS利差",
            "value": f"{hy_val:.2f}%",
            "raw_value": hy_val,
            "week_change": f"{hy_1w_chg:+}bp" if hy_1w_chg is not None else "无数据",
            "month_change": f"{hy_4w_chg:+}bp" if hy_4w_chg is not None else "无数据",
            "raw_week_change_bp": hy_1w_chg,
            "raw_month_change_bp": hy_4w_chg,
            "threshold": f"4周变动 > {thresholds['hy_4w_bp']}bp",
            "triggered": triggered,
            "risk_level": risk_str,
            "data_date": hy_date,
            "source": "FRED (ICE BofA US HY)"
        }
        if triggered:
            results["triggered"]["l2"].append("hy_oas")
            results["triggered_count_by_layer"]["l2"] += 1
    else:
        results["l2"]["hy_oas"] = {
            "name": "高收益(HY) OAS利差",
            "value": "无数据", "raw_value": None,
            "threshold": f"4周变动 > {thresholds['hy_4w_bp']}bp",
            "triggered": False, "risk_level": "⚪ 无数据",
            "data_date": None, "source": "无数据"
        }

    # --- L2.4 CDS (CoreWeave / Oracle / SoftBank) ---
    log.info("正在获取CDS数据...")
    cds_targets = [
        ("coreweave_cds", "CoreWeave 5Y CDS", "coreweave", thresholds["coreweave_cds_bp"], "high"),
        ("oracle_cds", "Oracle 5Y CDS", "oracle", thresholds["oracle_cds_bp"], "medium"),
        ("softbank_cds", "SoftBank 5Y CDS（观察项）", "softbank-group", thresholds["softbank_cds_bp"], "medium"),
    ]
    for key, name, slug, thresh, sev in cds_targets:
        cds_data = fetch_cds_from_wgb(slug)
        val = cds_data["value"]
        triggered = val is not None and val > thresh
        results["l2"][key] = {
            "name": name,
            "value": f"{val:.0f}bp" if val is not None else "无数据",
            "raw_value": val,
            "week_change": "无数据",
            "month_change": "无数据",
            "threshold": f"> {thresh}bp",
            "triggered": triggered,
            "risk_level": risk_level(triggered, sev) if val is not None else "⚪ 无数据",
            "data_date": cds_data.get("date"),
            "source": cds_data.get("source", "无数据")
        }
        if triggered:
            results["triggered"]["l2"].append(key)
            results["triggered_count_by_layer"]["l2"] += 1

    # --- L2.5 回购压力 (SOFR-GC) ---
    # GC = SOFR的担保部分，这里简化：FRED有TGCR（三方一般担保品利率）
    # 先尝试拿TGCR，如果没有则估算
    tgcr_rows = fred_fetch("TGCR", days=30)
    if tgcr_rows and sofr_val is not None:
        gc_date, gc_val = tgcr_rows[0]
        repo_spread = round((sofr_val - gc_val) * 100, 1)
        triggered = repo_spread > thresholds["repo_pressure_bp"]
        results["l2"]["repo_pressure"] = {
            "name": "回购压力(SOFR-GC利差)",
            "value": f"{repo_spread}bp",
            "raw_value": repo_spread,
            "week_change": None,
            "threshold": f"走阔 > {thresholds['repo_pressure_bp']}bp",
            "triggered": triggered,
            "risk_level": risk_level(triggered, "medium"),
            "data_date": gc_date,
            "source": "FRED (SOFR + TGCR)"
        }
        if triggered:
            results["triggered"]["l2"].append("repo_pressure")
            results["triggered_count_by_layer"]["l2"] += 1
    else:
        # 兜底：用SOFR-IORB间接观察回购压力
        results["l2"]["repo_pressure"] = {
            "name": "回购压力(SOFR-GC利差)",
            "value": "无数据",
            "raw_value": None,
            "threshold": f"走阔 > {thresholds['repo_pressure_bp']}bp",
            "triggered": False,
            "risk_level": "⚪ 无数据",
            "data_date": None,
            "source": "无数据(TGCR不可用)"
        }

    # === L3 银行层 ===
    log.info("正在获取银行层数据...")
    kbe_data = fetch_kbe_price()
    kbe_val = kbe_data["value"]
    kbe_wchg = kbe_data.get("week_change")
    kbe_triggered = kbe_wchg is not None and kbe_wchg < thresholds["kbe_weekly_drop_pct"]
    results["l3"]["kbe"] = {
        "name": "银行股ETF(KBE)",
        "value": f"${kbe_val}" if kbe_val else "无数据",
        "raw_value": kbe_val,
        "week_change": f"{kbe_wchg:+.2f}%" if kbe_wchg is not None else "无数据",
        "threshold": f"单周跌幅 > {abs(thresholds['kbe_weekly_drop_pct'])}%",
        "triggered": kbe_triggered,
        "risk_level": risk_level(kbe_triggered, "high") if kbe_val else "⚪ 无数据",
        "data_date": kbe_data.get("date"),
        "source": kbe_data.get("source", "无数据")
    }
    if kbe_triggered:
        results["triggered"]["l3"].append("kbe")
        results["triggered_count_by_layer"]["l3"] += 1

    # L3.2 HY-银行股传导确认
    hy_trig = "hy_oas" in results["triggered"]["l2"]
    if hy_trig and kbe_triggered:
        results["l3"]["hy_bank_conduction"] = {
            "name": "HY-银行股传导确认",
            "value": "已确认",
            "threshold": "HY 4周>100bp 且 银行股同步下跌",
            "triggered": True,
            "risk_level": "🔴 严重",
            "data_date": None,
            "source": "合成指标 (L2 HY + L3 KBE)"
        }
        results["triggered"]["l3"].append("hy_bank_conduction")
        results["triggered_count_by_layer"]["l3"] += 1
    else:
        results["l3"]["hy_bank_conduction"] = {
            "name": "HY-银行股传导确认",
            "value": "未确认",
            "threshold": "HY 4周>100bp 且 银行股同步下跌",
            "triggered": False,
            "risk_level": "🟢 低",
            "data_date": None,
            "source": "合成指标 (L2 HY + L3 KBE)"
        }

    # === L4 外汇层 ===
    log.info("正在获取外汇层数据...")
    dxy_rows = fred_data.get("DXY", [])
    if dxy_rows:
        dxy_date, dxy_val = dxy_rows[0]
        dxy_1w = dxy_rows[5][1] if len(dxy_rows) > 5 else None
        dxy_wchg = round((dxy_val - dxy_1w) / dxy_1w * 100, 2) if dxy_1w else None
        dxy_triggered = dxy_wchg is not None and dxy_wchg > thresholds["dxy_weekly_rise_pct"]
        results["l4"]["dxy"] = {
            "name": "美元指数(DXY, 广义贸易加权)",
            "value": f"{dxy_val:.2f}",
            "raw_value": dxy_val,
            "week_change": f"{dxy_wchg:+.2f}%" if dxy_wchg is not None else "无数据",
            "threshold": f"单周涨幅 > {thresholds['dxy_weekly_rise_pct']}%",
            "triggered": dxy_triggered,
            "risk_level": risk_level(dxy_triggered, "medium"),
            "data_date": dxy_date,
            "source": "FRED (DTWEXBGS)"
        }
        if dxy_triggered:
            results["triggered"]["l4"].append("dxy")
            results["triggered_count_by_layer"]["l4"] += 1
    else:
        results["l4"]["dxy"] = {
            "name": "美元指数(DXY)",
            "value": "无数据", "raw_value": None,
            "threshold": f"单周涨幅 > {thresholds['dxy_weekly_rise_pct']}%",
            "triggered": False, "risk_level": "⚪ 无数据",
            "data_date": None, "source": "无数据"
        }

    # L4.2 USDCNH
    cny_rows = fred_data.get("USDCNY", [])
    key_levels = thresholds["usdcnh_key_levels"]
    if cny_rows:
        cny_date, cny_val = cny_rows[0]
        cny_1w = cny_rows[5][1] if len(cny_rows) > 5 else None
        cny_wchg = round(abs(cny_val - cny_1w) / cny_1w * 100, 2) if cny_1w else None
        cny_wchg_signed = round((cny_val - cny_1w) / cny_1w * 100, 2) if cny_1w else None
        # 突破关键位
        broken_levels = [lv for lv in key_levels if (cny_val >= lv and (cny_1w is None or cny_1w < lv))]
        vol_triggered = cny_wchg is not None and cny_wchg > thresholds["usdcnh_weekly_move_pct"]
        key_triggered = len(broken_levels) > 0
        cny_triggered = vol_triggered or key_triggered
        sev = "high" if cny_triggered else "low"
        note = ""
        if key_triggered:
            note = f" 突破 {broken_levels}"
        results["l4"]["usdcnh"] = {
            "name": "美元兑人民币(USDCNY)",
            "value": f"{cny_val:.4f}{note}",
            "raw_value": cny_val,
            "week_change": f"{cny_wchg_signed:+.2f}%" if cny_wchg_signed is not None else "无数据",
            "threshold": f"周波动>{thresholds['usdcnh_weekly_move_pct']}% 或破{key_levels}",
            "triggered": cny_triggered,
            "risk_level": risk_level(cny_triggered, sev),
            "data_date": cny_date,
            "source": "FRED (DEXCHUS, 在岸)"
        }
        if cny_triggered:
            results["triggered"]["l4"].append("usdcnh")
            results["triggered_count_by_layer"]["l4"] += 1
    else:
        results["l4"]["usdcnh"] = {
            "name": "美元兑人民币(USDCNY)",
            "value": "无数据", "raw_value": None,
            "threshold": f"周波动>{thresholds['usdcnh_weekly_move_pct']}% 或破{key_levels}",
            "triggered": False, "risk_level": "⚪ 无数据",
            "data_date": None, "source": "无数据"
        }

    # === 传导状态 ===
    triggered_layers = sum(1 for c in results["triggered_count_by_layer"].values() if c > 0)
    if triggered_layers == 0:
        results["conduction_status"] = "🟢 正常"
        results["conduction_desc"] = "各层指标均在阈值内，信用环境平稳"
    elif triggered_layers == 1:
        layer_names = {"l2": "L2 货币信用链", "l3": "L3 银行层", "l4": "L4 外汇层"}
        t_layer = [k for k, v in results["triggered_count_by_layer"].items() if v > 0][0]
        results["conduction_status"] = "🟡 独立风险"
        results["conduction_desc"] = f"仅{layer_names[t_layer]}触发，尚未形成跨层传导，需持续观察"
    elif triggered_layers == 2:
        results["conduction_status"] = "🟠 传导中"
        results["conduction_desc"] = "两层同时触发，风险正在跨市场传导，密切关注是否蔓延至第三层"
    else:
        results["conduction_status"] = "🔴 系统性风险"
        results["conduction_desc"] = "三层全部触发，信用风险已传导至全市场，建议启动避险预案"
    results["triggered_layers"] = triggered_layers

    return results


# ========= 报告生成 =========
def generate_report(results: dict, run_date: str) -> str:
    """生成Markdown格式周报"""
    lines = []
    lines.append(f"# 信用风险传导周度监控 | {run_date}")
    lines.append("")
    lines.append(f"## 总体状态：{results['conduction_status']}")
    lines.append("")
    lines.append(f"> {results['conduction_desc']}")
    lines.append("")

    layer_names = {"l2": "L2 货币信用链（传导起点）", "l3": "L3 银行层（传导中继）", "l4": "L4 外汇层（传导放大器）"}
    layer_keys = ["l2", "l3", "l4"]

    for lk in layer_keys:
        lines.append(f"## {layer_names[lk]}")
        lines.append("")
        lines.append("| 指标 | 当前值 | 周变动 | 4周变动 | 阈值 | 状态 | 风险等级 | 数据日期 | 来源 |")
        lines.append("| --- | ---: | ---: | ---: | --- | :---: | :---: | --- | --- |")
        for key, m in results[lk].items():
            val = m.get("value", "-")
            wchg = m.get("week_change", "-")
            mchg = m.get("month_change", "-")
            thr = m.get("threshold", "-")
            status = "🔴触发" if m.get("triggered") else "正常"
            risk = m.get("risk_level", "-")
            ddate = m.get("data_date", "-") or "-"
            src = m.get("source", "-")
            if len(src) > 25:
                src = src[:25] + "..."
            lines.append(f"| {m['name']} | {val} | {wchg} | {mchg} | {thr} | {status} | {risk} | {ddate} | {src} |")
        lines.append("")

    # 触发详情
    triggered_items = []
    for lk in layer_keys:
        for key, m in results[lk].items():
            if m.get("triggered"):
                triggered_items.append((lk, key, m))

    if triggered_items:
        lines.append("## ⚠️ 触发指标详解")
        lines.append("")
        for lk, key, m in triggered_items:
            lines.append(f"### {m['risk_level']} {m['name']}")
            lines.append("")
            lines.append(f"- **当前值**：{m['value']}")
            lines.append(f"- **周变动**：{m.get('week_change', '无数据')}")
            lines.append(f"- **阈值**：{m['threshold']}")
            lines.append(f"- **数据日期**：{m.get('data_date', '-')}")
            lines.append(f"- **数据来源**：{m['source']}")
            lines.append("")

    lines.append("## 传导路径分析")
    lines.append("")
    lines.append(f"**传导状态**：{results['conduction_status']}")
    lines.append("")
    lines.append(f"**触发层级数**：{results['triggered_layers']}/3")
    lines.append("")

    layer_short = {"l2": "L2货币信用链", "l3": "L3银行层", "l4": "L4外汇层"}
    triggered_layers_names = [layer_short[k] for k, v in results["triggered_count_by_layer"].items() if v > 0]
    if triggered_layers_names:
        lines.append(f"**涉及层级**：{' → '.join(triggered_layers_names)}")
        lines.append("")
    lines.append(results["conduction_desc"])
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(f"*生成时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
    lines.append("")
    lines.append("*数据说明：FRED数据为官方数据源（延迟1-2工作日），CDS等搜索兜底数据仅供参考，非彭博实时行情。*")

    return "\n".join(lines)


def generate_alert_email(results: dict, run_date: str) -> str:
    """生成告警邮件HTML"""
    items = []
    for lk in ["l2", "l3", "l4"]:
        for key, m in results[lk].items():
            if m.get("triggered"):
                items.append(m)

    html = f"""
    <html><body style="font-family: Arial, sans-serif; padding: 20px;">
    <h2 style="color: #d32f2f;">⚠️ 信用风险预警 | {run_date}</h2>
    <p><strong>总体状态：</strong>{results['conduction_status']}</p>
    <p>{results['conduction_desc']}</p>
    <hr>
    <h3>触发指标（{len(items)}项）</h3>
    <table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; width: 100%;">
    <tr style="background: #f5f5f5;">
    <th>指标</th><th>当前值</th><th>阈值</th><th>风险等级</th><th>数据日期</th>
    </tr>
    """
    for m in items:
        html += f"""<tr>
        <td>{m['name']}</td>
        <td style="color: #d32f2f; font-weight: bold;">{m['value']}</td>
        <td>{m['threshold']}</td>
        <td>{m['risk_level']}</td>
        <td>{m.get('data_date', '-')}</td>
        </tr>"""
    html += f"""
    </table>
    <hr>
    <p><strong>传导判定：</strong>{results['triggered_layers']}层触发</p>
    <p style="color: #666; font-size: 12px;">
    数据来源：FRED官方数据 + 公开市场报价<br>
    本邮件由信用利差监控系统自动发送，幂等保护：同一天同一指标仅告警一次
    </p>
    </body></html>
    """
    return html


# ========= 主流程 =========
def main():
    import argparse
    parser = argparse.ArgumentParser(description="信用风险三层传导监控")
    parser.add_argument("mode", nargs="?", default="full",
                       choices=["full", "weekly", "alert"],
                       help="运行模式：full=采集+周报+告警/ weekly=仅周报/ alert=仅检查告警")
    parser.add_argument("--no-feishu", action="store_true", help="跳过飞书推送")
    parser.add_argument("--no-email", action="store_true", help="跳过邮件告警")
    parser.add_argument("--force-alert", action="store_true", help="强制发送告警（忽略幂等）")
    args = parser.parse_args()

    today = datetime.date.today().isoformat()
    log.info(f"=== 信用风险监控启动 | 模式: {args.mode} | 日期: {today} ===")

    # 加载配置
    cfg = load_config()

    # 初始化数据库
    conn = db_init()

    # 采集计算
    results = compute_metrics(cfg)

    # 记录历史
    for lk in ["l2", "l3", "l4"]:
        for key, m in results[lk].items():
            record_history(conn, key, m.get("value"), m.get("data_date"), m.get("source"), today)

    # 生成报告
    report_md = generate_report(results, today)

    # 保存报告
    report_path = OUTPUT_DIR / f"{today}_信用风险周度监控.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_md)
    log.info(f"报告已保存: {report_path}")

    # 飞书推送（周报）
    if not args.no_feishu and args.mode in ("full", "weekly"):
        # 卡片标题
        total = results["triggered_layers"]
        if total == 0:
            emoji = "🟢"
            template = "green"
        elif total == 1:
            emoji = "🟡"
            template = "yellow"
        elif total == 2:
            emoji = "🟠"
            template = "orange"
        else:
            emoji = "🔴"
            template = "red"

        title = f"{emoji} 信用风险监控周报 | {today}"
        # 精简版markdown用于卡片（取前面部分 + 触发详情）
        card_lines = []
        card_lines.append(f"**总体状态：{results['conduction_status']}**")
        card_lines.append("")
        card_lines.append(f"{results['conduction_desc']}")
        card_lines.append("")

        # 核心指标速览
        card_lines.append("**核心指标速览：**")
        card_lines.append("")
        card_lines.append("| 指标 | 当前值 | 状态 |")
        card_lines.append("| --- | ---: | :---: |")
        for lk in ["l2", "l3", "l4"]:
            for key, m in results[lk].items():
                if m.get("triggered"):
                    card_lines.append(f"| {m['name']} | {m['value']} | 🔴触发 |")
                elif key in ("sofr_iorb", "ig_oas", "hy_oas", "coreweave_cds", "kbe", "dxy", "usdcnh"):
                    if not m.get("triggered") and m.get("value") != "无数据":
                        card_lines.append(f"| {m['name']} | {m['value']} | ✅正常 |")
        card_lines.append("")

        # 触发详情
        triggered = []
        for lk in ["l2", "l3", "l4"]:
            for key, m in results[lk].items():
                if m.get("triggered"):
                    triggered.append(m)
        if triggered:
            card_lines.append("**⚠️ 触发详情：**")
            card_lines.append("")
            for m in triggered:
                card_lines.append(f"- **{m['name']}**：{m['value']} (阈值 {m['threshold']})")
            card_lines.append("")

        card_md = "\n".join(card_lines)
        push_feishu_card(cfg.get("feishu_webhook", ""), title, card_md, template=template)

    # 告警（邮件 + 额外飞书卡片）
    if not args.no_email and args.mode in ("full", "alert"):
        # 收集新触发的指标（幂等）
        new_alerts = []
        for lk in ["l2", "l3", "l4"]:
            for key, m in results[lk].items():
                if m.get("triggered"):
                    if args.force_alert or should_alert(conn, key, today):
                        new_alerts.append((lk, key, m))

        if new_alerts:
            log.info(f"发现 {len(new_alerts)} 个新告警，发送邮件...")

            # 记录幂等
            for lk, key, m in new_alerts:
                record_alert(conn, key, m["name"], m["value"],
                           m["threshold"], m["risk_level"], lk, today)

            # 邮件
            email_html = generate_alert_email(results, today)
            subject = f"⚠️ 信用风险预警 - {results['conduction_status']} - {today}"
            email_ok = send_alert_email(cfg, subject, email_html)

            # 飞书额外告警卡片
            if not args.no_feishu:
                alert_lines = [
                    f"**🔴 信用风险告警**",
                    "",
                    f"**状态：{results['conduction_status']}**",
                    "",
                    f"共触发 **{len(new_alerts)}** 项指标：",
                    "",
                ]
                for lk, key, m in new_alerts:
                    alert_lines.append(f"- **{m['name']}**：{m['value']}")
                    alert_lines.append(f"  阈值：{m['threshold']} | 风险：{m['risk_level']}")
                alert_lines.append("")
                alert_lines.append("详细报告见周报附件。")
                push_feishu_card(
                    cfg.get("feishu_webhook", ""),
                    f"🔴 信用风险告警 | {today}",
                    "\n".join(alert_lines),
                    template="red"
                )
        else:
            log.info("无新触发告警（或均已告警过）")

    conn.close()
    log.info("=== 监控完成 ===")


if __name__ == "__main__":
    main()
