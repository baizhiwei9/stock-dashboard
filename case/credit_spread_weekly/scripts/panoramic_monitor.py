#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
周度流动性与信用风险全景监控 — 纯Python零Token版本
====================================================
三合一合并版：美元流动性 + 中国固收流动性 + 信用利差三层传导

三大模块：
  模块A - 美元流动性（宏观流动性周报搬过来）
    WALCL/准备金/RRP/SOFR-IORB/MOVE/TGA/净流动性
  模块B - 中国固收流动性（固收监控搬过来）
    OMO/MLF/存单利率/DR001/DR007/五阶段判定
  模块C - 信用利差三层传导
    L2货币信用链 / L3银行层 / L4外汇层
  模块D - 综合矩阵判定
    流动性状态 × 信用风险状态 = 综合评级

数据源：
  FRED公开CSV（零token）、Nasdaq API、中国货币网/央行公开数据
告警：飞书卡片推送 + 告警邮件（幂等去重）
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

def _hub_lib_dir(_f=__file__):
    _d = os.path.dirname(os.path.abspath(_f))
    for _ in range(6):
        _lib = os.path.join(_d, "_lib")
        if os.path.isfile(os.path.join(_lib, "hub_root.py")):
            return _lib
        _d = os.path.dirname(_d)
    return ""

_HUB_LIB_DIR = _hub_lib_dir()
if _HUB_LIB_DIR and _HUB_LIB_DIR not in sys.path:
    sys.path.insert(0, _HUB_LIB_DIR)
from hub_root import (  # noqa: E402
    feishu_webhook as _feishu_webhook,
    smtp_password as _smtp_password,
    tushare_token as _tushare_token,
)

# ========= 路径配置 =========
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "outputs"
CONFIG_DIR = BASE_DIR / "config"
DATA_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
CONFIG_DIR.mkdir(exist_ok=True)

DB_PATH = DATA_DIR / "monitor.db"

# ========= 日志 =========
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)


# ========= FRED 数据采集 =========
FRED_BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv?id="

# 所有FRED系列
FRED_SERIES = {
    # 美元流动性
    "WALCL":      "WALCL",         # 美联储总资产（百万美元）
    "RESBALNS":   "WRESBAL",       # 存款机构准备金余额（百万美元，周频）
    "WTREGEN":    "WTREGEN",       # 准备金+TGA合计（留作参考）
    "RRPONTSYD":  "RRPONTSYD",     # 隔夜逆回购（百万美元）
    "SOFR":       "SOFR",          # 担保隔夜融资利率
    "IORB":       "IORB",          # 准备金余额利率
    "DGS10":      "DGS10",         # 10年期美债收益率
    "DGS2":       "DGS2",          # 2年期美债收益率
    # 信用利差
    "IG_OAS":     "BAMLC0A0CM",    # 投资级 OAS
    "HY_OAS":     "BAMLH0A0HYM2",  # 高收益 OAS
    "CCC_OAS":    "BAMLH0A3HYC",   # CCC级及以下高收益债 OAS
    # 外汇
    "DXY":        "DTWEXBGS",      # 广义贸易加权美元指数
    "USDCNY":     "DEXCHUS",       # 美元/人民币
    "USDJPY":     "DEXJPUS",       # 美元/日元
}


def fred_fetch(series_id: str, days: int = 120) -> list:
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
        rows.sort(key=lambda x: x[0], reverse=True)
        return rows[:days]
    except Exception as e:
        log.warning(f"FRED拉取失败 {series_id}: {e}")
        return []


def latest(rows: list):
    return rows[0] if rows else (None, None)


def nth(rows: list, n: int):
    """第n个有效数据点（0为最新）"""
    return rows[n] if len(rows) > n else (None, None)


# ========= Nasdaq ETF 数据 =========
def fetch_etf_nasdaq(symbol: str, days: int = 30) -> list:
    """从Nasdaq官方API获取ETF历史数据，返回[(date, close), ...]新→旧"""
    try:
        from_date = (datetime.date.today() - datetime.timedelta(days=days+20)).strftime("%Y-%m-%d")
        url = f"https://api.nasdaq.com/api/quote/{symbol}/historical?assetclass=etf&fromdate={from_date}&limit={days}"
        r = requests.get(url, timeout=20, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        })
        r.raise_for_status()
        d = r.json()
        rows = d.get("data", {}).get("tradesTable", {}).get("rows", [])
        pairs = []
        for row in rows:
            try:
                ds = row["date"]
                dt = datetime.datetime.strptime(ds, "%m/%d/%Y").strftime("%Y-%m-%d")
                close = float(row["close"].replace("$", "").replace(",", ""))
                pairs.append((dt, close))
            except (KeyError, ValueError):
                continue
        pairs.sort(key=lambda x: x[0], reverse=True)
        return pairs[:days]
    except Exception as e:
        log.warning(f"Nasdaq {symbol} 获取失败: {e}")
        return []


# ========= 中国货币市场数据（多源爬虫兜底） =========
def fetch_china_shibor_akshare() -> dict:
    """从AKShare Skill CLI获取SHIBOR历史数据（最稳的源）
    返回 {tenor: [(date, value), ...]} 新→旧"""
    try:
        import subprocess
        skill_dir = BASE_DIR / ".skills" / "skill_akshare-macro-fixed-income-forex"
        cli = skill_dir / "bin" / "_cli_wrapper.py"
        if not cli.exists():
            return {}
        result = subprocess.run(
            ["python3", str(cli), "call", "macro-china-shibor"],
            capture_output=True, text=True, timeout=30,
            cwd=str(skill_dir)
        )
        if result.returncode != 0:
            log.warning(f"AKShare SHIBOR调用失败: {result.stderr[:200]}")
            return {}
        import json
        data = json.loads(result.stdout)
        values = data.get("values", {})
        products = data.get("products", [])
        out = {}
        for prod in products:
            series = []
            for date_str, val_map in sorted(values.items(), reverse=True):
                if prod in val_map:
                    try:
                        v = float(val_map[prod][0])
                        series.append((date_str, v))
                    except (ValueError, IndexError):
                        continue
            if series:
                out[prod] = series
        log.info(f"AKShare SHIBOR获取成功: {list(out.keys())}")
        return out
    except Exception as e:
        log.warning(f"AKShare SHIBOR异常: {e}")
        return {}


def fetch_china_lpr_akshare() -> list:
    """从AKShare Skill获取LPR历史数据
    返回 [{date, lpr1y, lpr5y}, ...] 新→旧"""
    try:
        import subprocess
        skill_dir = BASE_DIR / ".skills" / "skill_akshare-macro-fixed-income-forex"
        cli = skill_dir / "bin" / "_cli_wrapper.py"
        if not cli.exists():
            return []
        result = subprocess.run(
            ["python3", str(cli), "call", "macro-china-lpr"],
            capture_output=True, text=True, timeout=30,
            cwd=str(skill_dir)
        )
        if result.returncode != 0:
            return []
        import json
        raw = json.loads(result.stdout)
        # 格式: {"result": {"data": [{"TRADE_DATE": "...", "LPR1Y": 3.0, "LPR5Y": 3.5, ...}, ...]}}
        rows = []
        data_list = []
        if isinstance(raw, dict):
            if "result" in raw and isinstance(raw["result"], dict):
                data_list = raw["result"].get("data", [])
            elif "data" in raw:
                data_list = raw["data"]
        elif isinstance(raw, list):
            data_list = raw

        for item in data_list:
            date_str = item.get("TRADE_DATE", item.get("日期", ""))
            if " " in date_str:
                date_str = date_str.split(" ")[0]
            rows.append({
                "date": date_str,
                "lpr1y": item.get("LPR1Y", item.get("1年期LPR")),
                "lpr5y": item.get("LPR5Y", item.get("5年期LPR")),
            })
        log.info(f"AKShare LPR获取成功: {len(rows)}条")
        return rows
    except Exception as e:
        log.warning(f"AKShare LPR异常: {e}")
        return []


def fetch_china_rates_web() -> dict:
    """网页爬虫兜底：央行/中债/中国货币网
    返回 {"dr001": (date, val), "dr007": (date, val), "cd_1y": ..., ...}"""
    result = {}

    # 源1：中债收益率曲线 - 解析主页表格
    try:
        r = requests.get(
            "https://yield.chinabond.com.cn/cbweb-mn/yield_main?locale=zh_CN",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            timeout=15
        )
        if r.status_code == 200:
            r.encoding = "utf-8"
            text = r.text
            import re
            # 找收益率数据的模式
            # 中债页面有表格，用通用方式提取数字
            # 先提取日期
            date_match = re.search(r'(\d{4}-\d{2}-\d{2})', text)
            data_date = date_match.group(1) if date_match else None

            # 尝试找包含"国债"、"存单"等关键词的段落
            # 简化：只标记是否可访问，具体数据后续用接口
            log.info(f"中债收益率页面可访问 (len={len(text)})")
    except Exception as e:
        log.debug(f"中债收益率页面访问失败: {e}")

    return result


def compute_quantile(value: float, series: list) -> float:
    """计算value在历史序列中的分位数（0-100），series为新→旧"""
    if not series or value is None:
        return None
    vals = [v for _, v in series if v is not None]
    if not vals:
        return None
    below = sum(1 for v in vals if v < value)
    return round(below / len(vals) * 100, 1)


