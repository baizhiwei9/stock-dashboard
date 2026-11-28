#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""出清周期判定（周期边界由 MA40/MA80 定义）

- 出清开始 = 沪深300 MA40 下穿 MA80（死叉）→ 开始发送报告
- 出清完成 = 沪深300 MA40 上穿 MA80（金叉）→ 停止发送报告
- 出清期间用两个进度指标衡量走了多远：被动化率、年化基差回落
输出 output/data/cycle.json
"""
import json
import os
from datetime import datetime

import pandas as pd

from calc_clearing import BASIS_DROP, DATA, PASSIVE_ALERT, PASSIVE_START, PASSIVE_TOP, _load, calc_basis, calc_passive

MA_FAST, MA_SLOW = 40, 80


def calc_ma(hs_recs, keep=140):
    hs = pd.DataFrame(hs_recs).sort_values("date").copy()
    hs["date"] = hs["date"].astype(str)
    hs["close"] = pd.to_numeric(hs["close"], errors="coerce")
    hs = hs.dropna(subset=["close"]).reset_index(drop=True)
    hs["ma_fast"] = hs["close"].rolling(MA_FAST).mean()
    hs["ma_slow"] = hs["close"].rolling(MA_SLOW).mean()
    t = hs.dropna(subset=["ma_fast", "ma_slow"]).reset_index(drop=True)
    t["sign"] = (t["ma_fast"] > t["ma_slow"]).astype(int)
    t["cross"] = t["sign"].diff()

    last = t.iloc[-1]
    golden = bool(last["sign"] == 1)
    crosses = t[t["cross"].notna() & (t["cross"] != 0)]
    if len(crosses):
        last_cross = crosses.iloc[-1]
        since = str(last_cross["date"])
        days = int((t["date"] > since).sum()) + 1
        last_cross_type = "上穿（金叉）" if last_cross["sign"] == 1 else "下穿（死叉）"
    else:
        since, days, last_cross_type = None, None, None

    gap_pct = (float(last["ma_slow"]) - float(last["ma_fast"])) / float(last["ma_fast"]) * 100

    # 横盘情形下 MA40/MA80 的收敛速度（点/日）：dif 每交易日的变化量
    c0 = float(last["close"])
    mf, ms = float(last["ma_fast"]), float(last["ma_slow"])
    dif_v = mf - ms
    speed = -(mf - c0) / MA_FAST + (ms - c0) / MA_SLOW
    flat_days = int(abs(dif_v) / speed) if (dif_v < 0 and speed > 0) else None

    series = [
        {
            "date": str(r["date"]),
            "close": round(float(r["close"]), 2),
            "ma_fast": round(float(r["ma_fast"]), 2),
            "ma_slow": round(float(r["ma_slow"]), 2),
        }
        for _, r in t.tail(keep).iterrows()
    ]
    hist_crosses = [
        {
            "date": str(r["date"]),
            "type": "上穿" if r["sign"] == 1 else "下穿",
            "close": round(float(r["close"]), 2),
            "ma_fast": round(float(r["ma_fast"]), 2),
            "ma_slow": round(float(r["ma_slow"]), 2),
        }
        for _, r in crosses.tail(6).iterrows()
    ]
    return {
        "symbol": "沪深300",
        "close": round(float(last["close"]), 2),
        "date": str(last["date"]),
        "ma_fast_name": f"MA{MA_FAST}",
        "ma_slow_name": f"MA{MA_SLOW}",
        "ma_fast": round(float(last["ma_fast"]), 2),
        "ma_slow": round(float(last["ma_slow"]), 2),
        "dif": round(float(last["ma_fast"]) - float(last["ma_slow"]), 2),
        "gap_pct": round(gap_pct, 2),
        "converge_speed": round(speed, 2),
        "flat_days": flat_days,
        "golden": golden,
        "state": "非出清（MA40 在 MA80 上方）" if golden else "出清中（MA40 在 MA80 下方）",
        "in_clearing": not golden,
        "since": since,
        "days": days,
        "last_cross_type": last_cross_type,
        "report_action": "停止发送" if golden else "继续发送",
        "crosses": hist_crosses,
        "series": series,
    }


def main():
    etf = _load("etf_scale.json")
    basis = _load("basis.json")
    sent = _load("sentiment.json")

    ma = calc_ma(sent["hs300"])
    passive = calc_passive(etf, sent["hs300"])
    basis_r = calc_basis(basis)

    # 出清期间的两个进度指标（不是完成判定，完成只认 MA40 上穿 MA80）
    progress = [
        {
            "name": "被动化率",
            "value": passive["value_pct"],
            "value_dog": passive["value_pct_dog"],
            "unit": "%",
            "start": PASSIVE_START,
            "alert": PASSIVE_ALERT,
            "top": PASSIVE_TOP,
            "done": passive["value_pct"] >= PASSIVE_ALERT,
            "note": "宽基ETF ÷ 沪深300自由流通市值",
        },
        {
            "name": "基差回落",
            "value": basis_r["IF"]["drop_pp"],
            "unit": "pp",
            "target": BASIS_DROP,
            "done": basis_r["IF"]["drop_pp"] >= BASIS_DROP,
            "note": f"IF 远季年化 {basis_r['IF']['annual_pct']}%，距5日高回落",
        },
    ]

    out = {
        "as_of": basis.get("as_of"),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "ma": ma,
        "passive": passive,
        "basis": basis_r,
        "progress": progress,
        "progress_done": int(sum(x["done"] for x in progress)),
    }
    with open(os.path.join(DATA, "cycle.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)

    print(f"MA 状态：{ma['state']}  MA40 {ma['ma_fast']} / MA80 {ma['ma_slow']}  差 {ma['dif']} ({ma['gap_pct']}%)")
    print(f"最近交叉：{ma['since']} {ma['last_cross_type']} → 已 {ma['days']} 个交易日")
    print(f"报告动作：{ma['report_action']}")
    for x in progress:
        print(f"进度 {x['name']} {x['value']}{x['unit']} 完成={x['done']}")
    return out


if __name__ == "__main__":
    main()
