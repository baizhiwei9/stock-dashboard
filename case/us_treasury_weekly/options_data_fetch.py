#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美债 ETF 期权数据获取层（每周变化报告 - 持仓量分布数据源）
============================================================
抓取四只美债相关 ETF 的期权链与持仓量分布，落盘 options_data_output.json；
同时按日追加 OI 历史快照 options_oi_history.json（供近一年/近一月 2σ、80 分位、
20 日 95 分位等统计口径使用）。

监控对象：
  TLT  （主监控，20Y+ 长债）
  TLH  （10-20Y 档辅助，次长）
  IEF  （7-10Y 档辅助，中长）
  XLU  （利率敏感公用事业股，验证买盘是否外溢）

数据域（每个标的一份）：
  chain            期权链（expiry/strike/call put 双侧 vol/OI/bid/ask）
  distrib_expiry   按到期月的 OI 分布（call/put/total）
  distrib_strike   按行权价区间的 OI 分布
  top_strike       主力行权价（call/put OI 最大行权价）
  top_contract     最大 OI 合约
  summary          总 call/put vol、OI、pcr(vol/oi)、30 日均量
  iv_stats         ATM IV / 全链 IV 中位数（BSM 反推）

数据源：
  - NASDAQ 期权链  https://api.nasdaq.com/api/quote/{SYM}/option-chain
  - NASDAQ 报价摘要 https://api.nasdaq.com/api/quote/{SYM}/summary（标的价格/股息率/均量）
  - 新浪美股日K    https://stock.finance.sina.com.cn/usstock/api/jsonp.php/.../US_MinKService.getDailyK
  - Treasury.gov 收益率页（10Y/30Y）

IV 口径：由于免费期权链不含 IV 字段，用 BSM 反推（期权中间价、标的价格、
无风险利率=2Y、股息率=NASDAQ Yield）；仅对存在有效 bid/ask 的合约反推，
深度虚值/零价合并跳过，反推失败返回 null。

用法：
  python3 options_data_fetch.py [--json options_data_output.json]