def fetch_china_rates() -> dict:
    """获取中国货币市场利率全集
    返回结构：每个指标 {value, date, source, series(历史120天), quantile}"""
    result = {
        "shibor_on":   {"value": None, "date": None, "source": "无数据", "series": [], "quantile": None},
        "shibor_1w":   {"value": None, "date": None, "source": "无数据", "series": [], "quantile": None},
        "shibor_1m":   {"value": None, "date": None, "source": "无数据", "series": [], "quantile": None},
        "shibor_3m":   {"value": None, "date": None, "source": "无数据", "series": [], "quantile": None},
        "shibor_1y":   {"value": None, "date": None, "source": "无数据", "series": [], "quantile": None},
        "lpr_1y":      {"value": None, "date": None, "source": "无数据", "series": [], "quantile": None},
        "lpr_5y":      {"value": None, "date": None, "source": "无数据", "series": [], "quantile": None},
        "dr001":       {"value": None, "date": None, "source": "无数据", "series": [], "quantile": None},
        "dr007":       {"value": None, "date": None, "source": "无数据", "series": [], "quantile": None},
        "r001":        {"value": None, "date": None, "source": "无数据", "series": [], "quantile": None},
        "r007":        {"value": None, "date": None, "source": "无数据", "series": [], "quantile": None},
        "cd_1y_aaa":   {"value": None, "date": None, "source": "无数据", "series": [], "quantile": None},
        "mlf_1y":      {"value": None, "date": None, "source": "无数据", "series": [], "quantile": None},
        "omo_7d":      {"value": None, "date": None, "source": "无数据", "series": [], "quantile": None},
    }

    # ---- 源1：AKShare Skill - SHIBOR ----
    shibor_data = fetch_china_shibor_akshare()
    tenor_map = {
        "O/N": "shibor_on", "1W": "shibor_1w", "1M": "shibor_1m",
        "3M": "shibor_3m", "1Y": "shibor_1y"
    }
    for tenor, key in tenor_map.items():
        if tenor in shibor_data and shibor_data[tenor]:
            series = shibor_data[tenor][:120]
            latest_val = series[0][1]
            latest_date = series[0][0]
            result[key]["value"] = latest_val
            result[key]["date"] = latest_date
            result[key]["series"] = series
            result[key]["source"] = "Shibor.org (AKShare)"
            result[key]["quantile"] = compute_quantile(latest_val, series)
            log.info(f"  {key}: {latest_val}% ({latest_date})")

    # ---- 源2：AKShare Skill - LPR ----
    lpr_data = fetch_china_lpr_akshare()
    if lpr_data and lpr_data[0]:
        latest_lpr = lpr_data[0]
        if latest_lpr.get("lpr1y") is not None:
            result["lpr_1y"]["value"] = float(latest_lpr["lpr1y"])
            result["lpr_1y"]["date"] = latest_lpr.get("date", "")
            result["lpr_1y"]["source"] = "央行LPR (AKShare)"
            log.info(f"  lpr_1y: {latest_lpr['lpr1y']}% ({latest_lpr.get('date','')})")
        if latest_lpr.get("lpr5y") is not None:
            result["lpr_5y"]["value"] = float(latest_lpr["lpr5y"])
            result["lpr_5y"]["date"] = latest_lpr.get("date", "")
            result["lpr_5y"]["source"] = "央行LPR (AKShare)"
            log.info(f"  lpr_5y: {latest_lpr['lpr5y']}%")

    # ---- 源3：Tushare - DR/R回购利率（代理版） ----
    try:
        import sys
        sys.path.insert(0, str(BASE_DIR / "scripts"))
        from tushare_init import pro
        from datetime import date, timedelta
        end_dt = date.today()
        start_dt = end_dt - timedelta(days=365)  # 1年数据用于分位数
        df = pro.repo_daily(start_date=start_dt.strftime('%Y%m%d'),
                           end_date=end_dt.strftime('%Y%m%d'))
        if df is not None and len(df) > 0:
            # 按品种分组
            repo_map = {
                "DR001": "dr001", "DR007": "dr007",
                "R001": "r001", "R007": "r007",
            }
            for repo_name, key in repo_map.items():
                subset = df[df['repo_maturity'] == repo_name].copy()
                if len(subset) == 0:
                    continue
                subset = subset.sort_values('trade_date', ascending=False)
                # 用weight_rate或avg_rate
                rate_col = 'weight_r' if 'weight_r' in subset.columns and subset['weight_r'].notna().any() else 'avg_rate'
                series = []
                for _, row in subset.iterrows():
                    val = row.get(rate_col)
                    if val is None or (isinstance(val, float) and __import__('math').isnan(val)):
                        continue
                    td = str(row['trade_date'])
                    td_fmt = f"{td[:4]}-{td[4:6]}-{td[6:8]}"
                    series.append((td_fmt, float(val)))
                if series:
                    result[key]["value"] = series[0][1]
                    result[key]["date"] = series[0][0]
                    result[key]["series"] = series[:250]
                    result[key]["source"] = "全国银行间同业拆借中心 (Tushare)"
                    result[key]["quantile"] = compute_quantile(series[0][1], series)
                    log.info(f"  {key}: {series[0][1]:.4f}% ({series[0][0]})")
    except Exception as e:
        log.warning(f"Tushare回购数据获取失败: {e}")

    # ---- 源4：OMO 7天逆回购利率（政策利率，变动极少） ----
    # 央行公告页正文由JS渲染，直接抓取不稳定。OMO 7天利率作为政策利率调整频率低，
    # 这里使用最新已知值，并标注日期。如遇央行调准需手动更新。
    if not result["omo_7d"]["value"]:
        result["omo_7d"]["value"] = 1.40
        result["omo_7d"]["date"] = "2024-09-17"
        result["omo_7d"]["source"] = "央行7天逆回购（政策利率）"
        result["omo_7d"]["note"] = "政策利率，变动极少，遇调准需更新"
        log.info(f"  omo_7d: {result['omo_7d']['value']}% (政策利率)")

    # ---- 源5：MLF 1年期利率（政策利率，变动极少） ----
    if not result["mlf_1y"]["value"]:
        result["mlf_1y"]["value"] = 2.50
        result["mlf_1y"]["date"] = "2024-09-17"
        result["mlf_1y"]["source"] = "央行1年期MLF（政策利率）"
        result["mlf_1y"]["note"] = "政策利率，变动极少，遇调准需更新"
        log.info(f"  mlf_1y: {result['mlf_1y']['value']}% (政策利率)")

    # ---- 源6：1年AAA同业存单（中债商业银行普通债收益率曲线，带分位数） ----
    try:
        import akshare as ak
        from datetime import date, timedelta
        end_dt = date.today()
        start_dt = end_dt - timedelta(days=350)  # 1年数据（接口限制<1年）
        df = ak.bond_china_yield(start_date=start_dt.strftime('%Y%m%d'),
                                end_date=end_dt.strftime('%Y%m%d'))
        # 筛选商业银行普通债AAA（最接近同业存单AAA的收益率曲线）
        cd_df = df[df['曲线名称'].str.contains('商业银行普通债')].copy()
        if len(cd_df) > 0:
            cd_df = cd_df.sort_values('日期', ascending=False)
            series = []
            for _, row in cd_df.iterrows():
                try:
                    v = float(row['1年'])
                    if v > 0:
                        series.append((str(row['日期']), v))
                except (ValueError, TypeError):
                    continue
            if series:
                result["cd_1y_aaa"]["value"] = series[0][1]
                result["cd_1y_aaa"]["date"] = series[0][0]
                result["cd_1y_aaa"]["series"] = series[:250]
                result["cd_1y_aaa"]["source"] = "中债商业银行普通债(AAA)1Y"
                result["cd_1y_aaa"]["quantile"] = compute_quantile(series[0][1], series)
                result["cd_1y_aaa"]["is_estimate"] = False
                log.info(f"  cd_1y_aaa: {series[0][1]}% (分位{result['cd_1y_aaa']['quantile']})")
    except Exception as e:
        log.warning(f"中债存单收益率获取失败: {e}")

    # ---- 源7：1年AAA同业存单估算兜底（SHIBOR 1Y + 10bp） ----
    if result["shibor_1y"]["value"] and not result["cd_1y_aaa"]["value"]:
        est_val = round(result["shibor_1y"]["value"] + 0.10, 4)
        result["cd_1y_aaa"]["value"] = est_val
        result["cd_1y_aaa"]["date"] = result["shibor_1y"]["date"]
        result["cd_1y_aaa"]["source"] = "估算 (SHIBOR 1Y + 10bp)"
        result["cd_1y_aaa"]["is_estimate"] = True
        log.info(f"  cd_1y_aaa: {est_val:.4f}% (估算)")

    return result


# ========= 数据库（幂等告警） =========
def db_init():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
    CREATE TABLE IF NOT EXISTS alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        alert_date TEXT NOT NULL,
        indicator_key TEXT NOT NULL,
        indicator_name TEXT NOT NULL,
        current_value TEXT,
        threshold TEXT,
        risk_level TEXT,
        module TEXT,
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


def record_alert(conn, indicator_key, indicator_name, current_value, threshold, risk_level, module, alert_date):
    try:
        conn.execute(
            """INSERT INTO alerts
               (alert_date, indicator_key, indicator_name, current_value, threshold, risk_level, module, triggered_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (alert_date, indicator_key, indicator_name, str(current_value), str(threshold),
             risk_level, module, datetime.datetime.now().isoformat())
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


# ========= 配置与推送 =========
def load_config():
    cfg_path = CONFIG_DIR / "config.json"
    if not cfg_path.exists():
        default = {
            "feishu_webhook": _feishu_webhook(),
            "email": {
                "smtp_server": "smtp.163.com",
                "smtp_port": 465,
                "sender": "songshang951@163.com",
                "password": _smtp_password(),
                "receiver": "410119229@qq.com"
            },
            "thresholds": {
                # 美元流动性
                "reserve_trillion_warn": 3.0,
                "reserve_trillion_danger": 2.5,
                "rrp_bln_danger": 100,
                "sofr_iorb_bp_warn": 15,
                "sofr_iorb_bp_danger": 25,
                "move_index_warn": 100,
                "net_liq_down_weeks": 3,
                # 中国固收
                "cd_deviation_bp_warn": 30,
                # 信用利差
                "ig_4w_bp": 50,
                "ig_1w_bp": 20,
                "hy_4w_bp": 100,
                "ccc_4w_bp": 200,
                "kbe_weekly_drop_pct": -7,
                "dxy_weekly_rise_pct": 2,
                "usdcnh_weekly_move_pct": 1.5,
                "usdcnh_key_levels": [7.0, 7.2],
            }
        }
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(default, f, indent=2, ensure_ascii=False)
        log.warning(f"配置文件已创建: {cfg_path}")
        return default
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    # 配置文件里不再放凭据（历史上明文进过 git）。空值从环境变量 / secrets.json 补。
    if not (cfg.get("feishu_webhook") or "").strip():
        cfg["feishu_webhook"] = _feishu_webhook()
    _email = cfg.get("email") or {}
    if isinstance(_email, dict) and not (_email.get("password") or "").strip():
        _email["password"] = _smtp_password()
        cfg["email"] = _email
    return cfg


def push_feishu_card(webhook: str, title: str, markdown: str, template: str = "blue") -> bool:
    if not webhook:
        log.warning("飞书webhook未配置")
        return False
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


def send_alert_email(cfg: dict, subject: str, body_html: str) -> bool:
    ec = cfg.get("email", {})
    if not ec.get("password"):
        log.warning("邮件密码未配置")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = Header(ec.get("sender", ""), "utf-8")
        msg["To"] = ec.get("receiver", "")
        msg["Subject"] = Header(subject, "utf-8")
        msg.attach(MIMEText(body_html, "html", "utf-8"))
        server = smtplib.SMTP_SSL(ec.get("smtp_server", "smtp.163.com"),
                                   ec.get("smtp_port", 465), timeout=30)
        server.login(ec["sender"], ec["password"])
        server.sendmail(ec["sender"], [ec["receiver"]], msg.as_string())
        server.quit()
        log.info(f"告警邮件已发送: {subject}")
        return True
    except Exception as e:
        log.warning(f"邮件发送失败: {e}")
        return False


# ========= 指标计算 =========
def risk_str(triggered: bool, level: str = "low") -> str:
    if not triggered:
        return "🟢 低"
    return {"low": "🟡 中", "medium": "🟠 高", "high": "🔴 严重"}.get(level, "🟡 中")


_RISK_ORDER = {"⚪ 无数据": -1, "🟢 低": 0, "🟡 中": 1, "🟠 高": 2, "🔴 严重": 3}
def _max_risk(*levels: str) -> str:
    """返回多个风险等级中最高的那个"""
    best = "🟢 低"
    for l in levels:
        if _RISK_ORDER.get(l, -1) > _RISK_ORDER.get(best, -1):
            best = l
    return best


def _quantile_of(value: float, series: list) -> float:
    """计算value在series（新→旧）中的历史分位数0-100"""
    if not series or value is None:
        return None
    values = [v for _, v in series if v is not None][:250]
    if len(values) < 20:
        return None
    below = sum(1 for v in values if v < value)
    return round(below / len(values) * 100, 1)


def _level_from_quantile(q: float) -> str:
    """分位数→风险等级：>90严重 / >80高 / >70中 / 其余低"""
    if q is None:
        return "🟢 低"
    if q >= 90:
        return "🔴 严重"
    elif q >= 80:
        return "🟠 高"
    elif q >= 70:
        return "🟡 中"
    return "🟢 低"


def _fetch_move_index() -> tuple:
    """抓取MOVE指数（ICE美债波动率指数），返回(value, date_str, source)或(None, None, None)"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml',
    }
    # 数据源1：CNBC
    try:
        r = requests.get('https://www.cnbc.com/quotes/.MOVE', headers=headers, timeout=15)
        if r.status_code == 200:
            import re
            m = re.search(r'"last":"([\d.]+)"', r.text)
            if m:
                val = float(m.group(1))
                # 日期从页面meta或脚本来，简化用今天
                from datetime import date
                return val, date.today().strftime('%Y-%m-%d'), 'CNBC (ICE MOVE Index)'
    except Exception as e:
        log.warning(f"MOVE CNBC抓取失败: {e}")
    return None, None, None


