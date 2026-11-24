# -*- coding: utf-8 -*-
"""核对入选名单数据（对照原始响应独立复算）+ 拉取画图所需历史行情"""
import json
import os
import statistics as stc
import sys
import time

import requests

WS = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5e722c9a84ffeEPBhVrM9DzQb6b"
TASK = os.path.join(WS, "将军基本面拓扑选股")
DATA = os.path.join(TASK, "data")
os.makedirs(DATA, exist_ok=True)

sys.path.insert(0, os.path.join(WS, "pylibs"))
CFG = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f86bdd5bfffeXHo4SFOaOa2sWG/pylibs"
sys.path.insert(0, CFG)
import ts_config  # noqa

TOKEN, URL = ts_config.TS_TOKEN, ts_config.TS_HTTP_URL
S = requests.Session()


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
    print("FAIL", api, params, last)
    return [], []


cfg = json.load(open(os.path.join(TASK, "config.json"), encoding="utf-8"))
codes = [s["code"] for d in cfg["directions"] for s in d["stocks"]]
name_of = {s["code"]: s["name"] for d in cfg["directions"] for s in d["stocks"]}

print("%-11s %-8s %8s %8s %9s %9s %6s" % ("code", "name", "现价config", "现价原始", "PE27config", "PE27复算", "判定"))
ok_all = True
for d in cfg["directions"]:
    for s in d["stocks"]:
        code = s["code"]
        # 1) 原始估值
        bp = os.path.join(WS, "consensus_raw", code + "_basic.json")
        db = json.load(open(bp, encoding="utf-8"))
        rows = [dict(zip(db["fields"], r)) for r in db["items"]]
        rows = [r for r in rows if r.get("trade_date")]
        rows.sort(key=lambda x: x["trade_date"])
        last = rows[-1]
        mv_wan = float(last["total_mv"])
        # 2) 原始研报复算 2027Q4 中位数
        rp = os.path.join(WS, "consensus_raw", code + ".json")
        rr = json.load(open(rp, encoding="utf-8"))
        recs = [dict(zip(rr["fields"], r)) for r in rr["items"]]
        best = {}
        for r in recs:
            org, q = r.get("org_name"), str(r.get("quarter") or "")
            if not org or not q:
                continue
            k = (org, q)
            if k not in best or str(best[k]["report_date"]) < str(r["report_date"]):
                best[k] = r
        np27 = []
        for r in best.values():
            if str(r.get("quarter")) == "2027Q4":
                try:
                    v = float(r.get("np"))
                except (TypeError, ValueError):
                    continue
                if v == v and abs(v) < 1e12:
                    np27.append(v)
        np_med = stc.median(np27)
        pe27 = round(mv_wan / np_med, 2)
        ok = (abs(last["close"] - s["close"]) < 1e-6) and (abs(pe27 - s["pe27E"]) < 0.02)
        ok_all = ok_all and ok
        print("%-11s %-8s %8.2f %8.2f %9.2f %9.2f %6s" % (
            code, s["name"], s["close"], float(last["close"]), s["pe27E"], pe27,
            "PASS" if ok else "MISMATCH"))
        s["data_check"] = {"trade_date": last["trade_date"], "close": float(last["close"]),
                           "pe_ttm": float(last["pe_ttm"]), "pe27E_recomputed": pe27,
                           "n_orgs_2027": len(np27), "status": "PASS" if ok else "MISMATCH"}

print("估值与一致预期复算一致:", ok_all)

# 3) 历史行情（近 6 个月）用于子图
print("== 拉取历史行情 ==")
for code in codes:
    f, it = call("daily", {"ts_code": code, "start_date": "20260401", "end_date": "20261030"})
    rows = [dict(zip(f, r)) for r in it] if f else []
    rows.sort(key=lambda x: x["trade_date"])
    p = os.path.join(DATA, "px_%s.csv" % code)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("trade_date,close,pct_chg,amount\n")
        for r in rows:
            fh.write("%s,%s,%s,%s\n" % (r["trade_date"], r["close"], r.get("pct_chg", ""), r.get("amount", "")))
    print(code, name_of[code], "行数", len(rows), rows[0]["trade_date"] if rows else "-", "->", rows[-1]["trade_date"] if rows else "-")

with open(os.path.join(TASK, "config.json"), "w", encoding="utf-8") as fh:
    json.dump(cfg, fh, ensure_ascii=False, indent=1)
print("config.json 已回写核对结果")