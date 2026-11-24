# -*- coding: utf-8 -*-
"""行业级缠论过滤：对六个方向对应的申万三级行业指数跑缠论分型版，输出结构与买卖点结论

写入 config.industry_filter_results
用法: python3 chan_industry.py [--task-dir DIR] [--start 20250101]
"""
import argparse
import json
import os
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-dir", default=".")
    ap.add_argument("--start", default="20250101")
    a = ap.parse_args()
    cfg_p = os.path.join(a.task_dir, "config.json")
    cfg = json.load(open(cfg_p, encoding="utf-8"))
    filt = cfg.get("industry_filter", {})
    idx_map = filt.get("indices", {})
    out_dir = os.path.join(a.task_dir, "data", "index_ohlcv")
    os.makedirs(out_dir, exist_ok=True)

    tdate = cfg.get("asof", "2026-10-30").replace("-", "")
    results, detail = {}, {}
    for direction, lst in idx_map.items():
        rows_out = []
        for it in lst:
            code, name = it["code"], it["name"]
            if not code:
                continue
            f, d = call("sw_daily", {"ts_code": code, "start_date": a.start, "end_date": tdate})
            recs = [dict(zip(f, r)) for r in d] if f else []
            recs.sort(key=lambda x: x["trade_date"])
            if len(recs) < 120:
                print("[warn] %s(%s) 指数数据不足 %d 根" % (name, code, len(recs)))
            csv_p = os.path.join(out_dir, "%s.csv" % code)
            with open(csv_p, "w", encoding="utf-8") as fh:
                fh.write("date,open,high,low,close,vol\n")
                for r in recs:
                    fh.write("%s,%s,%s,%s,%s,%s\n" % (r["trade_date"], r.get("open"), r.get("high"),
                                                      r.get("low"), r.get("close"), r.get("vol", 0)))
            df = pd.read_csv(csv_p)
            res = analyze(df, level="day", backtest=False, name="%s(%s)" % (name, code))
            zs = (res.get("结构") or {}).get("中枢") or []
            sig = res.get("交易机会") or []
            close = float(recs[-1]["close"])
            last_zs = zs[-1] if zs else None
            if last_zs:
                zd, zg = float(last_zs["下沿ZD"]), float(last_zs["上沿ZG"])
                pos = "中枢上方" if close > zg else ("中枢内部" if close >= zd else "中枢下方")
            else:
                zd = zg = None
                pos = "无中枢"
            b1 = [s for s in sig if s.get("类型") == "B1"]
            ok = (pos != "中枢下方") or bool(b1)
            rows_out.append({"name": name, "code": code, "close": close, "位置": pos,
                             "ZD": zd, "ZG": zg,
                             "距中枢上沿": round((close / zg - 1) * 100, 2) if zg else None,
                             "笔数": len((res.get("结构") or {}).get("笔端点") or []),
                             "B1数": len(b1), "买卖点总数": len(sig),
                             "买卖点": [{"时间": s.get("时间"), "类型": s.get("类型"), "价格": s.get("价格")}
                                     for s in sig[-3:]],
                             "判定": "通过" if ok else "不通过",
                             "说明": ("不在中枢下方（%s）" % pos) if pos != "中枢下方"
                                   else ("中枢下方但出现一买 B1 %d 次" % len(b1) if b1 else "中枢下方且无一买")})
            time.sleep(0.5)
        n_ok = sum(1 for r in rows_out if r["判定"] == "通过")
        ratio = (n_ok / len(rows_out)) if rows_out else 0.0
        results[direction] = {"n_indices": len(rows_out), "n_pass": n_ok,
                              "pass_ratio": round(ratio, 2),
                              "判定": "通过" if ratio >= 0.5 else "不通过",
                              "明细": rows_out}
        print("== %-14s 指数 %d 个，通过 %d 个（%.0f%%）→ 行业级缠论 %s" % (
            direction, len(rows_out), n_ok, ratio * 100, results[direction]["判定"]))
        for r in rows_out:
            print("   %-10s %-11s 收盘 %9.2f 位置 %-5s 距上沿 %7s%% B1 %d | %s" % (
                r["name"], r["code"], r["close"], r["位置"],
                ("%+.1f" % r["距中枢上沿"]) if r["距中枢上沿"] is not None else "-",
                r["B1数"], r["判定"]))
        detail[direction] = rows_out

    cfg["industry_filter_results"] = {"asof": cfg.get("asof"), "window_start": a.start,
                                      "rule": filt.get("direction_pass"), "directions": results}
    json.dump(cfg, open(cfg_p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump({"asof": cfg.get("asof"), "directions": results},
              open(os.path.join(a.task_dir, "chan_industry.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("通过行业:", [k for k, v in results.items() if v["判定"] == "通过"])
    print("不通过行业:", [k for k, v in results.items() if v["判定"] != "通过"])


if __name__ == "__main__":
    main()