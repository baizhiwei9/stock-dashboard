# -*- coding: utf-8 -*-
"""按行业级缠论结论重建行业与个股名单

- 行业：六个方向全部保留在对照表中（绿＝聚集／红＝扩散），标出行业级缠论通过与否
- 最终入选行业＝行业级缠论通过者；行业内个股按一致预期 2027E 增速排序，逐个过个股级缠论，取前二
- 同步写入 config：directions、excluded_industries、事件说明
用法: python3 finalize_selection.py [--task-dir DIR]
"""
import argparse
import json
import os
import statistics as stc
import sys
import time

import pandas as pd
import requests

WS = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5e722c9a84ffeEPBhVrM9DzQb6b"
sys.path.insert(0, os.path.join(WS, "pylibs"))
sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f86bdd5bfffeXHo4SFOaOa2sWG/pylibs")
sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/skills/user/chan-fenxing-ban")
import ts_config  # noqa
from chan_fractal import analyze  # noqa

TOKEN, URL = ts_config.TS_TOKEN, ts_config.TS_HTTP_URL
S = requests.Session()
TDATE = "20261030"
MIN_ORGS = 6

CAND = {
    "覆铜板/电子布/CCL": [("南亚新材", "688519.SH"), ("生益科技", "600183.SH"),
                    ("华正新材", "603186.SH"), ("宏昌电子", "603002.SH")],
    "创新药": [("百济神州", "688235.SH"), ("恒瑞医药", "600276.SH"), ("石药创新", "300765.SZ")],
}


def call(api, params, retry=4):
    last = ""
    for i in range(retry + 1):
        try:
            time.sleep(0.8)
            r = S.post(URL, json={"api_name": api, "token": TOKEN, "params": params,
                                  "fields": ""}, timeout=45)
            j = r.json()
            if j.get("code") == 0:
                return j["data"]["fields"], j["data"]["items"]
            last = "code=%s msg=%s" % (j.get("code"), (j.get("msg") or "")[:60])
            if "过快" in (j.get("msg") or "") or "超限" in (j.get("msg") or ""):
                time.sleep(12 + 8 * i)
                continue
        except Exception as e:  # noqa
            last = repr(e)[:120]
        time.sleep(2 + 2 * i)
    print("[warn] 接口失败", api, params, last)
    return [], []


def chan_of(nm, code, kind, task_dir):
    f, it = call("daily", {"ts_code": code, "start_date": "20250101", "end_date": TDATE})
    recs = [dict(zip(f, r)) for r in it] if f else []
    recs.sort(key=lambda x: x["trade_date"])
    if len(recs) < 120:
        print("[warn] %s 日线不足 %d 根" % (code, len(recs)))
    os.makedirs(os.path.join(task_dir, "data", "ohlcv"), exist_ok=True)
    p = os.path.join(task_dir, "data", "ohlcv", "%s.csv" % code)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("date,open,high,low,close,vol\n")
        for r in recs:
            fh.write("%s,%s,%s,%s,%s,%s\n" % (r["trade_date"], r.get("open"), r.get("high"),
                                              r.get("low"), r.get("close"), r.get("vol", 0)))
    res = analyze(pd.read_csv(p), level="day", backtest=False, name="%s(%s)" % (nm, code))
    zs = (res.get("结构") or {}).get("中枢") or []
    sig = res.get("交易机会") or []
    b1 = [s for s in sig if s.get("类型") == "B1"]
    close = float(recs[-1]["close"])
    lz = zs[-1] if zs else None
    if lz:
        zd, zg = float(lz["下沿ZD"]), float(lz["上沿ZG"])
        pos = "中枢上方" if close > zg else ("中枢内部" if close >= zd else "中枢下方")
    else:
        zd = zg = None
        pos = "无中枢"
    return {"name": nm, "code": code, "kind": kind, "close": close,
            "chan": {"位置": pos, "距中枢上沿": round((close / zg - 1) * 100, 2) if zg else None,
                     "B1数": len(b1), "判定": "通过" if ((pos != "中枢下方") or b1) else "不通过",
                     "说明": ("不在中枢下方（%s）" % pos) if pos != "中枢下方"
                           else ("中枢下方但出现一买 %d 次" % len(b1) if b1 else "中枢下方且无一买")}}


def fund_of(code):
    fb, itb = call("daily_basic", {"ts_code": code, "start_date": "20260901", "end_date": TDATE})
    db = [dict(zip(fb, r)) for r in itb] if fb else []
    db.sort(key=lambda x: x["trade_date"])
    if not db:
        return {}
    mv = float(db[-1]["total_mv"])
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
    orgs = len({r.get("org_name") for r in best.values() if str(r.get("quarter")) == "2027Q4"})
    return {"close": float(db[-1]["close"]), "pe_ttm": float(db[-1]["pe_ttm"]),
            "pe27E": round(mv / stc.median(n27), 2) if (n27 and orgs >= MIN_ORGS) else None,
            "g27": round((stc.median(n27) / stc.median(n26) - 1) * 100, 1)
            if (n27 and n26 and orgs >= MIN_ORGS) else None, "orgs": orgs}