def compute_usd_liquidity(cfg: dict, fred: dict) -> dict:
    """模块A：美元流动性"""
    # 补全默认阈值（兼容旧配置）
    default_th = {
        "reserve_trillion_warn": 3.0,
        "reserve_trillion_danger": 2.5,
        "rrp_bln_danger": 100,
        "sofr_iorb_bp_warn": 15,
        "sofr_iorb_bp_danger": 25,
        "move_index_warn": 100,
        "net_liq_down_weeks": 3,
        "cd_deviation_bp_warn": 30,
        "ig_4w_bp": 50,
        "ig_1w_bp": 20,
        "hy_4w_bp": 100,
        "kbe_weekly_drop_pct": -7,
        "dxy_weekly_rise_pct": 2,
        "usdcnh_weekly_move_pct": 1.5,
        "usdcnh_key_levels": [7.0, 7.2],
    }
    th = {**default_th, **cfg.get("thresholds", {})}
    out = {"indicators": {}, "alerts": [], "score": 0}

    # 1. 美联储总资产 WALCL
    walcl_rows = fred.get("WALCL", [])
    walcl_date, walcl_val = latest(walcl_rows)
    if walcl_val:
        walcl_trillion = walcl_val / 1000000  # 百万美元 → 万亿美元
        walcl_5d = nth(walcl_rows, 5)
        walcl_wchg = (walcl_val - walcl_5d[1]) / 1000000 if walcl_5d[1] else None
        out["indicators"]["walcl"] = {
            "name": "美联储总资产(WALCL)",
            "value": f"{walcl_trillion:.2f}T",
            "raw_value": walcl_trillion,
            "week_change": f"{walcl_wchg:+.2f}T" if walcl_wchg is not None else "无数据",
            "threshold": "—",
            "triggered": False,
            "risk_level": "🟢 低",
            "data_date": walcl_date,
            "source": "FRED (WALCL, 周三)"
        }
    else:
        out["indicators"]["walcl"] = {"name": "美联储总资产(WALCL)", "value": "无数据",
            "triggered": False, "risk_level": "⚪ 无数据", "data_date": None, "source": "无数据"}

    # 2. 准备金余额（WRESBAL，百万美元，周频周三）
    rsv_rows = fred.get("RESBALNS", [])
    if not rsv_rows:
        # 兜底：用WTREGEN近似（含TGA，会偏高）
        rsv_rows = fred.get("WTREGEN", [])
        src_label = "FRED (WTREGEN, 周三, 含TGA)"
    else:
        src_label = "FRED (WRESBAL, 周三)"
    rsv_date, rsv_val = latest(rsv_rows)
    if rsv_val:
        rsv_trillion = rsv_val / 1000000  # 百万 → 万亿
        triggered_danger = rsv_trillion < th["reserve_trillion_danger"]
        triggered_warn = rsv_trillion < th["reserve_trillion_warn"]
        triggered = triggered_warn or triggered_danger
        level = "high" if triggered_danger else ("medium" if triggered_warn else "low")
        wchg_val = (rsv_val - nth(rsv_rows, 5)[1]) / 1000000 if len(rsv_rows) > 5 and nth(rsv_rows, 5)[1] else None
        out["indicators"]["reserves"] = {
            "name": "银行准备金余额",
            "value": f"{rsv_trillion:.2f}T",
            "raw_value": rsv_trillion,
            "week_change": f"{wchg_val:+.2f}T" if wchg_val is not None else "无数据",
            "threshold": f"警戒线{th['reserve_trillion_warn']}T / 危险线{th['reserve_trillion_danger']}T",
            "triggered": triggered,
            "risk_level": risk_str(triggered, level),
            "data_date": rsv_date,
            "source": src_label
        }
        if triggered:
            out["alerts"].append("reserves")
            out["score"] += 2 if triggered_danger else 1
    else:
        out["indicators"]["reserves"] = {"name": "银行准备金余额", "value": "无数据",
            "triggered": False, "risk_level": "⚪ 无数据", "data_date": None, "source": "无数据"}

    # 3. 隔夜逆回购 RRP
    rrp_rows = fred.get("RRPONTSYD", [])
    rrp_date, rrp_val = latest(rrp_rows)
    if rrp_val:
        rrp_bln = rrp_val / 1000  # 百万 → 十亿
        # RRP低=缓冲耗尽=风险，高=流动性充裕
        triggered = rrp_bln < th["rrp_bln_danger"]
        out["indicators"]["rrp"] = {
            "name": "隔夜逆回购(ON RRP)",
            "value": f"{rrp_bln:.0f}B",
            "raw_value": rrp_bln,
            "week_change": f"{(rrp_val - (nth(rrp_rows,5)[1] or 0))/1000:+.0f}B" if len(rrp_rows) > 5 else "无数据",
            "threshold": f"低于{th['rrp_bln_danger']}B=缓冲耗尽",
            "triggered": triggered,
            "risk_level": risk_str(triggered, "medium"),
            "data_date": rrp_date,
            "source": "FRED (RRPONTSYD)"
        }
        if triggered:
            out["alerts"].append("rrp")
            out["score"] += 1
    else:
        out["indicators"]["rrp"] = {"name": "隔夜逆回购(ON RRP)", "value": "无数据",
            "triggered": False, "risk_level": "⚪ 无数据", "data_date": None, "source": "无数据"}

    # 4. SOFR-IORB 利差
    sofr_rows = fred.get("SOFR", [])
    iorb_rows = fred.get("IORB", [])
    sofr_date, sofr_val = latest(sofr_rows)
    iorb_date, iorb_val = latest(iorb_rows)
    if sofr_val and iorb_val:
        spread_bp = round((sofr_val - iorb_val) * 100, 1)
        triggered_danger = spread_bp > th["sofr_iorb_bp_danger"]
        triggered_warn = spread_bp > th["sofr_iorb_bp_warn"]
        triggered = triggered_warn or triggered_danger
        level = "high" if triggered_danger else ("medium" if triggered_warn else "low")
        out["indicators"]["sofr_iorb"] = {
            "name": "SOFR - IORB 利差",
            "value": f"{spread_bp}bp",
            "raw_value": spread_bp,
            "week_change": None,
            "threshold": f"> {th['sofr_iorb_bp_warn']}bp 预警 / >{th['sofr_iorb_bp_danger']}bp 危险",
            "triggered": triggered,
            "risk_level": risk_str(triggered, level),
            "data_date": sofr_date,
            "source": "FRED (SOFR + IORB)"
        }
        if triggered:
            out["alerts"].append("sofr_iorb")
            out["score"] += 2 if triggered_danger else 1
    else:
        out["indicators"]["sofr_iorb"] = {"name": "SOFR - IORB 利差", "value": "无数据",
            "triggered": False, "risk_level": "⚪ 无数据", "data_date": None, "source": "无数据"}

    # 5. 净流动性 = WALCL - 准备金 - RRP
    # 单位均为百万美元，统一转万亿
    rsv_for_net = rsv_val
    if walcl_val and rsv_for_net and rrp_val:
        net_liq = (walcl_val - rsv_for_net - rrp_val) / 1000000  # 百万 → 万亿
        # 连降3周判定
        weekly_points = []
        min_len = min(len(walcl_rows), len(rsv_rows), len(rrp_rows), 20)
        for i in range(0, min_len, 5):
            if i < len(walcl_rows) and i < len(rsv_rows) and i < len(rrp_rows):
                nl = (walcl_rows[i][1] - rsv_rows[i][1] - rrp_rows[i][1]) / 1000000
                weekly_points.append(nl)
        down_weeks = 0
        for i in range(len(weekly_points) - 1):
            if weekly_points[i] < weekly_points[i + 1]:
                down_weeks += 1
            else:
                break
        net_trend = f"连降{down_weeks}周" if down_weeks > 0 else "未连降"
        triggered = down_weeks >= th["net_liq_down_weeks"]
        out["indicators"]["net_liquidity"] = {
            "name": "美国净流动性",
            "value": f"{net_liq:.2f}T",
            "raw_value": net_liq,
            "week_change": net_trend,
            "threshold": f"连降{th['net_liq_down_weeks']}周=流量拐点",
            "triggered": triggered,
            "risk_level": risk_str(triggered, "medium"),
            "data_date": walcl_date,
            "source": "FRED (WALCL - 准备金 - RRP)"
        }
        if triggered:
            out["alerts"].append("net_liquidity")
            out["score"] += 1
    else:
        out["indicators"]["net_liquidity"] = {"name": "美国净流动性", "value": "无数据",
            "triggered": False, "risk_level": "⚪ 无数据", "data_date": None, "source": "无数据"}

    # 6. MOVE指数（搜索兜底，抓最新值+分位数）
    move_val, move_date, move_source = _fetch_move_index()
    if move_val is not None:
        # MOVE指数历史参考: 正常<80, 关注80-100, 预警100-120, 恐慌>120
        if move_val > 120:
            move_risk = "🔴 严重"
            move_trig = True
        elif move_val > 100:
            move_risk = "🟠 高"
            move_trig = True
        elif move_val > 80:
            move_risk = "🟡 中"
            move_trig = False
        else:
            move_risk = "🟢 低"
            move_trig = False
        out["indicators"]["move_index"] = {
            "name": "MOVE指数（美债波动率）",
            "value": f"{move_val:.1f}",
            "raw_value": move_val,
            "week_change": "—",
            "threshold": ">100预警 / >120恐慌",
            "triggered": move_trig,
            "risk_level": move_risk,
            "data_date": move_date,
            "source": move_source
        }
        if move_trig:
            out["alerts"].append("move_index")
            out["score"] += 1
    else:
        # 兜底：用10Y收益率展示，标注MOVE待接入
        dgs10_rows = fred.get("DGS10", [])
        dgs10_date, dgs10_val = latest(dgs10_rows)
        if dgs10_val:
            dgs10_w = nth(dgs10_rows, 5)
            dgs10_wchg = dgs10_val - dgs10_w[1] if dgs10_w and dgs10_w[1] else None
            out["indicators"]["dgs10"] = {
                "name": "10年期美债收益率",
                "value": f"{dgs10_val:.2f}%",
                "raw_value": dgs10_val,
                "week_change": f"{dgs10_wchg:+.2f}%" if dgs10_wchg is not None else "无数据",
                "threshold": "— (MOVE指数搜索未命中)",
                "triggered": False,
                "risk_level": "🟢 低",
                "data_date": dgs10_date,
                "source": "FRED (DGS10)"
            }
        else:
            out["indicators"]["dgs10"] = {"name": "10年期美债收益率", "value": "无数据",
                "triggered": False, "risk_level": "⚪ 无数据", "data_date": None, "source": "无数据"}

    # 美元流动性总评分 -> 状态标签
    if out["score"] >= 4:
        out["status"] = "🔴 紧张"
    elif out["score"] >= 2:
        out["status"] = "🟡 边际收紧"
    elif out["score"] >= 1:
        out["status"] = "🟢 中性"
    else:
        out["status"] = "🟢 宽松"

    return out


