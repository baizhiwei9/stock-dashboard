#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一美债数据获取层（美债监控 - 单一数据入口）
============================================================
将原三个脚本各自独立的数据抓取统一到此处，一次性获取全部美债监控所需原始数据，
落盘 treasury_data_output.json；三个分析脚本（长短结构 / 加息降息路径 / 回购监控）
从此统一数据层读取，各自只做逻辑判断与计算，不再单独联网。

数据域（统一 JSON 的六个板块）：
  h41               美联储 H.4.1 Treasury 期限分布（当期 + 周/季/年对比）
  mspd              财政部 MSPD 存量分券种（当期 + 上月 / 去年同月）
  rollover_duration 滚动偿债比 / 久期 proxy（Auction 口径，当期 + 上月 / 去年同月）
  auction_structure 当月发债结构比（当月 + 上月 / 去年同月）
  rates             联邦基金目标区间 R（FRED，本地缓存降级）+ 两年美债 Y2（FRED，Treasury.gov 备用）
buyback           财政部回购公告时间表 XML + 回购结果 API
   fomc              FOMC 已公布会议日历（取最近已知会议日，用于推算未来会议时间点）
   auction_detail    拍卖认购明细（近 3 年，bid_to_cover / 得标利率 / 三类竞标额），
                     供认购热情与买方结构分析（近 12 次均值 / 尾差 / 占比）

数据源：
  - 美联储 H.4.1 归档   https://www.federalreserve.gov/releases/h41/{ymd}/h41.htm
  - fiscaldata        https://api.fiscaldata.treasury.gov/services/api/fiscal_service
                      /v1/debt/mspd/mspd_table_1、/v1/accounting/od/auctions_query
  - FRED 无 key CSV    https://fred.stlouisfed.org/graph/fredgraph.csv?id=...
                      DFEDTARU / DFEDTARL / DGS2（urllib 优先，curl 子进程降级）
  - Treasury.gov 收益率 https://home.treasury.gov/resource-center/...  (DGS2 同源备用)
  - 回购公告 XML        https://home.treasury.gov/system/files/221/Tentative-Buyback-Schedule.xml
  - 回购结果 API        https://api.fiscal.treasury.gov/ap/exp/v1/marketable-securities/buybacks
  - FOMC 日历          https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm

用法：
  python3 treasury_data_fetch.py [--json 输出文件]
    默认输出 treasury_data_output.json
