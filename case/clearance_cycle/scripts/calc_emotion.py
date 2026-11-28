#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""情绪周期判定（独立模块，不再作为出清完成信号）

情绪温度 heat（0-100）由五维合成：
  1) 市场宽度（涨停家数）      2) 个股涨跌中位数
  3) 量能（沪深300成交额250日分位）  4) 杠杆（融资余额20日变化）
  5) 波动（QVIX 250日分位，高波动=恐慌，反向计分）
输出 output/data/emotion.json
"""
import json
import os
from datetime import datetime

import pandas as pd

from calc_clearing import DATA, _load, calc_sentiment, pct_rank

STAGES = [
    (70, "过热", "情绪高潮，追高赔率最差", "red"),
    (55, "亢奋", "赚钱效应扩散，但已非低吸区", "yellow"),
    (40, "中性偏暖", "情绪修复中，方向待确认", "blue"),
    (25, "修复初期", "恐慌退潮，仍处低位区", "blue"),
    (0, "冰点", "恐慌抛售，出清末端的典型特征", "green"),
]


def _width_score(limit_up, limit_down):
    """宽度得分 -25 ~ 45"""
    s = 0
    if limit_up >= 500:
        s += 45
    elif limit_up >= 200:
        s += 38
    elif limit_up >= 100:
        s += 30
    elif limit_up >= 50:
        s += 20
    elif limit_up >= 20:
        s += 10
    elif limit_up <= 10:
        s -= 10
    if limit_down >= 100:
        s -= 25
    elif limit_down >= 30:
        s -= 15
    elif limit_down >= 10:
        s -= 8
    return s


def _median_score(m):
    if m is None:
        return 0
    if m >= 6:
        return 35
    if m >= 4:
        return 30
    if m >= 2:
        return 22
    if m >= 0.5:
        return 12
    if m >= -0.5:
        return 0
    if m >= -2:
        return -12
    return -22


def _amount_score(rank):
    if rank is None:
        return 0
    if rank >= 80:
        return 20
    if rank >= 60:
        return 12
    if rank >= 40:
        return 5
    if rank <= 20:
        return -10
    return 0


def _margin_score(chg20):
    if chg20 is None:
        return 0
    if chg20 >= 3:
        return 10
    if chg20 >= 0:
        return 3
    if chg20 <= -8:
        return -18
    if chg20 <= -5:
        return -14
    return -6


def _vol_score(rank):
    """QVIX 高 = 恐慌 = 情绪冷，反向"""
    if rank is None:
        return 0
    if rank >= 80:
        return -10
    if rank >= 60:
        return -4
    if rank <= 20:
        return 10
    return 0


def calc_emotion(sent):
    s = calc_sentiment(sent)
    br = s["breadth"] or {}
    mg = s["margin"] or {}
    qv = s["qvix"] or {}

    limit_up = br.get("limit_up") or 0
    limit_down = br.get("limit_down") or 0
    median = br.get("median_chg")
    total = br.get("total") or 0
    up = br.get("up") or 0
    down = br.get("down") or 0

    parts = {
        "宽度(涨停)": _width_score(limit_up, limit_down),
        "涨幅(中位数)": _median_score(median),
        "量能(成交分位)": _amount_score(s["amount_rank_250"]),
        "杠杆(融资20日)": _margin_score(mg.get("chg20_pct")),
        "波动(QVIX分位)": _vol_score(qv.get("rank_250")),
    }
    raw = sum(parts.values())
    heat = int(max(0, min(100, raw + 20)))  # 原始区间约 -55~110，平移压缩到 0-100

    stage, meaning, tone = STAGES[-1][1], STAGES[-1][2], STAGES[-1][3]
    for th, name, mean, tn in STAGES:
        if heat >= th:
            stage, meaning, tone = name, mean, tn
            break

    up_ratio = round(up / total * 100, 1) if total else None
    return {
        "as_of": s["as_of"],
        "heat": heat,
        "raw_score": raw,
        "stage": stage,
        "meaning": meaning,
        "tone": tone,
        "parts": parts,
        "metrics": {
            "amount_yi": s["amount_yi"],
            "amount_rank_250": s["amount_rank_250"],
            "amount_yi_all": br.get("amount_yi_all"),
            "drawdown_from_250d_high_pct": s["drawdown_from_250d_high_pct"],
            "hs300_close": s["hs300_close"],
            "hs300_chg_pct": s["hs300_chg_pct"],
            "up": up,
            "down": down,
            "total": total,
            "up_ratio": up_ratio,
            "limit_up": limit_up,
            "limit_down": limit_down,
            "median_chg": median,
            "margin": mg,
            "qvix": qv,
        },
        "stages": [{"threshold": th, "name": nm, "meaning": mn} for th, nm, mn, _ in STAGES],
    }


def main():
    sent = _load("sentiment.json")
    em = calc_emotion(sent)
    out = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"), **em}
    with open(os.path.join(DATA, "emotion.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(f"情绪温度 {em['heat']} / 100 → {em['stage']}（{em['meaning']}）")
    for k, v in em["parts"].items():
        print(f"  {k}: {v:+d}")
    return out


if __name__ == "__main__":
    main()
