# -*- coding: utf-8 -*-
"""
日度报告基线组合统计引擎 (fail-safe)
=====================================
基期: 2026-10-24 (周六, 建仓价取最近交易日 2026-10-23 收盘)
统计止: 2026-12-31
资本分档: 指数档 10万 / 股票档 20万 / 行业档 20万, 合计 50万
口径: 逐报告口径 —— 每份报告只统计本报推荐组合自基期起的收益/最大损失/最大回撤,
      不做全局 50 万汇总。同一档位推荐多个标的时, 该档资金在标的之间等权均分。
      股票档默认空置, 待出现个股信号后再投入。

对外接口:
    stats_block(report_key, fmt="text") -> str      # 可追加到报告末尾的统计栏(异常返回 "")
    compute_report(report_key|entry, as_of=None)    # 明细 dict
    all_reports(as_of=None)                         # 全部报告明细 list
    summary(as_of=None)                             # 汇总 dict

设计原则: 任何异常都不向调用方抛出 —— 返回 "" / 空结构, 避免破坏生产报告推送。
"""
from __future__ import annotations

import os
import sys
import json
import datetime
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.path.dirname(HERE)
# 依赖目录: 沙箱内 tushare 等第三方库装在 workspace/pylibs, 报告进程未必设置 PYTHONPATH,
# 故此处兜底注入, 保证统计栏在定时任务环境中同样可用。
_PYLIBS = os.path.join(WORKSPACE, "pylibs")
if os.path.isdir(_PYLIBS) and _PYLIBS not in sys.path:
    sys.path.insert(0, _PYLIBS)
BASELINE_FILE = os.path.join(HERE, "baseline_holdings.json")
CACHE_DIR = os.path.join(HERE, "pf_cache")
TS_CONFIG_DIR = os.path.join(
    WORKSPACE, "ses_gffe5fd6b2558bffeYG74Ts057P2dLR", "strategy_repro"
)

BASE_DATE = "2026-10-24"      # 基期(周六)
END_DATE = "2026-12-31"       # 统计截止
BUCKET_LABEL = {"index": "指数档", "stock": "股票档", "industry": "行业档"}
BUCKET_ORDER = ["index", "stock", "industry"]

_http = None
_token = None
_url = None


def _http_session():
    """钉住单一连接的 HTTP 会话。

    代理网关 (ttx.dailyfetch.top) 会因『多出口 IP 同时使用』返回 ip超限；
    沙箱每次新建连接可能走不同出口 IP，故复用同一 keep-alive 连接以钉住出口 IP。
    令牌从 ts_config 读取，不在本文件落地。
    """
    global _http, _token, _url
    if _http is None:
        if TS_CONFIG_DIR not in sys.path:
            sys.path.insert(0, TS_CONFIG_DIR)
        import ts_config  # type: ignore
        import requests

        _token = ts_config.TS_TOKEN
        _url = ts_config.TS_HTTP_URL
        _http = requests.Session()
        _http.headers.update({"Connection": "keep-alive"})
    return _http


_API_BY_KIND = {
    "swidx": "sw_daily",
    "etf": "fund_daily",
    "index": "index_daily",
    "stock": "daily",
}


def _query(api: str, ts_code: str, start: str, end: str):
    s = _http_session()
    payload = {
        "api_name": api,
        "token": _token,
        "params": {"ts_code": ts_code, "start_date": start, "end_date": end},
        "fields": "ts_code,trade_date,close",
    }
    r = s.post(_url, json=payload, timeout=30)
    j = r.json()
    if j.get("code") != 0:
        raise RuntimeError("api %s: %s" % (api, str(j.get("msg"))[:80]))
    return j["data"]["items"]


# --------------------------------------------------------------------------- #
# 代码规范化与取数
# --------------------------------------------------------------------------- #
def norm_code(code: str, kind: str) -> str:
    code = str(code).strip().upper()
    if not code:
        return code
    if kind == "swidx":
        return code if code.endswith(".SI") else code.split(".")[0] + ".SI"
    if "." in code:
        return code
    head = code[0]
    if kind == "etf":
        # 1xx / 15x / 16x / 18x -> 深; 5xx -> 沪
        return code + (".SZ" if head in ("1",) else ".SH")
    if kind == "stock":
        if head == "6":
            return code + ".SH"
        if head in ("0", "3"):
            return code + ".SZ"
        if head in ("4", "8"):
            return code + ".BJ"
        return code + ".SH"
    if kind == "index":
        return code + (".SH" if code.startswith(("000", "999")) else ".SZ")
    return code