"""

import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta

import pandas as pd
import requests

# ---------------- 配置 ----------------
FISCAL_BASE = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service"
H41_ARCHIVE = "https://www.federalreserve.gov/releases/h41/{ymd}/h41.htm"
H41_LOOKBACK_WEEKS = 13
FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv"
FRED_SERIES = ("DFEDTARU", "DFEDTARL", "DGS2")
RATES_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "rate_fedfunds_cache.json")
TREASURY_YIELD_URL = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
                      "TextView?type=daily_treasury_yield_curve&field_tdr_date_value=%d")
BUYBACK_SCHEDULE_URL = "https://home.treasury.gov/system/files/221/Tentative-Buyback-Schedule.xml"
BUYBACK_API = "https://api.fiscal.treasury.gov/ap/exp/v1/marketable-securities/buybacks"
# client_id / client_secret 取自 treasurydirect 官网 buy-backs 页面公开脚本（页面表格同源接口）。
# 值本身是页面公开的，但仍按「敏感配置不入库」统一收口到
# ~/.config/stock-dashboard/secrets.json 的 treasury 段（或 TREASURY_CLIENT_ID /
# TREASURY_CLIENT_SECRET 环境变量）。缺失时该模块降级跳过，不阻断其它取数。
def _buyback_headers():
    try:
        _lib = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_lib")
        if _lib not in sys.path:
            sys.path.insert(0, _lib)
        import app_secrets
        sec = app_secrets.section("treasury")
    except Exception:                                      # noqa: BLE001
        sec = {}
    if not (sec.get("client_id") and sec.get("client_secret")):
        return {}
    return {"client_id": sec["client_id"], "client_secret": sec["client_secret"]}


BUYBACK_HEADERS = _buyback_headers()
FOMC_CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}
MONTHS = {
    "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
    "July": 7, "August": 8, "September": 9, "October": 10, "November": 11, "December": 12,
}


# ---------------- 基础请求 ----------------
def _get(url, params=None, headers=None, retries=3, timeout=90):
    """requests 通用请求（fiscaldata / federalreserve / treasury.gov 均可用）。"""
    last_err = None
    for i in range(retries):
        try:
            h = dict(HEADERS)
            if headers:
                h.update(headers)
            r = requests.get(url, params=params, headers=h, timeout=timeout)
            if r.status_code < 500:
                return r
            last_err = "HTTP %s" % r.status_code
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
        time.sleep(1 + i)
    raise RuntimeError("请求失败: %s | %s" % (url, last_err))


def _curl(url, tries=3, timeout=30):
    """抓取 URL 文本：requests 优先，curl 作降级。

    历史实现只走 curl（当时 FRED 对 python 侧间歇超时）。2026-11 复测环境反转：
    本机 curl 直连 fred.stlouisfed.org 稳定 exit 28（30s 超时），而 requests
    1.8s 正常返回 210KB。单一通道会把整条取数链路拖死，故改成双通道互为备份，
    任一成功即返回；两条都失败才抛错，错误信息带两侧原因便于定位。
    """
    last_err = []
    # 1) urllib（2026-11 实测本机对 FRED 唯一稳定通道：1.8s / 210KB；
    #    同环境 requests 稳定 ReadTimeout 25s、curl 稳定 exit 28）
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            if body:
                return body
            last_err.append("urllib: empty body")
        except Exception as exc:  # noqa: BLE001
            last_err.append("urllib: %s" % exc)
        time.sleep(1 + i)
    # 2) curl 降级（强制 --http1.1：部分网络下 HTTP/2 会返回 CURLE_HTTP2_STREAM）
    for i in range(tries):
        try:
            out = subprocess.check_output(
                ["curl", "-s", "--http1.1", "--compressed",
                 "--max-time", str(timeout),
                 "-A", "Mozilla/5.0", url],
                stderr=subprocess.DEVNULL, timeout=timeout + 10)
            if out:
                return out.decode("utf-8", errors="replace")
            last_err.append("curl: empty body")
        except Exception as exc:  # noqa: BLE001
            last_err.append("curl: %s" % exc)
        time.sleep(2 + i * 2)
    raise RuntimeError("抓取失败 %s: %s" % (url, " | ".join(last_err[-4:])))


def _fiscal(endpoint, params, timeout=90):
    r = _get(FISCAL_BASE + endpoint, params, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError("fiscaldata %s -> HTTP %s" % (endpoint, r.status_code))
    return r.json()


def _fiscal_all(endpoint, params, page_size=500, timeout=120):
    rows, page = [], 1
    while True:
        d = _fiscal(endpoint, dict(params, **{"page[size]": page_size, "page[number]": page}),
                    timeout=timeout)
        rows.extend(d.get("data", []))
        total = int(d.get("meta", {}).get("total-count", 0))
        if len(rows) >= total or not d.get("data"):
            break
        page += 1
    return rows


def _to_series(x, fill=0.0):
    s = x if isinstance(x, pd.Series) else pd.Series(x)
    return pd.to_numeric(s, errors="coerce").fillna(fill)


# ---------------- 1. 美联储 H.4.1 ----------------
def _parse_h41(html_text):
    dfs = pd.read_html(io.StringIO(html_text))
    for t in dfs:
        s = t.to_string()
        if "Within 15 days" in s and "U.S. Treasury securities" in s:
            for i in range(1, len(t)):
                prev = str(t.iloc[i - 1, 0])
                cur = str(t.iloc[i, 0])
                if "U.S. Treasury securities" in prev and cur.strip() == "Holdings":
                    row = t.iloc[i]
                    vals = _to_series(row).tolist()
                    short = vals[1] + vals[2] + vals[3]
                    long_ = vals[4] + vals[5] + vals[6]
                    total = vals[7] if len(vals) > 7 else short + long_
                    if total <= 0:
                        raise RuntimeError("H.4.1 解析：Treasury 持仓合计为 0")
                    return {
                        "short_1y_mil": short,
                        "long_mil": long_,
                        "total_mil": total,
                        "ratio_short_long": short / long_,
                        "short_share_pct": short / total * 100.0,
                    }
    raise RuntimeError("H.4.1 解析失败：未找到 U.S. Treasury securities 期限分布表")


def _parse_h41_release_date(html_text, fallback=None):
    m = re.search(r"For release at\s+.*?([A-Z][a-z]+)\s+(\d{1,2}),\s+(\d{4})", html_text)
    if m:
        try:
            return date(int(m.group(3)), MONTHS[m.group(1)], int(m.group(2)))
        except (KeyError, ValueError):
            pass
    return fallback


def _h41_archive_on_or_before(target):
    for back in range(12):
        cand = target - timedelta(days=back)
        url = H41_ARCHIVE.format(ymd=cand.strftime("%Y%m%d"))
        try:
            r = requests.get(url, headers=HEADERS, timeout=40)
            if r.status_code == 200:
                return cand, r.text
        except Exception:  # noqa: BLE001
            break
    raise RuntimeError("H.4.1 归档：找不到 %s 附近的有效发布日" % target)


def fetch_h41_release(ref_date):
    rel_date, html = _h41_archive_on_or_before(ref_date)
    metrics = _parse_h41(html)
    rel = _parse_h41_release_date(html, fallback=rel_date)
    return {"release_date": str(rel), "archive_date": str(rel_date), **metrics}


# ---------------- 2. MSPD 汇总 ----------------
def fetch_mspd_summary(as_of=None):
    if as_of is None:
        d = _fiscal("/v1/debt/mspd/mspd_table_1", {"sort": "-record_date", "page[size]": 60})
        recs = d["data"]
        latest = max(r["record_date"] for r in recs)
    else:
        d = _fiscal("/v1/debt/mspd/mspd_table_1", {"filter": "record_date:eq:%s" % as_of,
                                                   "page[size]": 60})
        recs = d["data"]
        latest = as_of
    amt = {}
    for r in recs:
        if r["record_date"] != latest:
            continue
        sc = r["security_class_desc"]
        pub = float(r.get("debt_held_public_mil_amt") or 0)
        ig = float(r.get("intragov_hold_mil_amt") or 0)
        amt[sc] = amt.get(sc, 0.0) + pub + ig

    bills = amt.get("Bills", 0.0)
    notes = amt.get("Notes", 0.0)
    bonds = amt.get("Bonds", 0.0)
    tips = amt.get("Treasury Inflation-Protected Securities", 0.0)
    frn = amt.get("Floating Rate Notes", 0.0)
    marketable = bills + notes + bonds + tips + frn
    return {
        "record_date": latest,
        "bills_mil": bills,
        "notes_mil": notes,
        "bonds_mil": bonds,
        "tips_mil": tips,
        "frn_mil": frn,
        "marketable_mil": marketable,
        "ratio_short_long": bills / (notes + bonds + tips) if (notes + bonds + tips) else None,
        "tbill_share_pct": bills / marketable * 100.0 if marketable else None,
    }


def mspd_timeline():
    d = _fiscal("/v1/debt/mspd/mspd_table_1", {"sort": "-record_date", "page[size]": 260})
    return sorted({r["record_date"] for r in d["data"]}, reverse=True)


# ---------------- 3/5. 滚动偿债比 / 久期 proxy（Auction 口径） ----------------
def fetch_rollover_duration(as_of, marketable_mil):
    dr = pd.Timestamp(as_of)
    rows = _fiscal_all(
        "/v1/accounting/od/auctions_query",
        {"filter": "maturity_date:gte:%s" % as_of,
         "fields": "cusip,security_type,security_term,issue_date,maturity_date,offering_amt"},
        page_size=500, timeout=150,
    )
    if not rows:
        raise RuntimeError("Auction 未到期记录为空（数据日 %s）" % as_of)
    df = pd.DataFrame(rows)
    df["offering"] = _to_series(df["offering_amt"])
    df["issue_date"] = pd.to_datetime(df["issue_date"], errors="coerce")
    df["maturity_date"] = pd.to_datetime(df["maturity_date"], errors="coerce")
    issued = df[df["issue_date"].notna() & (df["issue_date"] <= dr)]
    if issued.empty:
        raise RuntimeError("Auction 已发行未到期记录为空（数据日 %s）" % as_of)

    total_off = float(issued["offering"].sum())
    if total_off <= 0:
        raise RuntimeError("Auction 面值合计为 0")
    end12 = dr + timedelta(days=365)
    m12 = issued["maturity_date"] <= end12
    rollover_off = float(issued.loc[m12, "offering"].sum())
    rollover_off_mil = rollover_off / 1e6
    rollover_pct = rollover_off_mil / marketable_mil * 100.0 if marketable_mil else None

    days_left = (issued["maturity_date"] - dr).dt.days
    months = days_left / 30.44
    avg_maturity = float((issued["offering"] * months).sum() / total_off)

    return {
        "record_date": str(dr.date()),
        "n_records": len(issued),
        "n_cusips": int(issued["cusip"].nunique()),
        "maturing_in_12m_cusips": int(issued.loc[m12, "cusip"].nunique()),
        "offering_total_mil": total_off / 1e6,
        "offering_12m_mil": rollover_off_mil,
        "marketable_mil": marketable_mil,
        "rollover_12m_pct": rollover_pct,
        "avg_maturity_months": avg_maturity,
    }


# ---------------- 4. Auction 发债结构 ----------------
def fetch_auction_month(end_date, month_start=None):
    if month_start is None:
        month_start = end_date.replace(day=1)
    rows = _fiscal_all(
        "/v1/accounting/od/auctions_query",
        {"filter": "auction_date:gte:%s,auction_date:lte:%s" % (month_start, end_date),
         "fields": "security_type,auction_date,offering_amt"},
        page_size=300, timeout=150,
    )
    out = {"period": "%s ~ %s" % (month_start, end_date), "n_auctions": len(rows),
           "bills_share_pct": None, "bills_mil": 0.0, "total_mil": 0.0}
    if not rows:
        return out
    df = pd.DataFrame(rows)
    df["offering"] = _to_series(df["offering_amt"])
    bills = float(df.loc[df["security_type"].str.upper() == "BILL", "offering"].sum())
    total = float(df["offering"].sum())
    out["bills_mil"] = bills / 1e6
    out["total_mil"] = total / 1e6
    out["bills_share_pct"] = bills / total * 100.0 if total else None
    return out


# ---------------- 4b. Auction 拍卖认购明细（认购热情 / 买方结构） ----------------
def fetch_auction_detail(since_years=3.5):
    """抓取近 since_years 年已发布结果的拍卖明细。