"""
import json
import math
import os
import re
import sys
import time
from datetime import date, datetime, timedelta

import requests

SYMBOLS = ("TLT", "TLH", "IEF", "XLU")
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
}
NASDAQ_CHAIN = ("https://api.nasdaq.com/api/quote/{sym}/option-chain"
                "?assetclass=etf&limit=9999&fromdate={d0}&todate={d1}"
                "&excode=oprac&callput=call&money=all")
NASDAQ_SUMMARY = "https://api.nasdaq.com/api/quote/{sym}/summary?assetclass=etf"
SINA_DAILY_K = ("https://stock.finance.sina.com.cn/usstock/api/jsonp.php/"
                "var%20_=/US_MinKService.getDailyK?symbol={sym}")
TREASURY_YIELD_URL = ("https://home.treasury.gov/resource-center/data-chart-center/"
                      "interest-rates/TextView?type=daily_treasury_yield_curve"
                      "&field_tdr_date_value={year}")

HIST_FILE = "options_oi_history.json"
RATES_CACHE = "rate_fedfunds_cache.json"


def _get(url, timeout=60, retries=3):
    last = None
    for i in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            if r.status_code < 500:
                return r
            last = "HTTP %s" % r.status_code
        except Exception as exc:  # noqa: BLE001
            last = str(exc)
        time.sleep(1 + i)
    raise RuntimeError("请求失败 %s | %s" % (url, last))


def _num(v):
    """'1,234' / '$77.87' / '--' -> float/None"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").replace("$", "").strip()
    if s in ("", "--", "N/A", "null"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_expiry(expirygroup, expiry_short):
    """把 'Nov 20' + 组标题凑成完整日期（组标题年份优先，其次当前/次年）。"""
    m = re.search(r"([A-Za-z]{3,9})\s+(\d{1,2}),\s*(\d{4})", expirygroup or "")
    if m:
        return datetime.strptime("%s %s %s" % (m.group(1), m.group(2), m.group(3)),
                                 "%B %d %Y").date()
    m2 = re.search(r"([A-Za-z]{3})\s+(\d{1,2})", expiry_short or "")
    if not m2:
        return None
    cur = date.today()
    try:
        d = datetime.strptime("%s %d %d" % (m2.group(1), int(m2.group(2)), cur.year),
                              "%b %d %Y").date()
    except ValueError:
        return None
    return d if d >= date.today() - timedelta(days=2) else d.replace(year=cur.year + 1)


def _rmse_price(iv, *args):
    """BSM 看涨期权价格（内部函数）。"""
    s, k, t, r, q, target = args
    if iv <= 0:
        return 1e9
    d1 = (math.log(s / k) + (r - q + 0.5 * iv * iv) * t) / (iv * math.sqrt(t))
    d2 = d1 - iv * math.sqrt(t)
    price = s * math.exp(-q * t) * _norm(d1) - k * math.exp(-r * t) * _norm(d2)
    return abs(price - target)


def _norm(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bsm_iv(s, k, t, r, q, mid):
    """二分法反推看涨 IV；输入无效返回 None。"""
    if not all(v is not None and v > 0 for v in (s, k, t, r, mid)):
        return None
    if 0.02 < (mid - math.exp(-r * t) * k) / (s * math.exp(-q * t)) > 0.98:
        return None  # 深度实值/套利失真，跳过
    lo, hi, best = 1e-4, 5.0, None
    for _ in range(80):
        mid_v = (lo + hi) / 2
        try:
            p = _bsm_price(s, k, t, r, q, mid_v)
        except Exception:  # noqa: BLE001
            return None
        if abs(p - mid) < 1e-5:
            best = mid_v
            break
        if p > mid:
            hi = mid_v
        else:
            lo = mid_v
    if best is None:
        try:
            p_hi = _bsm_price(s, k, t, r, q, 5.0)
            best = 5.0 if p_hi < mid else None
        except Exception:  # noqa: BLE001
            best = None
    return best


def _bsm_price(s, k, t, r, q, iv):
    d1 = (math.log(s / k) + (r - q + 0.5 * iv * iv) * t) / (iv * math.sqrt(t))
    d2 = d1 - iv * math.sqrt(t)
    return s * math.exp(-q * t) * _norm(d1) - k * math.exp(-r * t) * _norm(d2)


def fetch_chain(sym):
    """NASDAQ 期权链：一次请求拿全到期日的 call/put 双侧数据。"""
    today = date.today()
    url = NASDAQ_CHAIN.format(sym=sym, d0=today.isoformat(), d1=(today.replace(year=today.year + 1)).isoformat())
    data = _get(url).json()
    table = (data.get("data") or {}).get("table") or {}
    rows = table.get("rows") or []
    contracts, expiry_cur = [], None
    for row in rows:
        if row.get("expirygroup"):
            expiry_cur = row["expirygroup"]
        short = row.get("expiryDate")
        strike = _num(row.get("strike"))
        if strike is None:
            continue
        expiry = _parse_expiry(expiry_cur, short)
        cb, ca = _num(row.get("c_Bid")), _num(row.get("c_Ask"))
        c_mid = (cb + ca) / 2 if cb is not None and ca is not None else None
        pb, pa = _num(row.get("p_Bid")), _num(row.get("p_Ask"))
        p_mid = (pb + pa) / 2 if pb is not None and pa is not None else None
        contracts.append({
            "expiry": expiry.isoformat() if expiry else None,
            "strike": strike,
            "c_vol": _num(row.get("c_Volume")) or 0,
            "c_oi": _num(row.get("c_Openinterest")) or 0,
            "c_mid": c_mid,
            "p_vol": _num(row.get("p_Volume")) or 0,
            "p_oi": _num(row.get("p_Openinterest")) or 0,
            "p_mid": p_mid,
        })
    return contracts


def fetch_summary(sym):
    d = _get(NASDAQ_SUMMARY.format(sym=sym)).json()
    s = (d.get("data") or {}).get("summaryData") or {}
    out = {}
    for k in ("PreviousClose", "Yield", "AvgDailyVol20Days", "AvgDailyVol65Days"):
        v = s.get(k)
        out[k] = _num(v.get("value") if isinstance(v, dict) else v)
    return out


def fetch_sina_daily_k(sym):
    """新浪美股日K：返回 [(date, vol), ...]，用于 30 日均量。"""
    r = _get(SINA_DAILY_K.format(sym=sym))
    txt = r.text
    m = re.search(r"\((\[.*\])\)", txt, re.S)
    if not m:
        raise RuntimeError("新浪日K 解析失败: %s" % sym)
    arr = json.loads(m.group(1))
    return [(row["d"], float(row["v"])) for row in arr if row.get("v")]


def fetch_yields():
    """Treasury.gov 日收益率页：返回 (date, 2Y, 10Y, 30Y)。"""
    html = _get(TREASURY_YIELD_URL.format(year=date.today().year)).text
    for r in reversed(re.split(r"<tr[^>]*>", html)):
        dm = re.search(r'datetime="(20\d\d-\d\d-\d\d)T', r)
        if not dm:
            continue
        vals = {}
        for tag in ("2year", "10year", "30year"):
            m = re.search(r'class="[^"]*field-bc-%s[^"]*"[^>]*>([^<]+)<' % tag, r)
            if m:
                v = m.group(1).strip()
                if v not in ("N/A", "") and re.match(r"^\d", v):
                    vals[tag] = float(v)
        if "10year" in vals and "30year" in vals:
            return dm.group(1), vals.get("2year"), vals["10year"], vals["30year"]
    raise RuntimeError("Treasury.gov 收益率页无有效 10Y/30Y 观测")


def _distributions(contracts):
    """按到期月 / 行权价分档统计 OI；主力行权价与最大 OI 合约。"""
    exp = {}
    strike = {}
    total_c_oi = total_p_oi = total_c_vol = total_p_vol = 0
    for c in contracts:
        total_c_oi += c["c_oi"]
        total_p_oi += c["p_oi"]
        total_c_vol += c["c_vol"]
        total_p_vol += c["p_vol"]
        if c["expiry"]:
            key = c["expiry"][:7]
            e = exp.setdefault(key, {"call_oi": 0, "put_oi": 0})
            e["call_oi"] += c["c_oi"]
            e["put_oi"] += c["p_oi"]
        sk = c["strike"]
        st = strike.setdefault(sk, {"call_oi": 0, "put_oi": 0})
        st["call_oi"] += c["c_oi"]
        st["put_oi"] += c["p_oi"]
    exp_dist = sorted(
        [{"month": k, "call_oi": v["call_oi"], "put_oi": v["put_oi"],
          "total_oi": v["call_oi"] + v["put_oi"]} for k, v in exp.items()],
        key=lambda x: x["month"])
    strike_dist = sorted(
        [{"strike": k, "call_oi": v["call_oi"], "put_oi": v["put_oi"],
          "total_oi": v["call_oi"] + v["put_oi"]} for k, v in strike.items()],
        key=lambda x: -x["total_oi"])[:12]

    call_top = sorted((c for c in contracts if c["c_oi"] > 0), key=lambda c: -c["c_oi"])
    put_top = sorted((c for c in contracts if c["p_oi"] > 0), key=lambda c: -c["p_oi"])
    call_strike = {c["strike"] for c in call_top[:1]}
    top = {
        "call_top_strike": call_top[0]["strike"] if call_top else None,
        "call_top_oi": call_top[0]["c_oi"] if call_top else 0,
        "put_top_strike": put_top[0]["strike"] if put_top else None,
        "put_top_oi": put_top[0]["p_oi"] if put_top else 0,
        "call_top_contract": (call_top[0]["expiry"], call_top[0]["strike"]) if call_top else None,
        "put_top_contract": (put_top[0]["expiry"], put_top[0]["strike"]) if put_top else None,
    }
    return {
        "distrib_expiry": exp_dist,
        "distrib_strike": strike_dist,
        "top": top,
        "totals": {"call_vol": total_c_vol, "put_vol": total_p_vol,
                   "call_oi": total_c_oi, "put_oi": total_p_oi,
                   "pcr_vol": round(total_p_vol / total_c_vol, 3) if total_c_vol else None,
                   "pcr_oi": round(total_p_oi / total_c_oi, 3) if total_c_oi else None},
    }


def _iv_stats(contracts, s, r, q=0.0):
    """BSM 反推 IV：ATM 区 IV 与全链 IV 中位数（有效 bid/ask 合约）。"""
    if not s:
        return {"atm_iv": None, "median_iv": None, "n_valid": 0}
    today = date.today()
    ivs = []
    for c in contracts:
        if not c["c_mid"] or not c["expiry"] or c["strike"] <= 0:
            continue
        tt = (datetime.strptime(c["expiry"], "%Y-%m-%d").date() - today).days / 365.0
        if tt <= 0.01:
            continue
        iv = bsm_iv(s, c["strike"], tt, r, q, c["c_mid"])
        if iv and 0.03 < iv < 3.0:
            ivs.append((c["strike"], iv))
    if not ivs:
        return {"atm_iv": None, "median_iv": None, "n_valid": 0}
    atm = min(ivs, key=lambda x: abs(x[0] - s))
    med = sorted(iv for _, iv in ivs)[len(ivs) // 2]
    return {"atm_iv": round(atm[1], 3), "median_iv": round(med, 3), "n_valid": len(ivs)}


def load_or_init_history():
    if os.path.exists(HIST_FILE):
        with open(HIST_FILE, encoding="utf-8") as fp:
            return json.load(fp)
    return {"_meta": {"note": "OI 历史快照：按交易日追加，供 2σ/分位统计"},
            "snapshots": []}


def main():
    out_json = sys.argv[2] if len(sys.argv) > 2 and sys.argv[1] == "--json" else "options_data_output.json"
    today = date.today()
    r = 0.0475  # 2Y 无风险利率近似（Treasury.gov 抓取，若成功则覆盖）
    try:
        ydate, y2, y10, y30 = fetch_yields()
        if y2:
            r = y2 / 100.0
        yields = {"date": ydate, "y2": y2, "y10": y10, "y30": y30, "source": "Treasury.gov"}
        print("  收益率   %s  2Y=%s 10Y=%s 30Y=%s" % (ydate, y2, y10, y30))
    except RuntimeError as exc:
        yields = {"date": None, "y2": None, "y10": None, "y30": None, "source": "Treasury.gov 暂不可达"}
        print("  ⚠ 收益率抓取失败: %s" % exc)

    symbols = {}
    stats_line = []
    for sym in SYMBOLS:
        chain = fetch_chain(sym)
        try:
            summary = fetch_summary(sym)
        except RuntimeError:
            summary = {}
        s = summary.get("PreviousClose")
        yield_pct = summary.get("Yield")
        kdata = []
        try:
            kdata = fetch_sina_daily_k(sym)
            vol30 = round(sum(k[1] for k in kdata[-30:]) / max(1, len(kdata[-30:])), 0) if kdata else None
        except RuntimeError:
            vol30 = None
        dist = _distributions(chain)

        ivs = _iv_stats(chain, s, r, q=(yield_pct or 0) / 100.0)
        sym_out = {
            "as_of": kdata[-1][0] if kdata else today.isoformat(),
            "summary": {
                "last": s,
                "dividend_yield": yield_pct,
                "avg_vol_30d": vol30,
                "avg_vol_20d": summary.get("AvgDailyVol20Days"),
                "avg_vol_65d": summary.get("AvgDailyVol65Days"),
            },
            **dist,
            "iv_stats": ivs,
            "n_contracts": len(chain),
            "chain": chain,
        }
        symbols[sym] = sym_out
        t = sym_out["totals"]
        print("  %-4s 到期日%3d档  C_vol=%6d P_vol=%6d PCR_v=%.2f  C_oi=%9d P_oi=%9d"
              % (sym, len(chain), t["call_vol"], t["put_vol"], t["pcr_vol"] or 0,
                 t["call_oi"], t["put_oi"]))

    data = {
        "run_date": today.isoformat(),
        "run_week": "%s-W%s" % (today.isocalendar()[0], today.isocalendar()[1]),
        "yields": yields,
        "symbols": symbols,
    }
    with open(out_json, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)

    # 追加 OI 历史快照（按交易日本日去重）
    hist = load_or_init_history()
    as_of = symbols["TLT"]["as_of"]
    existing = {s["date"] for s in hist.get("snapshots", [])}
    if as_of not in existing:
        snap = {"date": as_of, "symbols": {}}
        for sym, so in symbols.items():
            snap["symbols"][sym] = {
                "call_vol": so["totals"]["call_vol"],
                "put_vol": so["totals"]["put_vol"],
                "call_oi": so["totals"]["call_oi"],
                "put_oi": so["totals"]["put_oi"],
                "pcr_vol": so["totals"]["pcr_vol"],
                "pcr_oi": so["totals"]["pcr_oi"],
                "avg_vol_30d": so["summary"]["avg_vol_30d"],
                "last": so["summary"]["last"],
                "top_strike": so["top"]["call_top_strike"],
                "top_oi": so["top"]["call_top_oi"],
            }
        hist.setdefault("snapshots", []).append(snap)
        hist["snapshots"] = sorted(hist["snapshots"], key=lambda x: x["date"])
        with open(HIST_FILE, "w", encoding="utf-8") as fp:
            json.dump(hist, fp, ensure_ascii=False, indent=2)
    print("JSON 已落盘: %s | OI 历史快照 %d 个交易日" % (out_json, len(hist.get("snapshots", []))))


if __name__ == "__main__":
    main()