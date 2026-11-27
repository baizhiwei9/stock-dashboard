# -*- coding: utf-8 -*-
"""market_momentum 计算层 —— 只用真实存在的量，不编指标

对每只宽基（沪深300 / 中证500 / 中证1000 / 上证50 / 创业板指）算三项：

    momentum.rank   全市场宽基里的动量排名（1 = 最强），窗口 = MOM_DAYS 交易日
    momentum.score  动量分（0~100 的百分位，跨日可比，越大越强）
    momentum.turn   拐头信号：动量方向相对上一交易日翻转 → 1 / -1，未翻转 → 0

「拐头」定义（可复现，写死在代码里）
-----------------------------------
    score 的相对位置用「当前动量 / 前 MOM_DAYS 日动量」的百分比排名衡量。
    · 当日百分位 ≥ 60 且前一日 < 40  → 拐头向上  +1
    · 当日百分位 ≤ 40 且前一日 > 60  → 拐头向下  -1
    · 其余                             → 0（无信号）
    阈值取 60/40 而不是 50/50，是为了让「在 50 附近反复抖动」不产生假拐头。

口径为什么这样定
----------------
原 ``bin/industry_layer.py`` 的动量部分是「申万一级行业 5 日涨幅排名 + 资金流强度加权」，
资金流那块 DataHub 当日拿不到（见 data.py 说明）。所以这里退一步，只保留
**纯价格动量**这一个可复现、可跨日比较的口径，指标名也从「行业动量」改成
「宽基动量」，不做「看起来像原来」的假复刻。
行业维度的横向排名仍然出——放在报告的「申万一级行业动量」一栏里（只用 sw_daily
的 pct_change，不算资金流），但**不进 metrics**（那是行业不是宽基，sec 对不上）。
"""
from __future__ import annotations

import datetime
import math

MOM_DAYS = 20          # 动量窗口（交易日）
TURN_UP_HI = 60.0      # 拐头向上：当日百分位 ≥ HI
TURN_UP_LO = 40.0      # 且前一日 < LO
TURN_DN_LO = 40.0      # 拐头向下：当日百分位 ≤ LO
TURN_DN_HI = 60.0      # 且前一日 > HI


def _pct(a, b):
    if not a or not b or b <= 0:
        return None
    return (a / b - 1.0) * 100.0


def _pctile(values, x):
    """x 在 values 里的百分位（0~100）。并列按「大于等于的比例」算，越小越弱。"""
    vals = [v for v in values if v is not None]
    if x is None or len(vals) < 2:
        return None
    below = sum(1 for v in vals if v < x)
    equal = sum(1 for v in vals if v == x)
    return (below + 0.5 * equal) / len(vals) * 100.0


def series_by_code(rows):
    """日线 records → {ts_code: [(trade_date, close), ...] 升序}"""
    out = {}
    for r in rows or []:
        code = str(r.get("ts_code") or "")
        d = str(r.get("trade_date") or "")
        try:
            c = float(r.get("close"))
        except (TypeError, ValueError):
            continue
        if not code or len(d) != 8 or c <= 0:
            continue
        out.setdefault(code, []).append((d, c))
    for code in out:
        out[code].sort()
    return out


def momentum_series(closes, days=MOM_DAYS):
    """逐日算「近 days 日涨幅」（%）。返回 [(trade_date, mom)]，长度 = 收盘序列 - days。"""
    out = []
    for i in range(days, len(closes)):
        d, c = closes[i]
        _d0, c0 = closes[i - days]
        out.append((d, _pct(c, c0)))
    return out


def compute(indices_rows, secs):
    """返回 {sec: {name, close, mom, rank, score, turn, history}}。

    secs: [(code, name)] —— 顺序即报告里的展示顺序。
    """
    ser = series_by_code(indices_rows)
    per = {}
    for code, name in secs:
        closes = ser.get(code) or []
        hist = momentum_series(closes)
        mom = hist[-1][1] if hist else None
        prev = hist[-2][1] if len(hist) >= 2 else None
        per[code] = {
            "code": code, "name": name,
            "close": (closes[-1][1] if closes else None),
            "last_date": (closes[-1][0] if closes else ""),
            "mom": mom, "prev_mom": prev,
            "history": hist[-10:],
            "n_bars": len(closes),
        }
    # 横截面排名（当日）
    today = {c: v["mom"] for c, v in per.items() if v["mom"] is not None}
    desc = sorted(today.items(), key=lambda kv: -kv[1])
    for i, (code, _m) in enumerate(desc, 1):
        per[code]["rank"] = i
    n = len(today)
    for code, v in per.items():
        v.setdefault("rank", None)
        v["n_ranked"] = n

    # 百分位分 + 拐头：用「当日动量在近 n 日横截面分布中的位置」
    # 每个交易日的横截面 = 当天所有宽基的动量，构成一个分布；再算当日值的分位。
    by_date = {}
    for code, v in per.items():
        for d, m in v["history"]:
            if m is not None:
                by_date.setdefault(d, []).append(m)
    for code, v in per.items():
        h = v["history"]
        scores = []
        for d, m in h:
            pool = by_date.get(d) or []
            scores.append((d, _pctile(pool, m)))
        v["score_hist"] = scores
        v["score"] = scores[-1][1] if scores else None
        prev_score = scores[-2][1] if len(scores) >= 2 else None
        v["prev_score"] = prev_score
        turn = 0
        if v["score"] is not None and prev_score is not None:
            if v["score"] >= TURN_UP_HI and prev_score < TURN_UP_LO:
                turn = 1
            elif v["score"] <= TURN_DN_LO and prev_score > TURN_DN_HI:
                turn = -1
        v["turn"] = turn
    return per