def fetch_close(kind: str, code: str, start: str, end: str) -> dict:
    """返回 {trade_date: close} (字符串日期 -> float)，失败返回 {}。

    代理网关间歇性返回『ip超限』，属可恢复的瞬时错误：会话钉住出口 IP 后仍做
    有限重试（最多 6 次，递增间隔），仍失败才返回 {}。
    """
    import time

    ts = norm_code(code, kind)
    items = None
    api = _API_BY_KIND.get(kind, "daily")
    for attempt in range(6):
        try:
            items = _query(api, ts, start, end)
            break
        except Exception:
            items = None
            if attempt < 5:
                time.sleep(min(1.5 + attempt * 1.5, 6.0))
    if not items:
        return {}
    d = {}  # type: dict
    for row in items:
        try:
            day, val = str(row[1]), row[2]
            fv = float(val)
            if fv == fv:
                d[day] = fv
        except Exception:
            continue
    return d


def load_baseline() -> dict:
    with open(BASELINE_FILE, encoding="utf-8") as f:
        return json.load(f)


def get_entry(key_or_entry):
    if isinstance(key_or_entry, dict):
        return key_or_entry
    bl = load_baseline()
    for e in bl["reports"]:
        if e["key"] == key_or_entry:
            return e
    return None


# --------------------------------------------------------------------------- #
# 组合估值与指标
# --------------------------------------------------------------------------- #
def _series_stats(value_by_date: dict, funded: float):
    """value_by_date: {date: 组合市值}; funded: 投入本金(基数)。"""
    if not value_by_date or funded <= 0:
        return None
    dates = sorted(value_by_date.keys())
    vals = [value_by_date[d] for d in dates]
    nav = [v / funded for v in vals]
    peak = nav[0]
    max_dd = 0.0
    for n in nav:
        if n > peak:
            peak = n
        dd = (peak - n) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    return {
        "base_date": dates[0],
        "last_date": dates[-1],
        "funded": funded,
        "last_value": vals[-1],
        "total_return": nav[-1] - 1.0,
        "max_loss": min(nav) - 1.0,      # 相对本金的最低点(<=0)
        "max_drawdown": max_dd,          # 峰值回撤(>=0)
        "nav": [round(n, 6) for n in nav],
        "dates": dates,
    }


def compute_report(key_or_entry, as_of=None):
    """逐报告口径计算。返回 dict; 空仓/异常时 ok=False 且字段完整。"""
    as_of = as_of or datetime.date.today().strftime("%Y-%m-%d")
    if as_of < BASE_DATE:
        as_of = datetime.date.today().strftime("%Y-%m-%d")
    entry = get_entry(key_or_entry)
    out = {
        "key": (entry or {}).get("key", str(key_or_entry)),
        "name": (entry or {}).get("name", ""),
        "ok": False,
        "funded": 0.0,
        "has_position": False,
        "buckets": {},
        "legs": [],
        "error": "",
    }
    if entry is None:
        out["error"] = "baseline entry not found"
        return out
    try:
        buckets = entry.get("buckets") or {}
        funded = 0.0
        legs = []
        cash_total = 0.0
        for bname in BUCKET_ORDER:
            b = buckets.get(bname)
            if not b:
                continue
            hs = b.get("holdings") or []
            cap = float(b.get("capital", 0.0) or 0.0)
            if cap <= 0:
                continue
            if not hs:
                out["buckets"][bname] = {
                    "capital": cap, "invested": 0.0, "status": "空仓(无多头推荐)",
                    "holdings": [],
                }
                continue
            funded += cap
            cash_total += cap * float(b.get("cash_weight", 0.0) or 0.0)
            binfo = {"capital": cap, "invested": 0.0, "status": "建仓", "holdings": []}
            for h in hs:
                w = float(h.get("weight", 0.0) or 0.0)
                amt = cap * w
                legs.append(
                    {
                        "bucket": bname,
                        "code": h["code"],
                        "name": h.get("name", ""),
                        "kind": h.get("kind", "etf"),
                        "weight": w,
                        "amount": amt,
                    }
                )
                binfo["invested"] += amt
                binfo["holdings"].append(
                    {"code": h["code"], "name": h.get("name", ""), "weight": w, "amount": amt}
                )
            out["buckets"][bname] = binfo

        if funded <= 0 or not legs:
            out["ok"] = True
            out["has_position"] = False
            out["note"] = entry.get("rule", "")
            return out

        # 取价: 从基期前最后交易日(<=2026-10-24) 到 as_of
        start = (datetime.datetime.strptime(BASE_DATE, "%Y-%m-%d") - datetime.timedelta(days=7)).strftime("%Y%m%d")
        end = as_of.replace("-", "")
        all_dates = set()
        for lg in legs:
            d = fetch_close(lg["kind"], lg["code"], start, end)
            lg["_px"] = d
            all_dates |= set(d.keys())
        if not all_dates:
            out["ok"] = False
            out["error"] = "no price data fetched"
            return out
        # 有效交易日序列: <= 基期日 + 之后
        base_day = BASE_DATE.replace("-", "")
        dates = sorted(x for x in all_dates if x <= end)
        if not dates:
            out["ok"] = False
            out["error"] = "empty date axis"
            return out

        # 建仓价: 每腿取 <= base_day 的最近收盘; 若无则取最早可得
        missing = []
        for lg in legs:
            px = lg["_px"]
            before = [d for d in px if d <= base_day]
            base_d = max(before) if before else (min(px) if px else None)
            lg["base_px_date"] = base_d
            lg["base_px"] = px.get(base_d) if base_d else None
            if not lg["base_px"]:
                missing.append("%s(%s)" % (lg["code"], lg["name"]))
            lg["shares"] = (
                lg["amount"] / lg["base_px"] if lg["base_px"] else 0.0
            )
        if missing:
            out["ok"] = False
            out["error"] = "missing price: " + ",".join(missing)
            return out
        # 价值序列(前向填充)。首个估值日 = 各腿建仓日中的最早者(通常为 2026-10-23)
        base_dates = [lg["base_px_date"] for lg in legs if lg.get("base_px_date")]
        min_base = min(base_dates) if base_dates else base_day
        value_by_date = {}
        last_px = {}
        for d in dates:
            if d < min_base:
                continue
            tot = cash_total
            for lg in legs:
                px = lg["_px"]
                if d in px:
                    last_px[id(lg)] = px[d]
                p = last_px.get(id(lg))
                if p is None:
                    # 用建仓价兜底, 避免早于个股上市日造成的空值
                    p = lg["base_px"] or 0.0
                tot += lg["shares"] * p
            value_by_date[d] = tot
        if not value_by_date:
            out["ok"] = False
            out["error"] = "no valuation dates"
            return out

        st = _series_stats(value_by_date, funded)
        out["ok"] = True
        out["has_position"] = True
        out["funded"] = funded
        out["stats"] = st
        out["legs"] = [
            {
                "bucket": lg["bucket"],
                "code": lg["code"],
                "name": lg["name"],
                "kind": lg["kind"],
                "weight": lg["weight"],
                "amount": round(lg["amount"], 2),
                "base_px_date": lg.get("base_px_date"),
                "base_px": lg.get("base_px"),
                "last_px": last_px.get(id(lg)),
                "ret": (
                    (last_px.get(id(lg), 0.0) or 0.0) / lg["base_px"] - 1.0
                    if lg.get("base_px")
                    else None
                ),
            }
            for lg in legs
        ]
        return out
    except Exception as e:  # fail-safe
        out["ok"] = False
        out["error"] = "%s: %s" % (type(e).__name__, e)
        out["trace"] = traceback.format_exc()[-500:]
        return out