def compute_china_fixed_income(cfg: dict, fred: dict, rates: dict) -> dict:
    """模块B：中国固收流动性（五阶段判定）
    五阶段体系：
      ① 宽松深水区：价格(DR007<OMO) + 量(MLF净投放>0) 双宽
      ② 临界试探：价格在OMO附近震荡 + 量边际收窄
      ③ 供给拐点：DR007>OMO + MLF缩量
      ④ 紧缩确认：价格(DR007-MLF>0) + 量(净回笼) 双紧
      ⑤ 危机模式：R-DR分层走阔 + 信用分层爆发
    """
    default_th = {
        "cd_deviation_bp_warn": 30,
        "dr_omo_bp_tight": 10,   # DR007 - OMO > 10bp = 边际收紧
        "r_dr_widen_bp": 50,     # R007 - DR007 > 50bp = 分层扩大
        "credit_spread_warn": 50, # 信用利差走阔
    }
    th = {**default_th, **cfg.get("thresholds", {})}

    out = {"indicators": {}, "alerts": [], "stage": "⚪ 数据不足", "score": 0}

    # ---- 价格信号 ----
    # DR007 / OMO 7天
    dr007_val = rates.get("dr007", {}).get("value")
    dr007_date = rates.get("dr007", {}).get("date")
    dr007_src = rates.get("dr007", {}).get("source")
    dr007_q = rates.get("dr007", {}).get("quantile")

    omo_val = rates.get("omo_7d", {}).get("value")
    omo_date = rates.get("omo_7d", {}).get("date")
    omo_src = rates.get("omo_7d", {}).get("source")

    # DR001
    dr001_val = rates.get("dr001", {}).get("value")
    dr001_date = rates.get("dr001", {}).get("date")
    dr001_src = rates.get("dr001", {}).get("source")

    # SHIBOR作为价格信号参考
    shibor_on_val = rates.get("shibor_on", {}).get("value")
    shibor_on_q = rates.get("shibor_on", {}).get("quantile")
    shibor_1w_val = rates.get("shibor_1w", {}).get("value")
    shibor_1w_q = rates.get("shibor_1w", {}).get("quantile")
    shibor_1y_val = rates.get("shibor_1y", {}).get("value")
    shibor_1y_q = rates.get("shibor_1y", {}).get("quantile")

    # 价格信号1：DR007（核心价格锚）
    if dr007_val is not None:
        if omo_val:
            spread_bp = round((dr007_val - omo_val) * 100, 1)
            dr_omo_spread = spread_bp
            triggered = spread_bp > th["dr_omo_bp_tight"]
            out["indicators"]["dr007"] = {
                "name": "DR007（核心价格锚）",
                "value": f"{dr007_val:.2f}%",
                "raw_value": dr007_val,
                "week_change": "无数据",
                "vs_omo": f"{spread_bp:+}bp",
                "quantile": f"{dr007_q}%" if dr007_q else "—",
                "threshold": f"高于OMO {th['dr_omo_bp_tight']}bp = 边际收紧",
                "triggered": triggered,
                "risk_level": risk_str(triggered, "medium"),
                "data_date": dr007_date,
                "source": dr007_src
            }
        else:
            out["indicators"]["dr007"] = {
                "name": "DR007（核心价格锚）",
                "value": f"{dr007_val:.2f}%",
                "raw_value": dr007_val,
                "week_change": "无数据",
                "vs_omo": "无数据",
                "quantile": f"{dr007_q}%" if dr007_q else "—",
                "threshold": "—",
                "triggered": False,
                "risk_level": "🟢 低",
                "data_date": dr007_date,
                "source": dr007_src
            }
    else:
        out["indicators"]["dr007"] = {"name": "DR007（核心价格锚）", "value": "无数据",
            "triggered": False, "risk_level": "⚪ 无数据", "data_date": None, "source": "无数据",
            "quantile": "—", "vs_omo": "—"}

    # 价格信号2：DR001
    if dr001_val is not None:
        out["indicators"]["dr001"] = {
            "name": "DR001（隔夜资金面）",
            "value": f"{dr001_val:.2f}%",
            "raw_value": dr001_val,
            "week_change": "无数据",
            "quantile": f"{rates.get('dr001',{}).get('quantile','—')}%",
            "threshold": "—",
            "triggered": False,
            "risk_level": "🟢 低",
            "data_date": dr001_date,
            "source": dr001_src
        }
    else:
        out["indicators"]["dr001"] = {"name": "DR001（隔夜资金面）", "value": "无数据",
            "triggered": False, "risk_level": "⚪ 无数据", "data_date": None, "source": "无数据",
            "quantile": "—"}

    # 价格信号3：SHIBOR O/N（参考）
    if shibor_on_val is not None:
        out["indicators"]["shibor_on"] = {
            "name": "SHIBOR O/N",
            "value": f"{shibor_on_val:.3f}%",
            "raw_value": shibor_on_val,
            "week_change": "无数据",
            "quantile": f"{shibor_on_q}%" if shibor_on_q else "—",
            "threshold": "—",
            "triggered": False,
            "risk_level": "🟢 低",
            "data_date": rates.get("shibor_on", {}).get("date"),
            "source": rates.get("shibor_on", {}).get("source")
        }
    else:
        out["indicators"]["shibor_on"] = {"name": "SHIBOR O/N", "value": "无数据",
            "triggered": False, "risk_level": "⚪ 无数据", "data_date": None, "source": "无数据",
            "quantile": "—"}

    # 价格信号4：SHIBOR 1W
    if shibor_1w_val is not None:
        out["indicators"]["shibor_1w"] = {
            "name": "SHIBOR 1W",
            "value": f"{shibor_1w_val:.3f}%",
            "raw_value": shibor_1w_val,
            "week_change": "无数据",
            "quantile": f"{shibor_1w_q}%" if shibor_1w_q else "—",
            "threshold": "—",
            "triggered": False,
            "risk_level": "🟢 低",
            "data_date": rates.get("shibor_1w", {}).get("date"),
            "source": rates.get("shibor_1w", {}).get("source")
        }

    # 价格信号5：1年期存单（核心利差锚）
    cd_val = rates.get("cd_1y_aaa", {}).get("value")
    cd_q = rates.get("cd_1y_aaa", {}).get("quantile")
    cd_date = rates.get("cd_1y_aaa", {}).get("date")
    cd_src = rates.get("cd_1y_aaa", {}).get("source")
    mlf_val = rates.get("mlf_1y", {}).get("value")
    mlf_src = rates.get("mlf_1y", {}).get("source")

    if cd_val is not None:
        # 计算存单-MLF利差
        cd_mlf_spread = None
        if mlf_val:
            cd_mlf_spread = round((cd_val - mlf_val) * 100, 1)
            triggered = cd_mlf_spread > th["cd_deviation_bp_warn"]
        else:
            triggered = False

        out["indicators"]["cd_1y"] = {
            "name": "1年AAA同业存单",
            "value": f"{cd_val:.3f}%",
            "raw_value": cd_val,
            "week_change": "无数据",
            "vs_mlf": f"{cd_mlf_spread:+}bp" if cd_mlf_spread is not None else "无数据",
            "quantile": f"{cd_q}%" if cd_q else "—",
            "threshold": f"高于MLF {th['cd_deviation_bp_warn']}bp = 供给偏紧" if mlf_val else "—",
            "triggered": triggered,
            "risk_level": risk_str(triggered, "medium"),
            "data_date": cd_date,
            "source": cd_src
        }
        if triggered:
            out["alerts"].append("cd_1y")
            out["score"] += 1
    else:
        out["indicators"]["cd_1y"] = {"name": "1年AAA同业存单", "value": "无数据",
            "triggered": False, "risk_level": "⚪ 无数据", "data_date": None, "source": "无数据",
            "quantile": "—", "vs_mlf": "—"}

    # 政策利率：MLF
    if mlf_val is not None:
        out["indicators"]["mlf_1y"] = {
            "name": "1年期MLF（政策利率锚）",
            "value": f"{mlf_val:.2f}%",
            "raw_value": mlf_val,
            "week_change": "—",
            "threshold": "—",
            "triggered": False,
            "risk_level": "🟢 低",
            "data_date": rates.get("mlf_1y", {}).get("date"),
            "source": mlf_src
        }
    else:
        out["indicators"]["mlf_1y"] = {"name": "1年期MLF（政策利率锚）", "value": "无数据",
            "triggered": False, "risk_level": "⚪ 无数据", "data_date": None, "source": "无数据"}

    # OMO 7天
    if omo_val is not None:
        out["indicators"]["omo_7d"] = {
            "name": "7天OMO逆回购",
            "value": f"{omo_val:.2f}%",
            "raw_value": omo_val,
            "week_change": "—",
            "threshold": "—",
            "triggered": False,
            "risk_level": "🟢 低",
            "data_date": omo_date,
            "source": omo_src
        }
    else:
        out["indicators"]["omo_7d"] = {"name": "7天OMO逆回购", "value": "无数据",
            "triggered": False, "risk_level": "⚪ 无数据", "data_date": None, "source": "无数据"}

    # LPR
    lpr_1y_val = rates.get("lpr_1y", {}).get("value")
    if lpr_1y_val:
        out["indicators"]["lpr_1y"] = {
            "name": "1年期LPR",
            "value": f"{lpr_1y_val:.1f}%",
            "raw_value": lpr_1y_val,
            "week_change": "—",
            "threshold": "—",
            "triggered": False,
            "risk_level": "🟢 低",
            "data_date": rates.get("lpr_1y", {}).get("date"),
            "source": rates.get("lpr_1y", {}).get("source")
        }

    lpr_5y_val = rates.get("lpr_5y", {}).get("value")
    if lpr_5y_val:
        out["indicators"]["lpr_5y"] = {
            "name": "5年期LPR",
            "value": f"{lpr_5y_val:.1f}%",
            "raw_value": lpr_5y_val,
            "week_change": "—",
            "threshold": "—",
            "triggered": False,
            "risk_level": "🟢 低",
            "data_date": rates.get("lpr_5y", {}).get("date"),
            "source": rates.get("lpr_5y", {}).get("source")
        }

    # 利差信号：R-DR分层
    r007_val = rates.get("r007", {}).get("value")
    if r007_val and dr007_val is not None:
        r_dr_spread = round((r007_val - dr007_val) * 100, 1)
        triggered = r_dr_spread > th["r_dr_widen_bp"]
        out["indicators"]["r_dr_spread"] = {
            "name": "R007 - DR007（分层指标）",
            "value": f"{r_dr_spread}bp",
            "raw_value": r_dr_spread,
            "week_change": "无数据",
            "threshold": f">{th['r_dr_widen_bp']}bp = 非银资金紧张",
            "triggered": triggered,
            "risk_level": risk_str(triggered, "high"),
            "data_date": dr007_date,
            "source": "合成 (R007 - DR007)"
        }
        if triggered:
            out["alerts"].append("r_dr_spread")
            out["score"] += 2

    # USDCNY汇率视角
    cny_rows = fred.get("USDCNY", [])
    cny_date, cny_val = latest(cny_rows)
    if cny_val:
        out["indicators"]["usdcny"] = {
            "name": "美元/人民币(在岸)",
            "value": f"{cny_val:.4f}",
            "raw_value": cny_val,
            "week_change": None,
            "threshold": "—",
            "triggered": False,
            "risk_level": "🟢 低",
            "data_date": cny_date,
            "source": "FRED (DEXCHUS)"
        }
    else:
        out["indicators"]["usdcny"] = {"name": "美元/人民币", "value": "无数据",
            "triggered": False, "risk_level": "⚪ 无数据", "data_date": None, "source": "无数据"}

    # ---- 五阶段判定 ----
    price_signals = 0  # 价格端紧的信号数
    vol_signals = 0    # 量端紧的信号数
    credit_signals = 0 # 信用信号数

    # 价格信号判定
    if dr007_val is not None and omo_val and dr007_val > omo_val + th["dr_omo_bp_tight"] / 10000:
        price_signals += 1  # DR007 > OMO
    if cd_val is not None and mlf_val and cd_val > mlf_val + th["cd_deviation_bp_warn"] / 10000:
        price_signals += 1  # 存单 > MLF

    # 信用分层
    if r007_val and dr007_val and (r007_val - dr007_val) * 100 > th["r_dr_widen_bp"]:
        credit_signals += 1

    # 五阶段映射（简化版，量信号待接入后完善）
    if credit_signals >= 1 and price_signals >= 1:
        stage = "⑤ 危机模式"
        stage_color = "🔴"
        out["score"] += 3
    elif price_signals >= 2 and vol_signals >= 1:
        stage = "④ 紧缩确认"
        stage_color = "🟠"
        out["score"] += 2
    elif price_signals >= 1:
        stage = "③ 供给拐点"
        stage_color = "🟡"
        out["score"] += 1
    elif dr001_val is not None and shibor_on_val is not None:
        # 价格低 + 价格信号少 = 宽松或临界
        if price_signals == 0:
            stage = "① 宽松深水区"
            stage_color = "🟢"
        else:
            stage = "② 临界试探"
            stage_color = "🟢"
    else:
        stage = "数据不足"
        stage_color = "⚪"

    out["stage"] = f"{stage_color} {stage}"
    out["status"] = out["stage"]
    return out


def fetch_etf_nasdaq(symbol: str, days: int = 30) -> list:
    """从Nasdaq抓取ETF价格，返回[(date, price), ...]新→旧"""
    from datetime import date, timedelta
    from_date = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    url = f"https://api.nasdaq.com/api/quote/{symbol}/historical?assetclass=etf&fromdate={from_date}&limit={days}"
    try:
        r = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
            "Origin": "https://www.nasdaq.com",
            "Referer": "https://www.nasdaq.com/",
        })
        r.raise_for_status()
        data = r.json()
        rows = data.get("data", {}).get("tradesTable", {}).get("rows", [])
        result = []
        from datetime import datetime
        for row in rows:
            try:
                d = row.get("date", "")
                dt = datetime.strptime(d, "%m/%d/%Y")
                p = float(row.get("close", "0").replace("$", "").replace(",", ""))
                result.append((dt.strftime("%Y-%m-%d"), p))
            except (ValueError, TypeError):
                continue
        return result
    except Exception as e:
        log.warning(f"Nasdaq ETF抓取失败 {symbol}: {e}")
        return []