def sw_ranking(sw_rows, topn=10):
    """申万一级行业动量榜（近 MOM_DAYS 交易日涨幅 / 当日涨幅），只读 sw_daily。

    返回 {"date":..., "by_mom": [...], "by_day": [...]}；数据不足返回 {"error": ...}。
    """
    if not sw_rows:
        return {"error": "sw_daily 无数据"}
    by_code = {}
    for r in sw_rows:
        code = str(r.get("ts_code") or "")
        d = str(r.get("trade_date") or "")
        if not code or len(d) != 8:
            continue
        try:
            close = float(r.get("close"))
        except (TypeError, ValueError):
            continue
        if close <= 0:
            continue
        by_code.setdefault(code, {"name": str(r.get("name") or code), "rows": {}})["rows"][d] = (
            close, r.get("pct_change"))
    if not by_code:
        return {"error": "sw_daily 无有效行"}

    all_dates = sorted({d for v in by_code.values() for d in v["rows"]})
    if not all_dates:
        return {"error": "sw_daily 无有效交易日"}
    last = all_dates[-1]
    base_idx = max(0, len(all_dates) - 1 - MOM_DAYS)
    base = all_dates[base_idx]

    items = []
    for code, v in by_code.items():
        rows = v["rows"]
        if last not in rows:
            continue
        c1 = rows[last][0]
        c0 = rows.get(base, (None,))[0]
        if not c0 or c0 <= 0:
            continue
        try:
            day_chg = float(rows[last][1])
        except (TypeError, ValueError):
            day_chg = None
        items.append({"code": code, "name": v["name"],
                      "mom": _pct(c1, c0), "day": day_chg,
                      "window": MOM_DAYS})
    if not items:
        return {"error": "sw_daily 有效行业为 0"}
    by_mom = sorted([x for x in items if x["mom"] is not None],
                    key=lambda x: -x["mom"])
    by_day = sorted([x for x in items if x["day"] is not None],
                    key=lambda x: -x["day"])
    return {"date": last, "base_date": base, "n": len(items),
            "by_mom": by_mom[:topn], "by_day": by_day[:topn],
            "tail_mom": by_mom[-topn:] if len(by_mom) > topn else []}


def market_env(indices_rows):
    """两市成交额与当日涨跌（只读指数日线，不碰个股）。"""
    ser = series_by_code(indices_rows)
    sh = ser.get("000001.SH") or []
    sz = ser.get("399106.SZ") or []
    out = {}
    if len(sh) >= 6:
        amt = {}
        for r in indices_rows or []:
            if str(r.get("ts_code")) in ("000001.SH", "399106.SZ"):
                try:
                    amt[(str(r.get("ts_code")), str(r.get("trade_date")))] = float(r.get("amount"))
                except (TypeError, ValueError):
                    pass
        last = sh[-1][0]
        prev4 = [d for d, _c in sh[-5:-1]]
        def total(day):
            a = amt.get(("000001.SH", day))
            b = amt.get(("399106.SZ", day))
            if a is None:
                return None
            return (a + (b or 0.0)) / 1e5      # 千元 → 亿元
        cur = total(last)
        hists = [total(d) for d in prev4]
        hists = [x for x in hists if x]
        out["amt_total"] = cur
        out["amt_prev4_avg"] = (sum(hists) / len(hists)) if hists else None
        out["amt_ratio"] = (cur / out["amt_prev4_avg"]) if (cur and out["amt_prev4_avg"]) else None
        out["date"] = last
    # 宽基当日涨跌
    for code, name in (("000001.SH", "上证指数"), ("399001.SZ", "深证成指")):
        s = ser.get(code) or []
        if len(s) >= 2:
            out.setdefault("pct", {})[name] = _pct(s[-1][1], s[-2][1])
    return out
