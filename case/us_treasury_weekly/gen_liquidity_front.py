#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成美债监控卡片【置顶栏目】数据文件 treasury_liquidity_front.json。

数据来源：macroliq_fetcher/data/ 已核验落盘文件（fred_data.json、
ice_move_record.json、boj_m2_jpy_full.json、ecb_m2_eur_full.json）及
availability_report.json（ECB/BOJ 资产负债表、NY Fed ACM 期限溢价）。
不联网、不编造；文件缺失时字段置空并标记 source 缺失。
输出：treasury_liquidity_front.json（美债工作目录）
"""
import datetime
import json
import os

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "..", "ses_gffe5ee1d17e02ffeBBPuFJhbMDuVqs", "macroliq_fetcher", "data")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "treasury_liquidity_front.json")


def load(name):
    p = os.path.join(BASE, name)
    try:
        with open(p, encoding="utf-8") as fp:
            return json.load(fp)
    except Exception as exc:  # noqa: BLE001
        return {"_err": str(exc)}


def main():
    out = {"source": "macroliq_fetcher data (已核验落盘)", "generated_at": "2026-10-14"}

    fred = load("fred_data.json")
    if not fred.get("_err"):
        series = fred.get("series", {})
        out["fred"] = {
            k: {"date": v.get("latest_date"), "value": v.get("latest_value"),
                "obs_n": len(v.get("observations", []))}
            for k, v in series.items()
        }
    else:
        out["fred_err"] = fred.get("_err")

    move = load("ice_move_record.json")
    if not move.get("_err"):
        h = move.get("history", [])
        out["move"] = h[-1] if h else {"_empty": True}
    else:
        out["move_err"] = move.get("_err")

    boj = load("boj_m2_jpy_full.json")
    if isinstance(boj, list) and boj:
        out["boj_m2"] = {"date": boj[-1]["date"], "value": boj[-1]["m2_jpy_100m"]}
    elif isinstance(boj, dict) and boj.get("_err"):
        out["boj_m2_err"] = boj.get("_err")

    ecb = load("ecb_m2_eur_full.json")
    if isinstance(ecb, list) and ecb:
        out["ecb_m2"] = {"date": ecb[-1]["date"], "value": round(ecb[-1]["m2_eur_millions"], 2)}
    elif isinstance(ecb, dict) and ecb.get("_err"):
        out["ecb_m2_err"] = ecb.get("_err")

    avail = load("availability_report.json")
    if not avail.get("_err"):
        s = avail.get("sources", {})
        out["ecb_bs"] = s.get("ecb_bs", {}).get("latest_found")
        out["boj_bs"] = s.get("boj_bs", {}).get("latest_found")
        out["acm"] = s.get("nyfed_acm", {}).get("latest_found")
        out["pboc_gap"] = {
            "claims": s.get("pboc_claims", {}).get("need"),
            "gold": s.get("pboc_gold", {}).get("need"),
            "m2_cn": s.get("global_m2", {}).get("cn", {}).get("fallback"),
        }
    else:
        out["avail_err"] = avail.get("_err")

    # 合并流动性指标计算结果（liquidity_metrics.py 产物：6指标/阶段/预警/缺口）
    lm = os.path.join(os.path.dirname(os.path.abspath(__file__)), "liquidity_metrics.json")
    try:
        with open(lm, encoding="utf-8") as fp:
            out["metrics"] = json.load(fp)
    except Exception as exc:  # noqa: BLE001
        out["metrics_err"] = str(exc)

    with open(OUT, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=2)
    print("已生成:", OUT)
    print(json.dumps(out, ensure_ascii=False, indent=1)[:2500])


if __name__ == "__main__":
    main()