def compute_credit_spread(cfg: dict, fred: dict, fred_long: dict = None) -> dict:
    """模块C：信用利差三层传导"""
    default_th = {
        "ig_4w_bp": 50, "ig_1w_bp": 20, "hy_4w_bp": 100,
        "ccc_4w_bp": 200, "ccc_abs_level": 15,
        "kbe_weekly_drop_pct": -7, "dxy_weekly_rise_pct": 2,
        "usdcnh_weekly_move_pct": 1.5, "usdcnh_key_levels": [7.0, 7.2],
        "hy_ig_spread_widen_bp": 100,  # HY-IG走阔>100bp=信用分层加剧
        "ccc_ig_spread_widen_bp": 300, # CCC-IG走阔>300bp=尾部风险扩散
    }
    th = {**default_th, **cfg.get("thresholds", {})}
    out = {"l2": {}, "l3": {}, "l4": {}, "alerts": {"l2": [], "l3": [], "l4": []},
           "triggered_layers": 0}

    # ---- L2 货币信用层 ----
    # IG OAS
    ig_rows = fred.get("IG_OAS", [])
    ig_val = None
    if ig_rows:
        ig_date, ig_val = ig_rows[0]
        ig_1w = ig_rows[5][1] if len(ig_rows) > 5 else None
        ig_4w = ig_rows[20][1] if len(ig_rows) > 20 else None
        ig_1w_chg = round((ig_val - ig_1w) * 100, 1) if ig_1w else None
        ig_4w_chg = round((ig_val - ig_4w) * 100, 1) if ig_4w else None
        ig_long = (fred_long or {}).get("IG_OAS", ig_rows)
        ig_q = _quantile_of(ig_val, ig_long)
        trig_1w = ig_1w_chg is not None and ig_1w_chg > th["ig_1w_bp"]
        trig_4w = ig_4w_chg is not None and ig_4w_chg > th["ig_4w_bp"]
        trig = trig_1w or trig_4w
        # 风险等级取变动和分位数的较高者
        chg_level = "high" if trig_4w else ("medium" if trig_1w else "low")
        q_level = _level_from_quantile(ig_q)
        risk = _max_risk(risk_str(trig, chg_level), q_level)
        out["l2"]["ig_oas"] = {
            "name": "投资级(IG) OAS",
            "value": f"{ig_val:.2f}%", "raw_value": ig_val,
            "week_change": f"{ig_1w_chg:+}bp" if ig_1w_chg is not None else "无数据",
            "month_change": f"{ig_4w_chg:+}bp" if ig_4w_chg is not None else "无数据",
            "quantile": f"{ig_q}%" if ig_q is not None else "—",
            "threshold": f"4周>{th['ig_4w_bp']}bp / 单周>{th['ig_1w_bp']}bp / 分位数>80%",
            "triggered": trig or (ig_q is not None and ig_q >= 80),
            "risk_level": risk,
            "data_date": ig_date, "source": "FRED (ICE BofA Corporate)"
        }
        if out["l2"]["ig_oas"]["triggered"]:
            out["alerts"]["l2"].append("ig_oas")
    else:
        out["l2"]["ig_oas"] = {"name": "投资级(IG) OAS", "value": "无数据",
            "triggered": False, "risk_level": "⚪ 无数据", "data_date": None, "source": "无数据", "quantile": "—"}

    # HY OAS
    hy_rows = fred.get("HY_OAS", [])
    hy_val = None
    if hy_rows:
        hy_date, hy_val = hy_rows[0]
        hy_1w = hy_rows[5][1] if len(hy_rows) > 5 else None
        hy_4w = hy_rows[20][1] if len(hy_rows) > 20 else None
        hy_1w_chg = round((hy_val - hy_1w) * 100, 1) if hy_1w else None
        hy_4w_chg = round((hy_val - hy_4w) * 100, 1) if hy_4w else None
        hy_long = (fred_long or {}).get("HY_OAS", hy_rows)
        hy_q = _quantile_of(hy_val, hy_long)
        trig = hy_4w_chg is not None and hy_4w_chg > th["hy_4w_bp"]
        chg_risk = risk_str(trig, "high")
        q_risk = _level_from_quantile(hy_q)
        risk = _max_risk(chg_risk, q_risk)
        q_trig = hy_q is not None and hy_q >= 80
        out["l2"]["hy_oas"] = {
            "name": "高收益(HY) OAS",
            "value": f"{hy_val:.2f}%", "raw_value": hy_val,
            "week_change": f"{hy_1w_chg:+}bp" if hy_1w_chg is not None else "无数据",
            "month_change": f"{hy_4w_chg:+}bp" if hy_4w_chg is not None else "无数据",
            "quantile": f"{hy_q}%" if hy_q is not None else "—",
            "threshold": f"4周>{th['hy_4w_bp']}bp / 分位数>80%",
            "triggered": trig or q_trig, "risk_level": risk,
            "data_date": hy_date, "source": "FRED (ICE BofA HY)"
        }
        if out["l2"]["hy_oas"]["triggered"]:
            out["alerts"]["l2"].append("hy_oas")
    else:
        out["l2"]["hy_oas"] = {"name": "高收益(HY) OAS", "value": "无数据",
            "triggered": False, "risk_level": "⚪ 无数据", "data_date": None, "source": "无数据", "quantile": "—"}

    # CCC级及以下高收益债 OAS（尾部信用风险）
    ccc_rows = fred.get("CCC_OAS", [])
    ccc_val = None
    if ccc_rows:
        ccc_date, ccc_val = ccc_rows[0]
        ccc_1w = ccc_rows[5][1] if len(ccc_rows) > 5 else None
        ccc_4w = ccc_rows[20][1] if len(ccc_rows) > 20 else None
        ccc_1w_chg = round((ccc_val - ccc_1w) * 100, 1) if ccc_1w else None
        ccc_4w_chg = round((ccc_val - ccc_4w) * 100, 1) if ccc_4w else None
        ccc_long = (fred_long or {}).get("CCC_OAS", ccc_rows)
        ccc_q = _quantile_of(ccc_val, ccc_long)
        ccc_4w_th = th.get("ccc_4w_bp", 200)
        ccc_abs_th = th.get("ccc_abs_level", 15)
        trig_4w = ccc_4w_chg is not None and ccc_4w_chg > ccc_4w_th
        trig_abs = ccc_val > ccc_abs_th
        trig = trig_4w or trig_abs
        chg_risk = "🔴 严重" if trig_4w else ("🟡 中" if trig_abs else "🟢 低")
        q_risk = _level_from_quantile(ccc_q)
        risk = _max_risk(chg_risk, q_risk)
        q_trig = ccc_q is not None and ccc_q >= 80
        out["l2"]["ccc_oas"] = {
            "name": "CCC级及以下 OAS",
            "value": f"{ccc_val:.2f}%", "raw_value": ccc_val,
            "week_change": f"{ccc_1w_chg:+}bp" if ccc_1w_chg is not None else "无数据",
            "month_change": f"{ccc_4w_chg:+}bp" if ccc_4w_chg is not None else "无数据",
            "quantile": f"{ccc_q}%" if ccc_q is not None else "—",
            "threshold": f"4周>{ccc_4w_th}bp / 绝对>{ccc_abs_th}% / 分位数>80%",
            "triggered": trig or q_trig, "risk_level": risk,
            "data_date": ccc_date, "source": "FRED (ICE BofA CCC&Lower)"
        }
        if out["l2"]["ccc_oas"]["triggered"]:
            out["alerts"]["l2"].append("ccc_oas")
    else:
        out["l2"]["ccc_oas"] = {"name": "CCC级及以下 OAS", "value": "无数据",
            "triggered": False, "risk_level": "⚪ 无数据", "data_date": None, "source": "无数据", "quantile": "—"}

    # ---- 评级利差（信用分层信号）----
    # HY - IG：信用分层程度
    if ig_val is not None and hy_val is not None:
        hy_ig_sp = round((hy_val - ig_val) * 100, 1)  # bp
        # 用各自历史序列推导利差分位数（简化：直接看绝对水平）
        hy_ig_trig = hy_ig_sp > th["hy_ig_spread_widen_bp"]
        # 参考HY分位数判断分层风险
        hy_q = _quantile_of(hy_val, hy_rows) if hy_rows else None
        spread_risk = "🔴 严重" if hy_ig_trig else ("🟠 高" if hy_q and hy_q >= 80 else "🟡 中" if hy_q and hy_q >= 70 else "🟢 低")
        out["l2"]["hy_ig_spread"] = {
            "name": "HY - IG 评级利差",
            "value": f"{hy_ig_sp}bp", "raw_value": hy_ig_sp,
            "week_change": "—", "month_change": "—", "quantile": "—",
            "threshold": f">{th['hy_ig_spread_widen_bp']}bp = 信用分层加剧",
            "triggered": hy_ig_trig, "risk_level": spread_risk,
            "data_date": ig_date, "source": "合成 (HY OAS - IG OAS)"
        }
        if hy_ig_trig:
            out["alerts"]["l2"].append("hy_ig_spread")

    # CCC - IG：尾部信用扩散
    if ig_val is not None and ccc_val is not None:
        ccc_ig_sp = round((ccc_val - ig_val) * 100, 1)
        ccc_ig_trig = ccc_ig_sp > th["ccc_ig_spread_widen_bp"]
        ccc_q = _quantile_of(ccc_val, ccc_rows) if ccc_rows else None
        spread_risk = "🔴 严重" if ccc_ig_trig else ("🟠 高" if ccc_q and ccc_q >= 80 else "🟡 中" if ccc_q and ccc_q >= 70 else "🟢 低")
        out["l2"]["ccc_ig_spread"] = {
            "name": "CCC - IG 评级利差",
            "value": f"{ccc_ig_sp}bp", "raw_value": ccc_ig_sp,
            "week_change": "—", "month_change": "—", "quantile": "—",
            "threshold": f">{th['ccc_ig_spread_widen_bp']}bp = 尾部风险扩散",
            "triggered": ccc_ig_trig, "risk_level": spread_risk,
            "data_date": ig_date, "source": "合成 (CCC OAS - IG OAS)"
        }
        if ccc_ig_trig:
            out["alerts"]["l2"].append("ccc_ig_spread")

    # SOFR-IORB（从流动性模块共享，这里也展示在L2下保持链路完整）
    # 略（已在模块A）

    # ---- L3 银行层 ----
    kbe_rows = fetch_etf_nasdaq("KBE", days=30)
    if kbe_rows:
        kbe_date, kbe_val = kbe_rows[0]
        kbe_1w = kbe_rows[5][1] if len(kbe_rows) > 5 else None
        kbe_wchg = round((kbe_val - kbe_1w) / kbe_1w * 100, 2) if kbe_1w else None
        trig = kbe_wchg is not None and kbe_wchg < th["kbe_weekly_drop_pct"]
        out["l3"]["kbe"] = {
            "name": "银行股ETF(KBE)",
            "value": f"{kbe_val:.2f}", "raw_value": kbe_val,
            "week_change": f"{kbe_wchg:+.2f}%" if kbe_wchg is not None else "无数据",
            "threshold": f"单周跌>{abs(th['kbe_weekly_drop_pct'])}%",
            "triggered": trig, "risk_level": risk_str(trig, "high"),
            "data_date": kbe_date, "source": "Nasdaq (KBE)"
        }
        if trig:
            out["alerts"]["l3"].append("kbe")
    else:
        out["l3"]["kbe"] = {"name": "银行股ETF(KBE)", "value": "无数据",
            "triggered": False, "risk_level": "⚪ 无数据", "data_date": None, "source": "无数据"}

    # HY + 银行股传导确认
    hy_trig = "hy_oas" in out["alerts"]["l2"]
    kbe_trig = "kbe" in out["alerts"]["l3"]
    if hy_trig and kbe_trig:
        out["l3"]["conduction"] = {"name": "HY-银行股传导确认", "value": "已确认",
            "triggered": True, "risk_level": "🔴 严重", "data_date": None,
            "source": "合成(L2 HY + L3 KBE)"}
        out["alerts"]["l3"].append("conduction")
    else:
        out["l3"]["conduction"] = {"name": "HY-银行股传导确认", "value": "未确认",
            "triggered": False, "risk_level": "🟢 低", "data_date": None,
            "source": "合成(L2 HY + L3 KBE)"}

    # ---- L4 外汇层 ----
    # DXY
    dxy_rows = fred.get("DXY", [])
    if dxy_rows:
        dxy_date, dxy_val = dxy_rows[0]
        dxy_1w = dxy_rows[5][1] if len(dxy_rows) > 5 else None
        dxy_wchg = round((dxy_val - dxy_1w) / dxy_1w * 100, 2) if dxy_1w else None
        trig = dxy_wchg is not None and dxy_wchg > th["dxy_weekly_rise_pct"]
        out["l4"]["dxy"] = {
            "name": "美元指数(广义贸易加权)",
            "value": f"{dxy_val:.2f}", "raw_value": dxy_val,
            "week_change": f"{dxy_wchg:+.2f}%" if dxy_wchg is not None else "无数据",
            "threshold": f"单周涨>{th['dxy_weekly_rise_pct']}%",
            "triggered": trig, "risk_level": risk_str(trig, "medium"),
            "data_date": dxy_date, "source": "FRED (DTWEXBGS)"
        }
        if trig:
            out["alerts"]["l4"].append("dxy")
    else:
        out["l4"]["dxy"] = {"name": "美元指数", "value": "无数据",
            "triggered": False, "risk_level": "⚪ 无数据", "data_date": None, "source": "无数据"}

    # USDJPY
    jpy_rows = fred.get("USDJPY", [])
    if jpy_rows:
        jpy_date, jpy_val = jpy_rows[0]
        jpy_1w = jpy_rows[5][1] if len(jpy_rows) > 5 else None
        jpy_wchg = round((jpy_val - jpy_1w) / jpy_1w * 100, 2) if jpy_1w else None
        # 日元贬值=美元兑日元上涨，160是关键位
        key_level = 160
        near_key = jpy_val > key_level * 0.97
        trig = jpy_val > key_level
        out["l4"]["usdjpy"] = {
            "name": "美元/日元(套息风向标)",
            "value": f"{jpy_val:.2f}", "raw_value": jpy_val,
            "week_change": f"{jpy_wchg:+.2f}%" if jpy_wchg is not None else "无数据",
            "threshold": f"突破{key_level}=套息交易风险",
            "triggered": trig,
            "risk_level": risk_str(trig, "high") if not near_key else "🟡 中",
            "data_date": jpy_date, "source": "FRED (DEXJPUS)"
        }
        if trig:
            out["alerts"]["l4"].append("usdjpy")
    else:
        out["l4"]["usdjpy"] = {"name": "美元/日元", "value": "无数据",
            "triggered": False, "risk_level": "⚪ 无数据", "data_date": None, "source": "无数据"}

    # USDCNY（已在模块B，这里也放一个保持L4完整）
    cny_rows = fred.get("USDCNY", [])
    if cny_rows:
        cny_date, cny_val = cny_rows[0]
        cny_1w = cny_rows[5][1] if len(cny_rows) > 5 else None
        cny_wchg = round(abs(cny_val - cny_1w) / cny_1w * 100, 2) if cny_1w else None
        cny_wchg_signed = round((cny_val - cny_1w) / cny_1w * 100, 2) if cny_1w else None
        key_levels = th["usdcnh_key_levels"]
        vol_trig = cny_wchg is not None and cny_wchg > th["usdcnh_weekly_move_pct"]
        trig = vol_trig
        out["l4"]["usdcny"] = {
            "name": "美元/人民币",
            "value": f"{cny_val:.4f}", "raw_value": cny_val,
            "week_change": f"{cny_wchg_signed:+.2f}%" if cny_wchg_signed is not None else "无数据",
            "threshold": f"周波动>{th['usdcnh_weekly_move_pct']}% / 关键位{key_levels}",
            "triggered": trig, "risk_level": risk_str(trig, "medium"),
            "data_date": cny_date, "source": "FRED (DEXCHUS)"
        }
        if trig:
            out["alerts"]["l4"].append("usdcny")
    else:
        out["l4"]["usdcny"] = {"name": "美元/人民币", "value": "无数据",
            "triggered": False, "risk_level": "⚪ 无数据", "data_date": None, "source": "无数据"}

    # 统计触发层数
    out["triggered_layers"] = sum(1 for v in out["alerts"].values() if v)
    if out["triggered_layers"] == 0:
        out["status"] = "🟢 正常"
    elif out["triggered_layers"] == 1:
        out["status"] = "🟡 独立风险"
    elif out["triggered_layers"] == 2:
        out["status"] = "🟠 传导中"
    else:
        out["status"] = "🔴 系统性风险"

    return out


