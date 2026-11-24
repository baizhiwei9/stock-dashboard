# -*- coding: utf-8 -*-
"""按缠论结论复核名单：不达标者用同行业下一顺位候选替换，补取新标的行情与预期，并复检

用法: python3 apply_chan_replace.py [--task-dir DIR]
"""
import argparse
import csv
import json
import os
import statistics as stc
import sys
import time

import requests

WS = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5e722c9a84ffeEPBhVrM9DzQb6b"
sys.path.insert(0, os.path.join(WS, "pylibs"))
sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f86bdd5bfffeXHo4SFOaOa2sWG/pylibs")
import ts_config  # noqa

TOKEN, URL = ts_config.TS_TOKEN, ts_config.TS_HTTP_URL
S = requests.Session()
TDATE = "20261030"

BACKUP_ORDER = {
    "电网设备与变压器": [("平高电气", "600312.SH"), ("中国西电", "601179.SH")],
    "铝/电解铝": [("中国铝业", "601600.SH"), ("神火股份", "000933.SZ")],
    "化学原料": [("卫星化学", "002648.SZ")],
}
REASON = {
    "600312.SH": "前三季归母 12.31 亿元、特高压新签同比 +149%，缠论中枢上方且有一买",
    "601179.SH": "输变电设备出口链条，缠论中枢下方但出现一买",
    "601600.SH": "全球最大氧化铝产能、前三季归母 179.16 亿元（+64.83%），缠论中枢下方但出现一买",
    "000933.SZ": "电解铝量价齐升，缠论中枢下方且无一买",
    "002648.SZ": "C2 产业链、2027E PE 最低档，缠论中枢下方但出现一买",
}


def call(api, params, retry=2):
    last = ""
    for i in range(retry + 1):
        try:
            r = S.post(URL, json={"api_name": api, "token": TOKEN, "params": params,
                                  "fields": ""}, timeout=45)
            j = r.json()
            if j.get("code") == 0:
                return j["data"]["fields"], j["data"]["items"]
            last = "code=%s msg=%s" % (j.get("code"), (j.get("msg") or "")[:60])
        except Exception as e:  # noqa
            last = repr(e)[:120]
        time.sleep(2 + 2 * i)
    print("[warn] 接口失败", api, params, last)
    return [], []


def fetch_stock(a, nm, code):
    f, it = call("daily", {"ts_code": code, "start_date": "20260401", "end_date": TDATE})
    rows = [dict(zip(f, r)) for r in it] if f else []
    rows.sort(key=lambda x: x["trade_date"])
    p = os.path.join(a.task_dir, "data", "px_%s.csv" % code)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("trade_date,close,pct_chg,amount\n")
        for r in rows:
            fh.write("%s,%s,%s,%s\n" % (r["trade_date"], r["close"], r.get("pct_chg", ""), r.get("amount", "")))
    fb, itb = call("daily_basic", {"ts_code": code, "start_date": "20260901", "end_date": TDATE})
    db = [dict(zip(fb, r)) for r in itb] if fb else []
    db.sort(key=lambda x: x["trade_date"])
    close = float(db[-1]["close"]) if db else float(rows[-1]["close"])
    mv = float(db[-1]["total_mv"]) if db else None
    pe_ttm = float(db[-1]["pe_ttm"]) if db else None
    fp, itp = call("report_rc", {"ts_code": code, "start_date": "20250101", "end_date": TDATE})
    recs = [dict(zip(fp, r)) for r in itp] if fp else []
    best = {}
    for r in recs:
        org, q = r.get("org_name"), str(r.get("quarter") or "")
        if not org or not q:
            continue
        k = (org, q)
        if k not in best or str(best[k]["report_date"]) < str(r["report_date"]):
            best[k] = r
    n27, n26 = [], []
    for r in best.values():
        try:
            v = float(r.get("np"))
        except (TypeError, ValueError):
            continue
        if v != v or abs(v) >= 1e12:
            continue
        if str(r.get("quarter")) == "2027Q4":
            n27.append(v)
        elif str(r.get("quarter")) == "2026Q4":
            n26.append(v)
    orgs27 = len({r.get("org_name") for r in best.values() if str(r.get("quarter")) == "2027Q4"})
    out = {"name": nm, "code": code, "close": close, "pe_ttm": pe_ttm,
           "pe27E": round(mv / stc.median(n27), 2) if (n27 and mv) else None,
           "np27E_yi": round(stc.median(n27) / 1e4, 2) if n27 else None,
           "g27": round((stc.median(n27) / stc.median(n26) - 1) * 100, 1) if (n27 and n26) else None,
           "orgs": orgs27, "reason": REASON.get(code, "")}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-dir", default=".")
    a = ap.parse_args()
    cfg_p = os.path.join(a.task_dir, "config.json")
    cfg = json.load(open(cfg_p, encoding="utf-8"))
    chan = json.load(open(os.path.join(a.task_dir, "chan_results.json"), encoding="utf-8"))
    verdict = {(c["direction"], c["code"]): c for c in chan["results"]}

    replaced = []
    for d in cfg["directions"]:
        direction = d["direction"]
        keep = []
        for s in d["stocks"]:
            v = verdict.get((direction, s["code"]))
            s["chan"] = {"位置": v["位置"], "距中枢上沿": v["距中枢上沿"], "B1数": v["B1数"],
                         "最近信号": v["最近信号"], "判定": v["判定"],
                         "中枢": v["最近中枢"]} if v else {}
            if v and v["判定"] == "通过":
                keep.append(s)
            else:
                replaced.append({"direction": direction, "name": s["name"], "code": s["code"],
                                 "reason": (v["判定说明"] if v else "无缠论结果")})
                print("替换掉 %s %s：%s" % (direction, s["name"], v["判定说明"] if v else "-"))
        if len(keep) < 2:
            for nm, code in BACKUP_ORDER.get(direction, []):
                v = verdict.get((direction, code))
                if not v or v["判定"] != "通过":
                    continue
                add = fetch_stock(a, nm, code)
                add["chan"] = {"位置": v["位置"], "距中枢上沿": v["距中枢上沿"], "B1数": v["B1数"],
                               "最近信号": v["最近信号"], "判定": v["判定"], "中枢": v["最近中枢"]}
                keep.append(add)
                print("补入 %s %s（%s）缠论%s" % (direction, nm, code, v["位置"]))
                if len(keep) >= 2:
                    break
                time.sleep(0.4)
        d["stocks"] = keep[:2]
        d["stocks"] = sorted(d["stocks"], key=lambda x: -(x.get("g27") or -999))

    cfg["chan_rule"] = "当前不在中枢下方，或位于中枢下方但已出现一买（B1）"
    cfg["replaced_out"] = replaced
    cfg["chan_asof"] = chan["asof"]
    json.dump(cfg, open(cfg_p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print("== 最终名单 ==")
    ok = True
    for d in cfg["directions"]:
        for s in d["stocks"]:
            ch = s.get("chan", {})
            if ch.get("判定") != "通过":
                ok = False
            print("%-14s %-8s %-10s 现价 %7.2f 2027E PE %5s 增速 %6s | 缠论 %-6s 距上沿 %s%% B1 %s" % (
                d["direction"], s["name"], s["code"], s["close"], s.get("pe27E"), s.get("g27"),
                ch.get("位置"), ch.get("距中枢上沿"), ch.get("B1数")))
    print("最终名单全部满足缠论门槛:", ok)
    print("替换明细:", [(r["direction"], r["name"]) for r in replaced])


if __name__ == "__main__":
    main()