每次拍卖保留核心字段：认购倍数 / 得标利率 / 一级交易商+直接+间接竞标额，
供分析脚本计算近 12 次均值、三类占比与趋势。fiscaldata 不含 WI（预发行）报价，
尾差（tail）无法由本 API 计算，分析脚本对 tail 直接标注"缺数据"。

返回：按 auction_date 升序的明细 dict 列表。
    """
    since = (date.today() - timedelta(days=int(since_years * 365.25))).isoformat()
    rows = _fiscal_all(
        "/v1/accounting/od/auctions_query",
        {"filter": "auction_date:gte:%s" % since,
         "fields": ("security_type,security_term,auction_date,issue_date,maturity_date,"
                    "offering_amt,bid_to_cover_ratio,high_yield,high_discnt_rate,"
                    "primary_dealer_tendered,primary_dealer_accepted,"
                    "direct_bidder_tendered,direct_bidder_accepted,"
                    "indirect_bidder_tendered,indirect_bidder_accepted,"
                    "comp_tendered,comp_accepted,noncomp_accepted")},
        page_size=500, timeout=180,
    )

    def clean(r):
        out = {}
        for k, v in r.items():
            if v in (None, "", "None", "null"):
                out[k] = None
            else:
                out[k] = v
        return out

    items = [clean(r) for r in rows]
    items.sort(key=lambda x: (x.get("auction_date") or "", x.get("security_term") or ""))
    return {
        "since": since,
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "n_records": len(items),
        "records": items,
    }


def _month_bounds(y, m):
    start = date(y, m, 1)
    end = (date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)) - timedelta(days=1)
    return start, end


# ---------------- 6. 利率：R 区间 / Y2 ----------------
def _fred_csv(series_id):
    rows = list(io.StringIO(_curl("%s?id=%s" % (FRED, series_id))))
    last = None
    for row in reversed(rows):
        parts = [p.strip() for p in row.split(",")]
        if len(parts) >= 2 and parts[1]:
            last = (parts[0], float(parts[1]))
            break
    if last is None:
        raise RuntimeError("FRED %s 无有效观测" % series_id)
    return last


def _treasury_yield_2y():
    html = _curl(TREASURY_YIELD_URL % date.today().year)
    rows = re.split(r"<tr[^>]*>", html)
    for r in reversed(rows):
        dm = re.search(r'datetime="(20\d\d-\d\d-\d\d)T', r)
        if not dm:
            continue
        t2 = re.search(r'class="[^"]*field-bc-2year[^"]*"[^>]*>([^<]+)<', r)
        if not t2:
            continue
        v = t2.group(1).strip()
        if v not in ("N/A", "") and re.match(r"^\d", v):
            return dm.group(1), float(v)
    raise RuntimeError("Treasury.gov 收益率页无有效 2Y 观测")


def _edb_db_path():
    """定位 data_hub EDB 库（统一接口；多路径探测，兼容换机/换挂载）。"""
    env = (os.environ.get("EDB_REPO") or "").strip()
    cands = []
    if env:
        cands.append(env)
    cands += [
        "/opt/coze/.fuse_drive/dataHub_三级体系",
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "dataHub_三级体系"),
    ]
    for repo in cands:
        p = os.path.join(repo, "data_hub", "edb", "data", "edb.sqlite3")
        if os.path.exists(p):
            return p
    return None


def _edb_latest(series_id, db=None):
    """从 data_hub EDB 取某序列最新 (date, value)；取不到返回 None。

    口径统一规则：利率/行情一律引用 data_hub 统一接口，不各自直连源站。
    """
    db = db or _edb_db_path()
    if not db:
        return None
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=10)
        try:
            row = con.execute(
                "SELECT date, value FROM edb_points WHERE series_id=? "
                "ORDER BY date DESC LIMIT 1", (series_id,)).fetchone()
        finally:
            con.close()
        if row and row[1] is not None:
            return str(row[0]), float(row[1])
    except Exception:  # noqa: BLE001
        return None
    return None


def _nyfed_target_range(timeout=20):
    """纽约联储官方 API 取联邦基金目标区间 (date, upper, lower)。

    FRED DFEDTARU/L 的唯一权威同源替代（同一 FOMC 决议值）。
    2026-11 实测：本机到 fred.stlouisfed.org 全面超时，markets.newyorkfed.org 正常。
    """
    url = "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/1.json"
    txt = _curl(url, tries=2, timeout=timeout)
    data = json.loads(txt)
    for item in data.get("refRates") or []:
        if item.get("type") == "EFFR" and item.get("targetRateTo") is not None:
            return (item.get("effectiveDate"),
                    float(item["targetRateTo"]), float(item["targetRateFrom"]))
    raise RuntimeError("NY Fed EFFR 无 targetRate 字段")


def fetch_rates():
    """R = 联邦基金目标区间（data_hub EDB → NY Fed 官方 API → FRED → 本地缓存）；
    Y2 = 两年美债（data_hub EDB → FRED DGS2 → Treasury.gov 官方收益率页）。"""
    # ---- R：目标区间上/下限 ----
    r_src = None
    r_date = r_up = r_lo = None

    hit_u = _edb_latest("US.FED.TARGET.UPPER")
    hit_l = _edb_latest("US.FED.TARGET.LOWER")
    if hit_u and hit_l:
        r_date, r_up = hit_u
        _, r_lo = hit_l
        r_src = "data_hub EDB（US.FED.TARGET.UPPER/LOWER）"

    if r_src is None:
        try:
            r_date, r_up, r_lo = _nyfed_target_range()
            r_src = "NY Fed markets API（EFFR targetRateTo/From）"
        except Exception:  # noqa: BLE001
            pass

    if r_src is None:
        try:
            r_date, r_up = _fred_csv("DFEDTARU")
            _, r_lo = _fred_csv("DFEDTARL")
            r_src = "FRED DFEDTARU/DFEDTARL"
        except RuntimeError:
            pass

    if r_src is not None:
        # 成功拿到就落本地缓存，下次源站全挂时还能降级
        try:
            with open(RATES_CACHE, "w", encoding="utf-8") as fp:
                json.dump({"r_date": r_date, "r_up": r_up, "r_lo": r_lo,
                           "source": r_src}, fp, ensure_ascii=False)
        except OSError:
            pass
    else:
        if not os.path.exists(RATES_CACHE):
            raise RuntimeError("联邦基金目标区间全部数据源不可达，且无本地缓存")
        with open(RATES_CACHE, encoding="utf-8") as fp:
            c = json.load(fp)
        r_date, r_up, r_lo = c["r_date"], c["r_up"], c["r_lo"]
        r_src = "本地缓存（%s）" % (c.get("source") or "历史值")

    # ---- Y2：两年美债 ----
    y2_src = None
    y2_date = y2 = None

    hit2 = _edb_latest("RATE.US.2Y")
    if hit2:
        y2_date, y2 = hit2
        y2_src = "data_hub EDB（RATE.US.2Y）"

    if y2_src is None:
        try:
            y2_date, y2 = _fred_csv("DGS2")
            y2_src = "FRED DGS2"
        except RuntimeError:
            y2_date, y2 = _treasury_yield_2y()
            y2_src = "Treasury.gov 日收益率页"

    return {
        "r_date": r_date, "r_up": r_up, "r_lo": r_lo,
        "r_mid": round((r_up + r_lo) / 2.0, 4),
        "r_source": r_src,
        "y2_date": y2_date, "y2": y2, "y2_source": y2_src,
    }


# ---------------- 7. 财政部回购：公告 + 结果 ----------------
def fetch_buyback_schedule():
    """Tentative-Buyback-Schedule.xml -> 未来(含近期)回购操作计划。"""
    r = _get(BUYBACK_SCHEDULE_URL, timeout=45)
    if r.status_code != 200:
        raise RuntimeError("时间表 XML -> HTTP %s" % r.status_code)
    xml = r.text

    def _field(block, name):
        m = re.search(r"<%s>\s*(.*?)\s*</%s>" % (name, name), block, re.S)
        return m.group(1).strip() if m else ""

    blocks = re.findall(r"<BuybackCalendarDate>(.*?)</BuybackCalendarDate>", xml, re.S)
    ops = []
    for b in blocks:
        op_date = _field(b, "OperationDate")
        if not op_date:
            continue
        ops.append({
            "operation_date": op_date,
            "bucket": _field(b, "PurchaseBucketName"),
            "operation_type": _field(b, "OperationType"),
            "security_type": _field(b, "SecurityType"),
            "max_size_usd": int(_field(b, "MaximumPurchaseAmountDollars") or 0),
            "announcement_date": _field(b, "AnnouncementDate"),
            "settlement_date": _field(b, "SettlementDate"),
        })
    ops.sort(key=lambda x: x["operation_date"])
    return ops


def fetch_buyback_results(since_days=45):
    """最近 since_days 天内已完成（Results 状态）的回购操作明细。"""
    if not BUYBACK_HEADERS:
        print("[buyback] 未配置 treasury.client_id/client_secret，跳过回购明细")
        return []
    now = datetime.now()
    start = (now - timedelta(days=since_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = (now + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = _get(BUYBACK_API, params={
        "operationStartDTMBegin": start,
        "operationStartDTMEnd": end,
    }, headers=BUYBACK_HEADERS, timeout=45)
    if r.status_code != 200:
        raise RuntimeError("buybacks API -> HTTP %s" % r.status_code)
    rows = r.json()
    ops = []
    for row in rows:
        if row.get("operationStatus") != "Results":
            continue
        ops.append({
            "operation_date": (row.get("operationStartDTM") or "")[:10],
            "settlement_date": row.get("settlementDT", ""),
            "operation_type": row.get("operationType", ""),
            "security_type": row.get("securityType", ""),
            "maturity_bucket": row.get("maturityBucket", ""),
            "max_size_usd": int(row.get("maxParAmountRedeemed") or 0),
            "submitted_usd": int(row.get("totalParAmountOffered") or 0),
            "accepted_usd": int(row.get("totalParAmountAccepted") or 0),
            "issues_eligible": int(row.get("numberIssuesEligible") or 0),
            "issues_accepted": int(row.get("numberIssuesAccepted") or 0),
        })
    ops.sort(key=lambda x: x["operation_date"])
    return ops


# ---------------- 8. FOMC 会议日历 ----------------
def fetch_fomc_calendar():
    """抓 FOMC 日历页，按年度区块（'YYYY FOMC Meetings'）解析已公布会议日期。

    页面结构：每个会议为 "Month" + "DD-DD"（跨两日，取第二日为实际决议日），
    年份由区块标题决定（如 '2026 FOMC Meetings'）。返回升序日期列表。
    """
    r = _get(FOMC_CALENDAR_URL, timeout=45)
    if r.status_code != 200:
        raise RuntimeError("FOMC 日历 -> HTTP %s" % r.status_code)
    html = r.text
    found = []
    blocks = list(re.finditer(r"(\d{4})\s+FOMC Meetings", html))
    for i, yr_m in enumerate(blocks):
        year = int(yr_m.group(1))
        hi = blocks[i + 1].start() if i + 1 < len(blocks) else len(html)
        seg = html[yr_m.end() - 400: hi]
        text = re.sub(r"<[^>]+>", " ", seg)
        text = text.replace("&nbsp;", " ").replace("\u2013", "-")
        for m in re.finditer(r"([A-Z][a-z]+)\s+(\d{1,2})-(\d{1,2})", text):
            mon = MONTHS.get(m.group(1))
            if not mon:
                continue
            # 排除注释中的历史日期，如 "January 25-26, 2028"
            if re.match(r",\s*20\d\d", text[m.end(): m.end() + 10]):
                continue
            day = int(m.group(3))  # 跨两日会议取第二日（实际决议日）
            try:
                found.append(date(year, mon, day))
            except ValueError:
                continue
    found = sorted(set(found))
    if not found:
        raise RuntimeError("FOMC 日历解析失败：未找到会议日期")
    return found


# ---------------- 统一入口 ----------------
def fetch_all():
    """单一入口：抓取全部美债数据，返回统一 dict。"""
    today = date.today()

    # H.4.1：当期 + 周/季/年
    h41 = {
        "current": fetch_h41_release(today),
        "wow": fetch_h41_release(today - timedelta(days=7)),
        "qoq": fetch_h41_release(today - timedelta(weeks=H41_LOOKBACK_WEEKS)),
        "yoy": fetch_h41_release(today - timedelta(weeks=52)),
    }

    # MSPD：当期 + 上月 / 去年同月
    dates = mspd_timeline()
    latest_d, prev_d = dates[0], dates[1]
    yoy_prefix = "%d-%02d" % (int(latest_d[:4]) - 1, int(latest_d[5:7]))
    yoy_d = next((x for x in dates if x.startswith(yoy_prefix)), None)
    mspd = {
        "timeline": dates,
        "current": fetch_mspd_summary(),
        "prev": fetch_mspd_summary(as_of=prev_d),
        "yoy": fetch_mspd_summary(as_of=yoy_d) if yoy_d else None,
        "compare_date": {"mom": prev_d, "yoy": yoy_d},
    }

    # 滚动偿债 / 久期：当期 + 上月 / 去年同月
    m_cur = mspd["current"]
    rollover_duration = {
        "current": fetch_rollover_duration(m_cur["record_date"], m_cur["marketable_mil"]),
        "prev": fetch_rollover_duration(prev_d, mspd["prev"]["marketable_mil"]),
        "yoy": fetch_rollover_duration(yoy_d, mspd["yoy"]["marketable_mil"]) if mspd["yoy"] else None,
    }

    # 发债结构：当月（截至今日）/ 上月整月 / 去年同月整月
    cur_s, cur_e = _month_bounds(today.year, today.month)
    pm_y, pm_m = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
    prev_s, prev_e = _month_bounds(pm_y, pm_m)
    yoy_s, yoy_e = _month_bounds(today.year - 1, today.month)
    auction_structure = {
        "current": fetch_auction_month(today, cur_s),
        "prev": fetch_auction_month(prev_e, prev_s),
        "yoy": fetch_auction_month(yoy_e, yoy_s),
    }

    # 利率 / 回购 / FOMC
    rates = fetch_rates()
    buyback = {
        "schedule": fetch_buyback_schedule(),
        "results": fetch_buyback_results(),
    }
    fomc = {"meeting_dates": [d.isoformat() for d in fetch_fomc_calendar()]}
    auction_detail = fetch_auction_detail()

    return {
        "run_date": str(today),
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "h41": h41,
        "mspd": mspd,
        "rollover_duration": rollover_duration,
        "auction_structure": auction_structure,
        "auction_detail": auction_detail,
        "rates": rates,
        "buyback": buyback,
        "fomc": fomc,
    }


def main():
    out_json = None
    if len(sys.argv) > 1 and sys.argv[1] == "--json":
        out_json = sys.argv[2] if len(sys.argv) > 2 else "treasury_data_output.json"

    today = date.today()
    data = fetch_all()
    summary = [
        "统一美债数据获取层",
        "  H.4.1     当期 %s | 周 %s | 季 %s | 年 %s" % (
            data["h41"]["current"]["release_date"], data["h41"]["wow"]["release_date"],
            data["h41"]["qoq"]["release_date"], data["h41"]["yoy"]["release_date"]),
        "  MSPD      当期 %s | 上月 %s | 去年同月 %s" % (
            data["mspd"]["current"]["record_date"], data["mspd"]["compare_date"]["mom"],
            data["mspd"]["compare_date"]["yoy"]),
        "  滚动/久期 当期 %s (rollover %.2f%%, dur %.1fm)" % (
            data["rollover_duration"]["current"]["record_date"],
            data["rollover_duration"]["current"]["rollover_12m_pct"],
            data["rollover_duration"]["current"]["avg_maturity_months"]),
        "  发债结构  %s (Bills 占比 %s%%)" % (
            data["auction_structure"]["current"]["period"],
            "%.1f" % data["auction_structure"]["current"]["bills_share_pct"]
            if data["auction_structure"]["current"]["bills_share_pct"] is not None else "-"),
        "  利率      R=%.2f%% (%s) | Y2=%.2f%% (%s)" % (
            data["rates"]["r_mid"], data["rates"]["r_source"],
            data["rates"]["y2"], data["rates"]["y2_source"]),
        "  回购      结果 %d 笔 | 未来计划 %d 场" % (
            len(data["buyback"]["results"]),
            len([u for u in data["buyback"]["schedule"]
                 if u["operation_date"] >= today.isoformat()])),
        "  拍卖明细  共 %d 条（自 %s，含 btc/得标利率/三类竞标）" % (
            data["auction_detail"]["n_records"], data["auction_detail"]["since"]),
        "  FOMC      已公布会议 %d 场，最近 %s" % (
            len(data["fomc"]["meeting_dates"]), data["fomc"]["meeting_dates"][-1]),
    ]
    print("=" * 60)
    print("\n".join(summary))
    print("=" * 60)

    if out_json:
        with open(out_json, "w", encoding="utf-8") as fp:
            json.dump(data, fp, ensure_ascii=False, indent=2)
        print("\nJSON 已落盘: %s" % out_json)


if __name__ == "__main__":
    main()