def compute_overall(usd_liq: dict, china_fi: dict, credit: dict) -> dict:
    """模块D：综合判定矩阵
    流动性状态 × 信用风险状态 = 综合评级"""
    # 简化综合评分
    usd_alerts = len(usd_liq.get("alerts", []))
    credit_layers = credit.get("triggered_layers", 0)
    # 计算总触发指标数
    total_alerts = usd_alerts
    for lk in ["l2", "l3", "l4"]:
        total_alerts += len(credit.get("alerts", {}).get(lk, []))

    total_score = usd_alerts + credit_layers * 2

    if total_score >= 5:
        grade = "🔴 高风险"
        summary = "流动性紧张 + 信用风险传导，建议启动避险预案"
    elif total_score >= 3:
        grade = "🟠 中高风险"
        summary = "流动性边际收紧 + 信用风险抬头，密切关注传导链条"
    elif total_score >= 1:
        grade = "🟡 中性偏紧"
        summary = "局部指标触发，尚未形成系统性风险，持续观察"
    else:
        grade = "🟢 稳健"
        summary = "流动性充裕，信用利差稳定，整体环境良好"

    return {
        "grade": grade,
        "score": total_score,
        "summary": summary,
        "usd_alerts": usd_alerts,
        "credit_layers": credit_layers,
        "total_alerts": total_alerts,
    }


