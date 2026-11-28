# -*- coding: utf-8 -*-
"""emit 阶段：写指标模板 out/latest.json（落库由框架 publish.py 负责）
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.environ.get("CASE_OUT_DIR") or os.path.join(HERE, "out")
SRC_DATA = os.path.join(HERE, "output", "data")


def load(name):
    with open(os.path.join(SRC_DATA, name), encoding="utf-8") as f:
        return json.load(f)


def main():
    os.makedirs(OUT, exist_ok=True)
    c = load("cycle.json")
    e = load("emotion.json")
    ma, p, b = c["ma"], c["passive"], c["basis"]
    bi = b["IF"]
    as_of = c["as_of"]

    points = [
        {"key": "clearance.ma_state", "sec": "000300.SH", "obs_date": as_of,
         "value_text": ma["state"], "quality": "ok", "name": "出清周期状态",
         "risk_level": "risk" if ma["in_clearing"] else "opportunity",
         "risk_note": f"MA40 {ma['ma_fast']:.0f} / MA80 {ma['ma_slow']:.0f}，缺口 {ma['gap_pct']:.2f}%"},
        {"key": "clearance.days", "sec": "000300.SH", "obs_date": as_of,
         "value": ma["days"], "dec": 0, "quality": "ok", "name": "出清持续交易日"},
        {"key": "clearance.ma_gap_pct", "sec": "000300.SH", "obs_date": as_of,
         "value": ma["gap_pct"], "dec": 2, "unit": "%", "quality": "ok", "name": "MA40−MA80 缺口"},
        {"key": "clearance.flat_days", "sec": "000300.SH", "obs_date": as_of,
         "value": ma["flat_days"], "dec": 0, "quality": "ok" if ma.get("flat_days") else "missing",
         "name": "横盘修复测算（交易日）"},
        {"key": "clearance.passive_pct", "sec": "000300.SH", "obs_date": as_of,
         "value": p["value_pct"], "dec": 2, "unit": "%", "quality": "ok",
         "name": "被动化率（自由流通市值分母）",
         "risk_level": "risk" if p["value_pct"] >= 7 else "none",
         "risk_note": f"7% 提醒线，土狗刻度 {p['value_pct_dog']:.2f}%"},
        {"key": "clearance.passive_pct_dog", "sec": "000300.SH", "obs_date": as_of,
         "value": p["value_pct_dog"], "dec": 2, "unit": "%", "quality": "estimated",
         "name": "被动化率（土狗锚点刻度）"},
        {"key": "clearance.basis_annual_pct", "sec": "IF.CFFEX", "obs_date": as_of,
         "value": bi["annual_pct"], "dec": 2, "unit": "%", "quality": "ok",
         "name": f"{bi['contract']} 年化基差"},
        {"key": "clearance.basis_drop_pp", "sec": "IF.CFFEX", "obs_date": as_of,
         "value": bi["drop_pp"], "dec": 2, "unit": "pp", "quality": "ok",
         "name": "基差距5日高点回落"},
        {"key": "emotion.heat", "sec": "000300.SH", "obs_date": e["as_of"],
         "value": e["heat"], "dec": 0, "quality": "ok", "name": "情绪温度（0-100）",
         "risk_level": "alert" if e["heat"] >= 70 else ("risk" if e["heat"] >= 55 else "none")},
        {"key": "emotion.stage", "sec": "000300.SH", "obs_date": e["as_of"],
         "value_text": e["stage"], "quality": "ok", "name": "情绪阶段"},
    ]

    payload = {"as_of": as_of, "points": points}
    with open(os.path.join(OUT, "latest.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"指标模板已写：out/latest.json（{len(points)} 条）")


if __name__ == "__main__":
    main()
