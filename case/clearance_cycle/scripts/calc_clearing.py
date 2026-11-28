#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""出清周期监控：三口径合算

A. 被动化率 = 宽基ETF总规模 / 沪深300自由流通市值（土狗口径）
   4.9% 起计时 → 7.0% 发提醒（出清进度基本完成）→ 历史区间 4.9%~8.5%
B. 基差年化率（现货-期货，按到期日年化）
   正常区间 4%~5%；距 5 日高点回落 ≥5 个百分点 = 基差出清完成
C. 情绪低点（原出清周期口径）：成交额分位 / 融资去杠杆 / 涨跌停结构 / 波动率分位
"""
import json
import os
from datetime import date, datetime

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "output", "data")

PASSIVE_START = 4.9      # 起计时线
PASSIVE_ALERT = 7.0      # 邮件提醒线
PASSIVE_TOP = 8.5        # 历史区间上沿
BASIS_NORMAL = (4.0, 5.0)
BASIS_DROP = 5.0         # 距5日高点回落（百分点）

# 交易所代码 → 中文名：降级说明会直接进邮件/报告正文，不能出现裸 sse/szse
EX_NAME = {"sse": "上交所", "szse": "深交所"}


def _load(name):
    p = os.path.join(DATA, name)
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def pct_rank(series, value):
    s = pd.Series(series).dropna()
    if not len(s) or value is None:
        return None
    return round(float((s <= value).mean() * 100), 1)


def calc_passive(etf, hs300_recs):
    """被动化率：当前 + 沪市份额回溯近似历史"""
    wide_yi = etf["wide_total_yi"]
    # 分子缺一边交易所时读数偏低约 2pp，标 reliable=False：
    # 告警与报告据此拒绝做 7% 判定，宁可显示「数据不全」也不给错信号。
    degraded = bool(etf.get("degraded"))
    missing_ex = list(etf.get("missing_exchanges") or [])
    missing_names = [EX_NAME.get(k, k) for k in missing_ex]
    # 分母：沪深300自由流通市值（Wind 21.2334 万亿，2026-11-27）
    denom_yi = 212334.0
    cur = wide_yi / denom_yi * 100

    # 历史：用沪深300点位比例推算分母，用沪市份额比例推算分子（深市固定）
    hs = pd.DataFrame(hs300_recs)
    hs["date"] = hs["date"].astype(str)
    hs = hs.sort_values("date")
    cur_close = float(hs["close"].iloc[-1])
    ratio_denom = lambda c: denom_yi * c / cur_close  # noqa: E731
    # 土狗锚点校准：以其 11/27 读数 7.83% 反推其分母，便于与原文刻度对齐。
    # 宽度口径取不到（wide_yi=0，多为 ETF 规模源不可用）时分母为 0，直接除会
    # ZeroDivisionError 把整个 render 打挂 —— 退化成 0 并保留 degraded 标记，
    # 下游仍拿到数值可格式化，报告按「数据不全」呈现。
    denom_dog = (wide_yi / 0.0783) if wide_yi else 0.0

    hist_path = os.path.join(DATA, "passive_history.csv")
    hist = []
    if os.path.exists(hist_path):
        h = pd.read_csv(hist_path)
        for _, r in h.iterrows():
            denom = ratio_denom(float(r["hs300_close"]))
            hist.append({
                "date": str(r["date"]),
                "wide_yi": round(float(r["wide_yi"]), 1),
                "pct": round(float(r["wide_yi"]) / denom * 100, 2) if denom else 0.0,
                "pct_dog": (round(float(r["wide_yi"]) / denom_dog * 100, 2)
                            if denom_dog else 0.0),
            })

    status = "未起计时"
    if degraded:
        status = f"数据不全（缺 {'/'.join(missing_names)}），不做 7% 判定"
    elif cur >= PASSIVE_ALERT:
        status = "已过提醒线（出清基本完成）"
    elif cur >= PASSIVE_START:
        status = "计时中（出清进行中）"
    return {
        "value_pct": round(cur, 2),
        "reliable": not degraded,
        "degraded": degraded,
        "missing_exchanges": missing_ex,
        "missing_names": missing_names,
        "wide_scale_yi": wide_yi,
        "denom_yi": denom_yi,
        "denom_note": "沪深300自由流通市值（Wind 21.2334万亿，2026-11-27）",
        "lines": {"start": PASSIVE_START, "alert": PASSIVE_ALERT, "top": PASSIVE_TOP},
        "status": status,
        "progress_pct": round(min(100, max(0, (cur - PASSIVE_START) / (PASSIVE_TOP - PASSIVE_START) * 100)), 1),
        "gap_to_alert_pp": round(cur - PASSIVE_ALERT, 2),
        "value_pct_dog": round(wide_yi / denom_dog * 100, 2) if denom_dog else 0.0,
        "denom_dog_yi": round(denom_dog, 0),
        "dog_calibrated": bool(denom_dog),
        "by_cat_yi": etf["by_cat_yi"],
        "history": hist,
        "tuogou_anchor": {"date": "2026-11-27", "pct": 7.83, "note": "土狗原文 Vol.261128 读数，其自由流通市值口径略小于 Wind"},
    }


def calc_basis(basis):
    """基差：各品种远季合约年化 + 距5日高点回落 + 250日分位"""
    out = {}
    for v, hist in basis.get("history", {}).items():
        h = pd.DataFrame(hist)
        if not len(h):
            continue
        q = h[h["is_quarter"]].copy()
        if not len(q):
            q = h.copy()
        # 每日取「远季合约」（剩余 60~150 天，即土狗口径的 IF2703 类合约）；
        # 年化基差在近月临近到期时会被天数分母放大失真，故不用当季
        def pick_quarter(g):
            g = g.sort_values("days_left")
            mid = g[(g["days_left"] >= 60) & (g["days_left"] <= 150)]
            if len(mid):
                return mid.iloc[-1]
            return g.iloc[-1]
        daily = q.groupby("date").apply(pick_quarter, include_groups=False).reset_index()
        daily = daily.sort_values("date")
        cur = daily.iloc[-1]
        last5 = daily.tail(5)
        hi5 = float(last5["annual_pct"].max())
        hi5_date = str(last5.loc[last5["annual_pct"].idxmax(), "date"])
        drop = hi5 - float(cur["annual_pct"])
        win250 = daily["annual_pct"].tail(250)
        out[v] = {
            "contract": cur["contract"],
            "date": str(cur["date"]),
            "spot": float(cur["spot"]),
            "fut": float(cur["fut"]),
            "basis": float(cur["basis"]),
            "days_left": int(cur["days_left"]),
            "annual_pct": round(float(cur["annual_pct"]), 2),
            "high5": round(hi5, 2),
            "high5_date": hi5_date,
            "drop_pp": round(drop, 2),
            "triggered": bool(drop >= BASIS_DROP),
            "normal_range": list(BASIS_NORMAL),
            "in_normal": bool(BASIS_NORMAL[0] <= float(cur["annual_pct"]) <= BASIS_NORMAL[1]),
            "pct_rank_250d": pct_rank(win250, float(cur["annual_pct"])),
            "pct_rank_40d": pct_rank(daily["annual_pct"].tail(40), float(cur["annual_pct"])),
            "hist_days": int(len(win250)),
            "series": [{"date": str(r["date"]), "annual_pct": round(float(r["annual_pct"]), 2)} for _, r in daily.tail(60).iterrows()],
            "contracts_today": [
                {k: c[k] for k in ["contract", "fut", "basis", "days_left", "annual_pct"]}
                for c in basis["varieties"][v]["contracts"]
            ],
        }
    return out


def calc_sentiment(sent):
    hs = pd.DataFrame(sent["hs300"]).sort_values("date")
    hs["amount_yi"] = pd.to_numeric(hs["amount_yi"], errors="coerce")
    # 实时补的当日 bar 若成交额取不到（日线兜底源无成交额），退回最近一个有效值，
    # 并在输出里标明「成交额为上一交易日口径」，不把 NaN 当成 0 参与分位。
    amt_valid = hs["amount_yi"].dropna()
    if len(amt_valid):
        cur_amount = float(amt_valid.iloc[-1])
        amount_rank = pct_rank(amt_valid.tail(250), cur_amount)
        amount_stale = bool(pd.isna(hs["amount_yi"].iloc[-1]))
        amount_date = str(hs.loc[amt_valid.index[-1], "date"])
    else:
        cur_amount, amount_rank, amount_stale, amount_date = None, None, False, None

    close = pd.to_numeric(hs["close"], errors="coerce")
    high250 = float(close.tail(250).max())
    drawdown = (1 - float(close.iloc[-1]) / high250) * 100

    mg = pd.DataFrame(sent.get("margin") or [])
    mg_info = {}
    if len(mg):
        mg["fin_bal_yi"] = pd.to_numeric(mg["fin_bal_yi"], errors="coerce")
        mg = mg.sort_values("date")
        cur_fin = float(mg["fin_bal_yi"].iloc[-1])
        chg20 = (cur_fin / float(mg["fin_bal_yi"].iloc[-21]) - 1) * 100 if len(mg) > 21 else None
        mg_info = {
            "date": str(mg["date"].iloc[-1]),
            "fin_bal_yi": round(cur_fin, 0),
            "chg20_pct": round(chg20, 2) if chg20 is not None else None,
            "maint_ratio": round(float(mg["ratio"].iloc[-1]), 1) if "ratio" in mg else None,
        }

    qv = pd.DataFrame(sent.get("qvix") or [])
    qv_info = {}
    if len(qv):
        qv["close"] = pd.to_numeric(qv["close"], errors="coerce")
        qv_info = {
            "date": str(qv["date"].iloc[-1]),
            "value": round(float(qv["close"].iloc[-1]), 2),
            "rank_250": pct_rank(qv["close"].tail(250), float(qv["close"].iloc[-1])),
        }

    br = sent.get("breadth") or {}
    chg_last = pd.to_numeric(hs["chg_pct"], errors="coerce").iloc[-1]
    return {
        "as_of": str(hs["date"].iloc[-1]),
        "amount_yi": round(cur_amount, 1) if cur_amount is not None else None,
        "amount_rank_250": amount_rank,
        "amount_date": amount_date,
        "amount_is_stale": amount_stale,
        "drawdown_from_250d_high_pct": round(drawdown, 2),
        "margin": mg_info,
        "qvix": qv_info,
        "breadth": br,
        "hs300_close": round(float(close.iloc[-1]), 2),
        "hs300_chg_pct": round(float(chg_last), 2) if pd.notna(chg_last) else None,
    }


def main():
    etf = _load("etf_scale.json")
    basis = _load("basis.json")
    sent = _load("sentiment.json")

    passive = calc_passive(etf, sent["hs300"])
    basis_r = calc_basis(basis)
    sentiment = calc_sentiment(sent)

    done_flags = {
        # 数据不全时不做 7% 判定，避免把偏低读数当成「未过线」计入完成项
        "被动化率≥7%": bool(passive.get("reliable", True)) and passive["value_pct"] >= PASSIVE_ALERT,
        "基差回落≥5pp": any(v["triggered"] for v in basis_r.values()),
        "情绪低点已过": (sentiment["breadth"].get("median_chg") or 0) > 0,
    }
    out = {
        "as_of": basis.get("as_of"),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "passive": passive,
        "basis": basis_r,
        "sentiment": sentiment,
        "done_flags": done_flags,
        "done_count": int(sum(done_flags.values())),
    }
    with open(os.path.join(DATA, "clearing.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(json.dumps({k: out[k] for k in ["as_of", "done_flags", "done_count"]}, ensure_ascii=False, indent=2))
    print("被动化率", passive["value_pct"], "%", passive["status"])
    for v, b in basis_r.items():
        print(f"基差 {v} {b['contract']} 年化{b['annual_pct']}% 5日高{b['high5']}% 回落{b['drop_pp']}pp 250日分位{b.get('pct_rank_250d')}% 触发={b['triggered']}")
    return out


if __name__ == "__main__":
    main()