def all_reports(as_of=None):
    try:
        bl = load_baseline()
        return [compute_report(e, as_of=as_of) for e in bl["reports"]]
    except Exception:
        return []


def summary(as_of=None):
    reps = all_reports(as_of=as_of)
    funded_total = sum(r.get("funded", 0.0) for r in reps)
    return {
        "base_date": BASE_DATE,
        "end_date": END_DATE,
        "as_of": as_of or datetime.date.today().strftime("%Y-%m-%d"),
        "reports": reps,
        "funded_total": funded_total,
    }


# --------------------------------------------------------------------------- #
# 报告末尾统计栏
# --------------------------------------------------------------------------- #
def _fmt_pct(x):
    return "%.2f%%" % (100.0 * x) if x is not None else "N/A"


def stats_block(report_key, fmt="text", as_of=None):
    """可安全追加到报告末尾的统计栏。任何异常返回 ""。"""
    try:
        r = compute_report(report_key, as_of=as_of)
        if not r.get("ok"):
            return ""
        name = r.get("name", "")
        if not r.get("has_position"):
            return (
                "【基线组合统计】基期 2026-10-24 · 统计至 2026-12-31\n"
                "　　本期无多头推荐 → 基线空仓, 累计收益 0.00%, 最大损失 0.00%, 最大回撤 0.00%\n"
                "　　(" + (r.get("note", "") or "本报当日无买入信号") + ")"
            )
        s = r["stats"]
        lines = [
            "【基线组合统计】基期 2026-10-24 · 统计至 2026-12-31 · 口径: 逐报告",
            "　　投入本金 %.0f 元 | 估值日 %s" % (s["funded"], s["last_date"]),
            "　　累计收益 %s | 最大损失 %s | 最大回撤 %s | 结论: %s"
            % (
                _fmt_pct(s["total_return"]),
                _fmt_pct(s["max_loss"]),
                _fmt_pct(s["max_drawdown"]),
                "盈利" if s["total_return"] > 0 else ("亏损" if s["total_return"] < 0 else "持平"),
            ),
        ]
        return "\n".join(lines)
    except Exception:
        return ""


if __name__ == "__main__":
    sm = summary()
    print(json.dumps(
        {
            "as_of": sm["as_of"],
            "base_date": sm["base_date"],
            "funded_total": sm["funded_total"],
            "reports": [
                {
                    "key": r["key"], "name": r["name"], "ok": r["ok"],
                    "has_position": r["has_position"], "funded": r["funded"],
                    "error": r.get("error", ""),
                    "stats": r.get("stats", {}),
                }
                for r in sm["reports"]
            ],
        },
        ensure_ascii=False, indent=1))