def write_px(task_dir, code):
    f, it = call("daily", {"ts_code": code, "start_date": "20260401", "end_date": TDATE})
    rows = [dict(zip(f, r)) for r in it] if f else []
    rows.sort(key=lambda x: x["trade_date"])
    p = os.path.join(task_dir, "data", "px_%s.csv" % code)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("trade_date,close,pct_chg,amount\n")
        for r in rows:
            fh.write("%s,%s,%s,%s\n" % (r["trade_date"], r["close"], r.get("pct_chg", ""), r.get("amount", "")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-dir", default=".")
    a = ap.parse_args()
    cfg_p = os.path.join(a.task_dir, "config.json")
    cfg = json.load(open(cfg_p, encoding="utf-8"))
    ind = json.load(open(os.path.join(a.task_dir, "chan_industry.json"), encoding="utf-8"))
    tbl = {r["direction"]: r for r in cfg["table"]}
    fdir = ind["directions"]

    keep_dirs, drop_dirs = [], []
    for d in [r["direction"] for r in cfg["table"]]:
        v = fdir[d]
        (keep_dirs if v["判定"] == "通过" else drop_dirs).append(d)

    old = {s["code"]: s for dd in cfg["directions"] for s in dd["stocks"]}
    new_dirs = []
    for d in [r["direction"] for r in cfg["table"]]:
        if d not in keep_dirs:
            continue
        stocks = []
        if d == "铝/电解铝":
            stocks = [old[c] for c in ["601600.SH", "002532.SZ"] if c in old]
        else:
            for nm, code in CAND.get(d, []):
                c = chan_of(nm, code, "候选", a.task_dir)
                fu = fund_of(code)
                if fu.get("orgs", 0) < MIN_ORGS:
                    print("跳过 %s %s：研报覆盖 %d 家（<6）" % (d, nm, fu.get("orgs", 0)))
                    continue
                if c["chan"]["判定"] != "通过":
                    print("缠论不通过：%s %s（%s）" % (d, nm, c["chan"]["说明"]))
                    continue
                stocks.append({"name": nm, "code": code, "close": fu.get("close"),
                               "pe_ttm": fu.get("pe_ttm"), "pe27E": fu.get("pe27E"),
                               "g27": fu.get("g27"), "orgs": fu.get("orgs"), "chan": c["chan"],
                               "reason": "行业级缠论通过；个股缠论%s" % c["chan"]["说明"]})
                if len(stocks) >= 2:
                    break
        stocks.sort(key=lambda x: -(x.get("g27") or -999))
        for s in stocks:
            ch = s.setdefault("chan", {})
            if not ch.get("说明"):
                b1 = ch.get("B1数") or 0
                ch["说明"] = ("不在中枢下方（%s）" % ch.get("位置")) if ch.get("位置") != "中枢下方" \
                    else ("中枢下方但出现一买 %d 次" % b1 if b1 else "中枢下方且无一买")
            write_px(a.task_dir, s["code"])
        r = tbl[d]
        new_dirs.append({"direction": d, "indices": r["indices"],
                         "median_neighbors": r["neighbors"], "judge": r["judge"],
                         "industry_chan": fdir[d]["判定"],
                         "industry_chan_pass": "%d/%d 指数通过" % (fdir[d]["n_pass"], fdir[d]["n_indices"]),
                         "flow_4d_yi": r["mf4d_yi"], "flow_rate": r["rate4"],
                         "stocks": stocks})

    cfg["directions"] = new_dirs
    cfg["excluded_industries"] = [
        {"direction": d, "judge": tbl[d]["judge"],
         "reason": "行业级缠论不通过（%d/%d 指数通过，未达多数决）" % (
             fdir[d]["n_pass"], fdir[d]["n_indices"]),
         "detail": "；".join("%s %s" % (x["name"], x["说明"]) for x in fdir[d]["明细"])}
        for d in drop_dirs]
    cfg["selection_final"] = "最终入选行业＝行业级缠论通过者（%s）；本轮通过者中含聚集方向，若只做扩散方向，则仅剩铝（2 只）。" % "、".join(
        "%s（%s）" % (d, tbl[d]["judge"]) for d in keep_dirs)
    json.dump(cfg, open(cfg_p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print("== 最终入选行业与个股 ==")
    ok = True
    for dd in cfg["directions"]:
        print("%-14s [%s] 行业级缠论 %s（%s）" % (
            dd["direction"], dd["judge"], dd["industry_chan"], dd["industry_chan_pass"]))
        for s in dd["stocks"]:
            if s["chan"]["判定"] != "通过":
                ok = False
            print("   %-8s %-10s 现价 %7.2f 2027E PE %5s 增速 %6s | %s" % (
                s["name"], s["code"], s["close"], s.get("pe27E"), s.get("g27"), s["chan"]["说明"]))
    print("行业级不通过（仅列示不入选）:", [(x["direction"], x["judge"]) for x in cfg["excluded_industries"]])
    print("最终名单全部满足个股级缠论:", ok)


if __name__ == "__main__":
    main()