# ========= 报告生成 =========
def generate_report(usd_liq, china_fi, credit, overall, run_date) -> str:
    lines = []
    lines.append(f"# 周度流动性与信用风险全景监控 | {run_date}")
    lines.append("")
    lines.append(f"## 综合评级：{overall['grade']}")
    lines.append("")
    lines.append(f"> {overall['summary']}")
    lines.append("")
    lines.append(f"**流动性告警**：{overall['usd_alerts']}项 | **信用传导层**：{overall['credit_layers']}/3 | **综合得分**：{overall['score']}")
    lines.append("")

    # 模块A：美元流动性
    lines.append("## 一、美元流动性")
    lines.append("")
    lines.append(f"**状态：{usd_liq.get('status', '—')}**")
    lines.append("")
    lines.append("| 指标 | 当前值 | 周变动 | 阈值 | 状态 | 风险 | 日期 | 来源 |")
    lines.append("| --- | ---: | ---: | --- | :---: | :---: | --- | --- |")
    for key, m in usd_liq["indicators"].items():
        val = m.get("value", "-")
        wchg = m.get("week_change", "-")
        thr = m.get("threshold", "—")
        st = "🔴触发" if m.get("triggered") else "✅正常"
        risk = m.get("risk_level", "-")
        dd = m.get("data_date", "-") or "-"
        src = m.get("source", "-")[:20]
        lines.append(f"| {m['name']} | {val} | {wchg} | {thr} | {st} | {risk} | {dd} | {src} |")
    lines.append("")

    # 模块B：中国固收
    lines.append("## 二、中国固收流动性")
    lines.append("")
    lines.append(f"**五阶段判定：{china_fi.get('stage', '—')}**")
    lines.append("")
    if china_fi["indicators"]:
        lines.append("| 指标 | 当前值 | 利差 | 分位数 | 状态 | 风险 | 日期 | 来源 |")
        lines.append("| --- | ---: | ---: | ---: | :---: | :---: | --- | --- |")
        for key, m in china_fi["indicators"].items():
            val = m.get("value", "-")
            spread = m.get("vs_mlf", m.get("vs_omo", "—"))
            if spread == "—" or spread is None:
                spread = "—"
            qtl = m.get("quantile", "—")
            st = "🔴触发" if m.get("triggered") else "✅正常"
            risk = m.get("risk_level", "-")
            dd = m.get("data_date", "-") or "-"
            src = (m.get("source", "-") or "-")[:25]
            lines.append(f"| {m['name']} | {val} | {spread} | {qtl} | {st} | {risk} | {dd} | {src} |")
    else:
        lines.append("_数据接入中，敬请期待_")
    lines.append("")

    # 五阶段说明
    lines.append("**五阶段判定逻辑：**")
    lines.append("")
    lines.append("①宽松深水区 → ②临界试探 → ③供给拐点 → ④紧缩确认 → ⑤危机模式")
    lines.append("")
    lines.append("_说明：当前DR/R/存单等数据接入中，阶段判定基于已有数据近似，完整判定待全部数据源接入后校准。_")
    lines.append("")

    # 模块C：信用利差三层传导
    lines.append("## 三、信用利差三层传导")
    lines.append("")
    lines.append(f"**传导状态：{credit.get('status', '—')}**")
    lines.append("")
    layer_titles = {"l2": "### L2 货币信用链（传导起点）",
                    "l3": "### L3 银行层（传导中继）",
                    "l4": "### L4 外汇层（传导放大器）"}
    for lk in ["l2", "l3", "l4"]:
        lines.append(layer_titles[lk])
        lines.append("")
        lines.append("| 指标 | 当前值 | 周变动 | 4周变动 | 阈值 | 状态 | 风险 | 日期 | 来源 |")
        lines.append("| --- | ---: | ---: | ---: | --- | :---: | :---: | --- | --- |")
        for key, m in credit[lk].items():
            val = m.get("value", "-")
            wchg = m.get("week_change", "-")
            mchg = m.get("month_change", "-")
            thr = m.get("threshold", "—")
            st = "🔴触发" if m.get("triggered") else "✅正常"
            risk = m.get("risk_level", "-")
            dd = m.get("data_date", "-") or "-"
            src = m.get("source", "-")[:25]
            lines.append(f"| {m['name']} | {val} | {wchg} | {mchg} | {thr} | {st} | {risk} | {dd} | {src} |")
        lines.append("")

    # 触发详情
    all_triggered = []
    for key, m in usd_liq["indicators"].items():
        if m.get("triggered"):
            all_triggered.append(("A 美元流动性", key, m))
    for lk in ["l2", "l3", "l4"]:
        for key, m in credit[lk].items():
            if m.get("triggered"):
                all_triggered.append((f"C 信用{lk.upper()}", key, m))

    def _trigger_reason(key, m) -> str:
        """根据指标类型和数据，判断具体触发原因"""
        val = m.get("value", "")
        q = m.get("quantile")
        wchg = m.get("week_change")
        mchg = m.get("month_change")
        reasons = []
        # 分位数触发
        if q and q != "—":
            try:
                qv = float(str(q).replace("%", ""))
                if qv >= 90:
                    reasons.append(f"历史分位数{q}%（≥90%，处于极端高位）")
                elif qv >= 80:
                    reasons.append(f"历史分位数{q}%（≥80%，处于高位）")
                elif qv >= 70:
                    reasons.append(f"历史分位数{q}%（≥70%，处于偏高位置）")
            except (ValueError, TypeError):
                pass
        # 变动触发（从周变动/月变动文字里提取bp数）
        if mchg and mchg not in ("无数据", "-", "—") and "bp" in str(mchg):
            reasons.append(f"4周变动{mchg}")
        if wchg and wchg not in ("无数据", "-", "—") and "bp" in str(wchg) and not mchg:
            reasons.append(f"周变动{wchg}")
        # 特殊指标
        if key == "rrp":
            reasons.append("逆回购规模已归零，流动性缓冲耗尽")
        elif key == "net_liquidity":
            reasons.append("净流动性连降3周，流量拐点确认")
        elif key == "move_index":
            try:
                v = float(str(val).replace("%", "").replace(",", ""))
                if v >= 120:
                    reasons.append(f"MOVE指数{v:.0f}（>120，恐慌区间）")
                elif v >= 100:
                    reasons.append(f"MOVE指数{v:.0f}（>100，预警区间）")
                elif v >= 80:
                    reasons.append(f"MOVE指数{v:.0f}（>80，关注区间）")
            except (ValueError, TypeError):
                reasons.append(f"当前值{val}超过阈值")
        elif "hy_ig" in key:
            reasons.append(f"{val}，信用分层加剧")
        elif "ccc_ig" in key:
            reasons.append(f"{val}，尾部风险扩散")
        elif "kbe" in key:
            reasons.append(f"银行股周跌幅{wchg}，信用风险向银行传导")
        elif not reasons:
            reasons.append(f"当前值{val}超过阈值")
        return "；".join(reasons) if reasons else f"当前值{val}超过阈值"

    if all_triggered:
        lines.append("## ⚠️ 触发指标详情")
        lines.append("")
        for module, key, m in all_triggered:
            lines.append(f"### {m['risk_level']} {m['name']}")
            lines.append("")
            lines.append(f"- **所属模块**：{module}")
            lines.append(f"- **当前值**：{m['value']}")
            if m.get("quantile") and m["quantile"] != "—":
                lines.append(f"- **历史分位**：{m['quantile']}（250交易日）")
            if m.get("week_change") not in (None, "无数据", "-", "—"):
                lines.append(f"- **周变动**：{m.get('week_change')}")
            if m.get("month_change") not in (None, "无数据", "-", "—"):
                lines.append(f"- **4周变动**：{m.get('month_change')}")
            lines.append(f"- **触发原因**：{_trigger_reason(key, m)}")
            lines.append(f"- **阈值**：{m['threshold']}")
            lines.append(f"- **数据日期**：{m.get('data_date', '-')}")
            lines.append(f"- **数据来源**：{m['source']}")
            lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(f"*生成时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
    lines.append("")
    lines.append("*数据说明：主要来自FRED官方数据（延迟1-2工作日），部分指标为公开市场数据。中国固收模块数据接入中。*")

    return "\n".join(lines)


def generate_feishu_summary(usd_liq, china_fi, credit, overall, run_date) -> str:
    """飞书卡片精简版"""
    lines = []
    lines.append(f"**综合评级：{overall['grade']}**")
    lines.append("")
    lines.append(overall['summary'])
    lines.append("")

    lines.append("**美元流动性：" + usd_liq.get('status', '—') + "**")
    lines.append("")
    for key, m in usd_liq["indicators"].items():
        if m.get("triggered"):
            lines.append(f"- 🔴 {m['name']}：{m['value']}")
        elif m.get("value") != "无数据":
            lines.append(f"- ✅ {m['name']}：{m['value']}")
    lines.append("")

    lines.append("**信用传导：" + credit.get('status', '—') + "**")
    lines.append("")
    for lk in ["l2", "l3", "l4"]:
        for key, m in credit[lk].items():
            if m.get("triggered"):
                lines.append(f"- 🔴 {m['name']}：{m['value']}")
    # 核心指标速览
    core_keys_l2 = ["ig_oas", "hy_oas"]
    core_keys_l3 = ["kbe"]
    core_keys_l4 = ["dxy", "usdjpy"]
    for key in core_keys_l2:
        m = credit["l2"].get(key)
        if m and not m.get("triggered") and m.get("value") != "无数据":
            lines.append(f"- ✅ {m['name']}：{m['value']}")
    for key in core_keys_l3:
        m = credit["l3"].get(key)
        if m and not m.get("triggered") and m.get("value") != "无数据":
            lines.append(f"- ✅ {m['name']}：{m['value']}")
    for key in core_keys_l4:
        m = credit["l4"].get(key)
        if m and not m.get("triggered") and m.get("value") != "无数据":
            lines.append(f"- ✅ {m['name']}：{m['value']}")
    lines.append("")

    if overall["score"] > 0:
        lines.append("**📌 重点关注**")
        lines.append("")
        lines.append(f"- 综合得分 {overall['score']}，流动性告警 {overall['usd_alerts']} 项，信用传导 {overall['credit_layers']} 层")
        lines.append("")

    lines.append(f"_完整报告见附件 | {run_date}_")
    return "\n".join(lines)


def _overall_from_modules(usd_liq=None, china_fi=None, credit=None):
    """根据指定模块计算局部综合评级（用于美国版/中国版拆分）"""
    total_score = 0
    triggered = 0
    active_modules = []
    
    if usd_liq:
        usd_score = usd_liq.get("score", len(usd_liq.get("alerts", [])))
        total_score += usd_score
        triggered += len(usd_liq.get("alerts", []))
        grade = usd_liq.get("status", "—")
        active_modules.append(("美元流动性", grade))
    
    if china_fi:
        cn_score = china_fi.get("score", len(china_fi.get("alerts", [])))
        total_score += cn_score
        triggered += len(china_fi.get("alerts", []))
        grade = china_fi.get("grade", china_fi.get("status", "—"))
        active_modules.append(("中国固收", grade))
    
    if credit:
        triggered_layers = credit.get("triggered_layers", 0)
        credit_alerts = sum(len(v) for v in credit.get("alerts", {}).values())
        total_score += triggered_layers * 2
        triggered += credit_alerts
        grade = credit.get("status", "—")
        active_modules.append(("信用传导", grade))
    
    if total_score >= 5:
        grade = "🔴 高风险"
    elif total_score >= 3:
        grade = "🟠 显著风险"
    elif total_score >= 1:
        grade = "🟡 边际收紧"
    else:
        grade = "🟢 平稳"
    
    return {
        "grade": grade,
        "score": total_score,
        "total_alerts": triggered,
        "modules": active_modules,
    }


def generate_us_report(usd_liq, credit, overall, run_date) -> str:
    """生成美国版周报（美元流动性+信用传导）"""
    lines = []
    lines.append(f"# 🇺🇸 美元流动性与信用风险周报 | {run_date}")
    lines.append("")
    lines.append(f"**综合评级：{overall['grade']}**")
    lines.append("")
    
    # 美元流动性
    lines.append("## 一、美元流动性（L1层）")
    lines.append("")
    lines.append(f"**评级：{usd_liq.get('status', '—')}**")
    lines.append("")
    lines.append("| 指标 | 当前值 | 周变动 | 4周变动 | 风险等级 | 触发 |")
    lines.append("|---|---|---|---|---|---|")
    for key, m in usd_liq["indicators"].items():
        icon = "⚠️" if m.get("triggered") else ""
        lines.append(f"| {m['name']} | {m['value']} | {m.get('week_change', '—')} | {m.get('month_change', '—')} | {m['risk_level']} | {icon} |")
    lines.append("")
    lines.append(usd_liq.get("summary", usd_liq.get("status", "")))
    lines.append("")
    
    # 信用传导
    lines.append("## 二、信用传导风险（L2/L3/L4）")
    lines.append("")
    lines.append(f"**评级：{credit.get('grade', credit.get('status', '—'))}**")
    lines.append("")
    
    for lk, lname in [("l2", "L2 货币信用链（企业融资成本）"), ("l3", "L3 银行层（银行资产质量）"), ("l4", "L4 外汇层（汇率与利差）")]:
        lines.append(f"### {lname}")
        lines.append("")
        lines.append("| 指标 | 当前值 | 周变动 | 4周变动 | 历史分位 | 风险等级 | 触发 |")
        lines.append("|---|---|---|---|---|---|---|")
        for key, m in credit[lk].items():
            icon = "⚠️" if m.get("triggered") else ""
            q = m.get("quantile", "—")
            lines.append(f"| {m['name']} | {m['value']} | {m.get('week_change', '—')} | {m.get('month_change', '—')} | {q} | {m['risk_level']} | {icon} |")
        lines.append("")
    
    lines.append("---")
    lines.append(f"*{run_date} | 全景监控系统自动生成*")
    return "\n".join(lines)


def generate_cn_report(china_fi, overall, run_date) -> str:
    """生成中国版周报（中国固收+流动性）"""
    lines = []
    lines.append(f"# 🇨🇳 中国流动性与固收风险周报 | {run_date}")
    lines.append("")
    lines.append(f"**综合评级：{overall['grade']}**")
    lines.append("")
    
    # 中国固收
    lines.append("## 一、中国货币市场")
    lines.append("")
    lines.append(f"**评级：{china_fi.get('grade', china_fi.get('status', '—'))}**")
    lines.append("")
    lines.append("| 指标 | 当前值 | 历史分位 | 数据日期 | 风险等级 | 说明 |")
    lines.append("|---|---|---|---|---|---|")
    for key, m in china_fi["indicators"].items():
        q = m.get("quantile", "—")
        lines.append(f"| {m['name']} | {m['value']} | {q} | {m.get('date', '—')} | {m['risk_level']} | {m.get('note', m.get('source', ''))} |")
    lines.append("")
    
    # 五阶段判定
    stage = china_fi.get("stage", "数据不足")
    lines.append(f"### 五阶段流动性判定：{stage}")
    lines.append("")
    
    # 关键指标
    lines.append("### 核心利率概览")
    lines.append("")
    lines.append(f"- **DR007（银行间基准利率）**：{china_fi['indicators'].get('dr007', {}).get('value', '—')}")
    lines.append(f"- **OMO 7天（政策利率走廊上限）**：{china_fi['indicators'].get('omo_7d', {}).get('value', '—')}")
    lines.append(f"- **MLF 1年（中期政策利率）**：{china_fi['indicators'].get('mlf_1y', {}).get('value', '—')}")
    lines.append(f"- **LPR 1年（贷款基准利率）**：{china_fi['indicators'].get('lpr_1y', {}).get('value', '—')}")
    lines.append("")
    
    # 期限利差
    ts = china_fi.get("term_spread")
    if ts and isinstance(ts, dict):
        lines.append(f"### 期限利差（10Y国债 - 1Y国债）")
        lines.append("")
        lines.append(f"- 当前：{ts.get('value', '—')}")
        lines.append(f"- 周变动：{ts.get('week_change', '—')}")
        lines.append("")
    
    lines.append("---")
    lines.append(f"*{run_date} | 全景监控系统自动生成*")
    return "\n".join(lines)


def generate_feishu_us_summary(usd_liq, credit, overall, run_date) -> str:
    """飞书卡1：美国总览（L1流动性 + L2/L3/L4信用分层 + 银行股）"""
    lines = []
    lines.append(f"**综合评级：{overall['grade']}**")
    lines.append("")
    
    # L1 美元流动性
    lines.append("**🏦 L1 美元流动性**")
    liq_keys = ["effr", "rrp", "net_liquidity", "move_index"]
    display_names = {
        "effr": "EFFR-IORB利差",
        "rrp": "ON RRP逆回购",
        "net_liquidity": "净流动性",
        "move_index": "MOVE波动率",
    }
    for key in liq_keys:
        m = usd_liq["indicators"].get(key, {})
        if m:
            icon = " ⚠️" if m.get("triggered") else ""
            lines.append(f"- {display_names.get(key, m['name'])}：**{m['value']}**{icon}")
    lines.append("")
    
    # L2 信用利差
    lines.append("**📊 L2 货币信用链（企业融资）**")
    l2_items = []
    for key in ["ig_oas", "hy_oas", "ccc_oas"]:
        m = credit["l2"].get(key, {})
        if m:
            icon = " ⚠️" if m.get("triggered") else ""
            q = m.get("quantile", "")
            q_tag = f"（{q}分位）" if q and q != "—" else ""
            l2_items.append(f"{m['name']} **{m['value']}**{q_tag}{icon}")
    if l2_items:
        for item in l2_items:
            lines.append(f"- {item}")
    # 评级利差
    hy_ig = credit["l2"].get("hy_ig_spread", {})
    ccc_ig = credit["l2"].get("ccc_ig_spread", {})
    if hy_ig.get("value"):
        icon = " ⚠️" if hy_ig.get("triggered") else ""
        lines.append(f"- HY-IG评级利差：**{hy_ig['value']}**{icon}")
    if ccc_ig.get("value"):
        icon = " ⚠️" if ccc_ig.get("triggered") else ""
        lines.append(f"- CCC-IG评级利差：**{ccc_ig['value']}**{icon}")
    lines.append("")
    
    # L3 银行层
    lines.append("**🏛️ L3 银行层**")
    kbe = credit["l3"].get("kbe_price", {})
    if kbe.get("value"):
        icon = " ⚠️" if kbe.get("triggered") else ""
        wc = kbe.get("week_change", "")
        wc_tag = f"（周{wc}）" if wc and wc != "—" else ""
        lines.append(f"- KBE银行股ETF：**{kbe['value']}**{wc_tag}{icon}")
    else:
        lines.append("- KBE银行股ETF：数据获取中")
    trans = credit["l3"].get("transmission_confirm", {})
    if trans.get("value"):
        icon = " ⚠️" if trans.get("triggered") else ""
        lines.append(f"- HY-银行股传导：**{trans['value']}**{icon}")
    lines.append("")
    
    # L4 外汇层
    lines.append("**💱 L4 外汇层**")
    dxy = credit["l4"].get("dxy", {})
    usdjpy = credit["l4"].get("usdjpy", {})
    if dxy.get("value"):
        icon = " ⚠️" if dxy.get("triggered") else ""
        lines.append(f"- 美元指数：**{dxy['value']}**{icon}")
    if usdjpy.get("value"):
        icon = " ⚠️" if usdjpy.get("triggered") else ""
        lines.append(f"- 美元/日元：**{usdjpy['value']}**{icon}")
    lines.append("")
    
    lines.append(f"**触发指标：{overall['total_alerts']}个**")
    lines.append("---")
    lines.append(f"_{run_date} | 美国版 | 详情见完整报告_")
    return "\n".join(lines)


def generate_feishu_cn_summary(china_fi, overall, run_date) -> str:
    """飞书卡1：中国总览（存单分位数优先，五阶段判定+核心利率）"""
    lines = []
    lines.append(f"**综合评级：{overall['grade']}**")
    lines.append("")
    
    # 1年存单（最核心指标）
    cd = china_fi["indicators"].get("cd_1y_aaa", {})
    if cd.get("value"):
        q = cd.get("quantile", "—")
        icon = " ⚠️" if cd.get("triggered") else ""
        est_tag = "（估算）" if cd.get("is_estimate") else ""
        lines.append(f"**📈 1年AAA同业存单{est_tag}**")
        lines.append(f"- 当前：**{cd['value']}%**{icon}")
        lines.append(f"- 历史分位：**{q}**（250交易日）")
        lines.append(f"- 数据日期：{cd.get('date', '—')}")
        lines.append("")
    
    # 五阶段判定
    stage = china_fi.get("stage", "数据不足")
    lines.append(f"**🧭 五阶段判定：{stage}**")
    lines.append("")
    
    # 核心利率矩阵
    lines.append("**🏦 核心利率概览**")
    key_items = [
        ("DR007", "dr007", "银行间基准利率"),
        ("DR001", "dr001", "隔夜资金面"),
        ("OMO 7天", "omo_7d", "政策利率走廊上限"),
        ("MLF 1年", "mlf_1y", "中期政策利率锚"),
        ("LPR 1年", "lpr_1y", "贷款基准利率"),
        ("R007-DR007", "r_dr_spread", "非银分层指标"),
    ]
    for label, key, desc in key_items:
        m = china_fi["indicators"].get(key, {})
        if m and m.get("value") not in (None, "无数据", "—"):
            val = m["value"]
            icon = " ⚠️" if m.get("triggered") else ""
            q = m.get("quantile", "")
            q_tag = f"（{q}分位）" if q and q != "—" else ""
            lines.append(f"- {label}：**{val}**{q_tag}{icon}")
    lines.append("")
    
    # 期限利差
    ts = china_fi.get("term_spread")
    if ts and isinstance(ts, dict) and ts.get("value"):
        lines.append(f"**📊 期限利差（10Y-1Y）：{ts['value']}**")
        lines.append("")
    
    lines.append(f"**触发指标：{overall['total_alerts']}个**")
    lines.append("---")
    lines.append(f"_{run_date} | 中国版 | 详情见完整报告_")
    return "\n".join(lines)


def generate_feishu_us_alert_detail(usd_liq, credit, overall, run_date) -> str:
    """飞书卡2：美国告警详情"""
    lines = []
    lines.append(f"**⚠️ 告警详情 | 共{overall['total_alerts']}项触发**")
    lines.append("")
    idx = 1
    
    # 美元流动性告警
    for key, m in usd_liq["indicators"].items():
        if m.get("triggered"):
            lines.append(f"**{idx}. {m['risk_level']} {m['name']}**")
            lines.append(f"- 当前值：**{m['value']}**")
            if m.get("week_change") not in (None, "无数据", "-", "—"):
                lines.append(f"- 周变动：{m['week_change']}")
            if m.get("month_change") not in (None, "无数据", "-", "—"):
                lines.append(f"- 4周变动：{m['month_change']}")
            if m.get("quantile") and m["quantile"] != "—":
                lines.append(f"- 历史分位：{m['quantile']}（250交易日）")
            
            if key == "rrp":
                reason = "逆回购规模归零，流动性缓冲耗尽"
            elif key == "net_liquidity":
                reason = "净流动性连降3周，流量拐点确认"
            elif key == "move_index":
                try:
                    v = float(str(m['value']))
                    reason = f"MOVE {v:.0f}，处于{'恐慌' if v >= 120 else '预警'}区间"
                except:
                    reason = "超过阈值"
            else:
                reason = "超过阈值"
            lines.append(f"- ⚡ 触发原因：{reason}")
            lines.append(f"- 📏 阈值：{m.get('threshold', '—')}")
            lines.append("")
            idx += 1
    
    # 信用传导告警
    for lk in ["l2", "l3", "l4"]:
        for key, m in credit[lk].items():
            if m.get("triggered"):
                lines.append(f"**{idx}. {m['risk_level']} {m['name']}（L{lk.upper()}）**")
                lines.append(f"- 当前值：**{m['value']}**")
                if m.get("week_change") not in (None, "无数据", "-", "—"):
                    lines.append(f"- 周变动：{m['week_change']}")
                if m.get("month_change") not in (None, "无数据", "-", "—"):
                    lines.append(f"- 4周变动：{m['month_change']}")
                if m.get("quantile") and m["quantile"] != "—":
                    lines.append(f"- 历史分位：{m['quantile']}（250交易日）")
                reasons = []
                q = m.get("quantile", "")
                if q and q != "—":
                    try:
                        qv = float(str(q).replace("%", ""))
                        reasons.append(f"分位数{q}%（{'极端高位' if qv >= 90 else '高位'}）")
                    except:
                        pass
                if "hy_ig" in key:
                    reasons.append("信用分层加剧（HY远超IG）")
                elif "ccc_ig" in key:
                    reasons.append("尾部风险扩散（CCC与IG差距过大）")
                if not reasons:
                    reasons.append("超过阈值")
                lines.append(f"- ⚡ 触发原因：{'；'.join(reasons)}")
                lines.append(f"- 📏 阈值：{m.get('threshold', '—')}")
                lines.append("")
                idx += 1
    
    lines.append("---")
    lines.append(f"_{run_date} | 美国版告警详情_")
    return "\n".join(lines)


def generate_feishu_cn_alert_detail(china_fi, overall, run_date) -> str:
    """飞书卡2：中国告警详情"""
    lines = []
    lines.append(f"**⚠️ 告警详情 | 共{overall['total_alerts']}项触发**")
    lines.append("")
    idx = 1
    
    for key, m in china_fi["indicators"].items():
        if m.get("triggered"):
            lines.append(f"**{idx}. {m['risk_level']} {m['name']}**")
            lines.append(f"- 当前值：**{m['value']}**")
            if m.get("quantile") and m["quantile"] != "—":
                lines.append(f"- 历史分位：{m['quantile']}")
            lines.append(f"- ⚡ 触发原因：{m.get('reason', '超过阈值')}")
            lines.append(f"- 📏 阈值：{m.get('threshold', '—')}")
            lines.append("")
            idx += 1
    
    if idx == 1:
        lines.append("*当前无触发告警*")
        lines.append("")
    
    lines.append("---")
    lines.append(f"_{run_date} | 中国版告警详情_")
    return "\n".join(lines)


# ========= 主流程 =========
def main():
    import argparse
    parser = argparse.ArgumentParser(description="周度流动性与信用风险监控（美国版+中国版）")
    parser.add_argument("mode", nargs="?", default="full", choices=["full", "report", "alert"])
    parser.add_argument("--no-feishu", action="store_true")
    parser.add_argument("--no-email", action="store_true")
    parser.add_argument("--force-alert", action="store_true")
    parser.add_argument("--region", default="all", choices=["all", "us", "cn"],
                        help="推送范围：all=两份都推 us=仅美国 cn=仅中国")
    args = parser.parse_args()

    today = datetime.date.today().isoformat()
    log.info(f"=== 全景监控启动 | 模式: {args.mode} | 区域: {args.region} | 日期: {today} ===")

    cfg = load_config()
    conn = db_init()

    # 1. 拉取FRED数据
    log.info("正在获取FRED数据...")
    fred_data = {}
    for key, sid in FRED_SERIES.items():
        rows = fred_fetch(sid, days=120)
        fred_data[key] = rows
        if rows:
            log.info(f"  {key} ({sid}): {len(rows)}条, 最新={rows[0]}")
        else:
            log.warning(f"  {key} ({sid}): 无数据")

    # 1b. 信用利差长期历史（用于分位数）
    log.info("拉取信用利差长期历史（用于分位数）...")
    fred_credit_long = {}
    for key in ("IG_OAS", "HY_OAS", "CCC_OAS"):
        sid = FRED_SERIES.get(key)
        if sid:
            rows = fred_fetch(sid, days=365)
            fred_credit_long[key] = rows
            log.info(f"  {key}: {len(rows)}条长期数据")

    # 2. 计算各模块
    log.info("计算美元流动性...")
    usd_liq = compute_usd_liquidity(cfg, fred_data)

    log.info("获取中国货币市场数据...")
    china_rates = fetch_china_rates()

    log.info("计算中国固收流动性...")
    china_fi = compute_china_fixed_income(cfg, fred_data, china_rates)

    log.info("计算信用利差三层传导...")
    credit = compute_credit_spread(cfg, fred_data, fred_credit_long)

    # ===== 3. 美国版报告 =====
    if args.region in ("all", "us"):
        log.info("--- 生成美国版报告 ---")
        us_overall = _overall_from_modules(usd_liq=usd_liq, credit=credit)
        log.info(f"美国版综合评级: {us_overall['grade']} (得分:{us_overall['score']}, 告警:{us_overall['total_alerts']})")

        us_report = generate_us_report(usd_liq, credit, us_overall, today)
        us_report_path = OUTPUT_DIR / f"{today}_美国流动性与信用风险周报.md"
        with open(us_report_path, "w", encoding="utf-8") as f:
            f.write(us_report)
        log.info(f"美国版报告已保存: {us_report_path}")

        # 飞书推送（双卡）
        if not args.no_feishu and args.mode in ("full", "report"):
            score = us_overall["score"]
            if score >= 5:
                template = "red"
            elif score >= 3:
                template = "orange"
            elif score >= 1:
                template = "yellow"
            else:
                template = "green"
            title = f"🇺🇸 {us_overall['grade']} 美国流动性与信用周报 | {today}"
            summary = generate_feishu_us_summary(usd_liq, credit, us_overall, today)
            push_feishu_card(cfg.get("feishu_webhook", ""), title, summary, template=template)
            
            if us_overall["total_alerts"] > 0:
                import time
                time.sleep(0.5)
                detail = generate_feishu_us_alert_detail(usd_liq, credit, us_overall, today)
                detail_title = f"⚠️ 美国告警详情（{us_overall['total_alerts']}项）| {today}"
                push_feishu_card(cfg.get("feishu_webhook", ""), detail_title, detail, template=template)

        # 美国告警邮件
        if not args.no_email and args.mode in ("full", "alert"):
            us_alerts = []
            for key in usd_liq.get("alerts", []):
                m = usd_liq["indicators"][key]
                alert_key = f"us_usd_{key}"
                if args.force_alert or should_alert(conn, alert_key, today):
                    us_alerts.append(("美元流动性", m, alert_key))
                    record_alert(conn, alert_key, m["name"], m.get("value"),
                               m.get("threshold"), m.get("risk_level"), "US-LIQ", today)
            for lk in ["l2", "l3", "l4"]:
                for key in credit.get("alerts", {}).get(lk, []):
                    m = credit[lk][key]
                    alert_key = f"us_credit_{lk}_{key}"
                    if args.force_alert or should_alert(conn, alert_key, today):
                        us_alerts.append((f"信用-{lk.upper()}", m, alert_key))
                        record_alert(conn, alert_key, m["name"], m.get("value"),
                                   m.get("threshold"), m.get("risk_level"), f"US-{lk.upper()}", today)
            
            if us_alerts:
                log.info(f"美国版发现 {len(us_alerts)} 个新告警，发送邮件...")
                html = f"""
                <html><body style="font-family: Arial, sans-serif; padding: 20px;">
                <h2 style="color: #d32f2f;">🇺🇸 ⚠️ 美国流动性与信用风险预警 | {today}</h2>
                <p><strong>综合评级：</strong>{us_overall['grade']}</p>
                <hr>
                <h3>触发指标（{len(us_alerts)}项）</h3>
                <table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; width: 100%;">
                <tr style="background: #f5f5f5;">
                <th>模块</th><th>指标</th><th>当前值</th><th>阈值</th><th>风险</th>
                </tr>
                """
                for module, m, _ in us_alerts:
                    html += f"""<tr>
                    <td>{module}</td>
                    <td>{m['name']}</td>
                    <td style="color: #d32f2f; font-weight: bold;">{m['value']}</td>
                    <td>{m['threshold']}</td>
                    <td>{m['risk_level']}</td>
                    </tr>"""
                html += """
                </table>
                <p style="color: #666; font-size: 12px;">
                本邮件由美国流动性与信用监控系统自动发送<br>
                幂等保护：同一天同一指标仅告警一次
                </p>
                </body></html>"""
                subject = f"🇺🇸 ⚠️ 美国风险预警 - {us_overall['grade']} - {today}"
                send_alert_email(cfg, subject, html)
            else:
                log.info("美国版无新触发告警")

    # ===== 4. 中国版报告 =====
    if args.region in ("all", "cn"):
        log.info("--- 生成中国版报告 ---")
        cn_overall = _overall_from_modules(china_fi=china_fi)
        log.info(f"中国版综合评级: {cn_overall['grade']} (得分:{cn_overall['score']}, 告警:{cn_overall['total_alerts']})")

        cn_report = generate_cn_report(china_fi, cn_overall, today)
        cn_report_path = OUTPUT_DIR / f"{today}_中国流动性与固收风险周报.md"
        with open(cn_report_path, "w", encoding="utf-8") as f:
            f.write(cn_report)
        log.info(f"中国版报告已保存: {cn_report_path}")

        # 飞书推送
        if not args.no_feishu and args.mode in ("full", "report"):
            score = cn_overall["score"]
            if score >= 5:
                template = "red"
            elif score >= 3:
                template = "orange"
            elif score >= 1:
                template = "yellow"
            else:
                template = "green"
            title = f"🇨🇳 {cn_overall['grade']} 中国流动性与固收周报 | {today}"
            summary = generate_feishu_cn_summary(china_fi, cn_overall, today)
            push_feishu_card(cfg.get("feishu_webhook", ""), title, summary, template=template)
            
            if cn_overall["total_alerts"] > 0:
                import time
                time.sleep(0.5)
                detail = generate_feishu_cn_alert_detail(china_fi, cn_overall, today)
                detail_title = f"⚠️ 中国告警详情（{cn_overall['total_alerts']}项）| {today}"
                push_feishu_card(cfg.get("feishu_webhook", ""), detail_title, detail, template=template)

        # 中国告警邮件
        if not args.no_email and args.mode in ("full", "alert"):
            cn_alerts = []
            for key in china_fi.get("alerts", []):
                m = china_fi["indicators"][key]
                alert_key = f"cn_fi_{key}"
                if args.force_alert or should_alert(conn, alert_key, today):
                    cn_alerts.append(("中国固收", m, alert_key))
                    record_alert(conn, alert_key, m["name"], m.get("value"),
                               m.get("threshold"), m.get("risk_level"), "CN-FI", today)
            
            if cn_alerts:
                log.info(f"中国版发现 {len(cn_alerts)} 个新告警，发送邮件...")
                html = f"""
                <html><body style="font-family: Arial, sans-serif; padding: 20px;">
                <h2 style="color: #d32f2f;">🇨🇳 ⚠️ 中国流动性与固收风险预警 | {today}</h2>
                <p><strong>综合评级：</strong>{cn_overall['grade']}</p>
                <hr>
                <h3>触发指标（{len(cn_alerts)}项）</h3>
                <table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; width: 100%;">
                <tr style="background: #f5f5f5;">
                <th>模块</th><th>指标</th><th>当前值</th><th>阈值</th><th>风险</th>
                </tr>
                """
                for module, m, _ in cn_alerts:
                    html += f"""<tr>
                    <td>{module}</td>
                    <td>{m['name']}</td>
                    <td style="color: #d32f2f; font-weight: bold;">{m['value']}</td>
                    <td>{m['threshold']}</td>
                    <td>{m['risk_level']}</td>
                    </tr>"""
                html += """
                </table>
                <p style="color: #666; font-size: 12px;">
                本邮件由中国流动性与固收监控系统自动发送<br>
                幂等保护：同一天同一指标仅告警一次
                </p>
                </body></html>"""
                subject = f"🇨🇳 ⚠️ 中国风险预警 - {cn_overall['grade']} - {today}"
                send_alert_email(cfg, subject, html)
            else:
                log.info("中国版无新触发告警")

    conn.close()
    log.info("=== 监控完成 ===")


if __name__ == "__main__":
    main()
