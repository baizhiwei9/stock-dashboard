# -*- coding: utf-8 -*-
"""对指定对象（基础层通过 + 机器人链目标）取指数 OHLCV 并跑缠论，输出结构与买卖点结论

规则同周报：收盘不在最近中枢下方，或位于中枢下方但出现一买（B1）
输出：data/fm_chan.json
用法: python3 chan_full.py
"""
import json
import os
import sys
import time

import pandas as pd
import requests

WS = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace"
sys.path.insert(0, os.path.join(WS, "pylibs"))
sys.path.insert(0, os.path.join(WS, "ses_gffe5f86bdd5bfffeXHo4SFOaOa2sWG", "pylibs"))
sys.path.insert(0, os.path.join(WS, "skills", "user", "chan-fenxing-ban"))
import ts_config  # noqa
from chan_fractal import analyze  # noqa

S = requests.Session()
D = "data"
OHLC = os.path.join(D, "fm_ohlcv")
START = "20250101"


def call(api, params, retry=4):
    last = ""
    for i in range(retry + 1):
        try:
            time.sleep(0.7)
            r = S.post(ts_config.TS_HTTP_URL, json={"api_name": api, "token": ts_config.TS_TOKEN,
                                                    "params": params, "fields": ""}, timeout=60)
            j = r.json()
            if j.get("code") == 0:
                return j["data"]
            last = "code=%s msg=%s" % (j.get("code"), (j.get("msg") or "")[:50])
            if "过快" in (j.get("msg") or "") or "超限" in (j.get("msg") or ""):
                time.sleep(12 + 8 * i)
                continue
            break
        except Exception as e:  # noqa
            last = repr(e)[:100]
            time.sleep(3 + 3 * i)
    print("[warn]", api, params, last)
    return None


ROBOT = ["850781.SI", "850741.SI", "850782.SI", "850784.SI", "850711.SI",          # 申万
         "886008.TI", "886069.TI", "885517.TI", "886002.TI", "885946.TI"]          # 概念


def main():
    screen = json.load(open(os.path.join(D, "fm_screen.json"), encoding="utf-8"))
    pass_base = [r for r in screen if r["pass_base"]]
    sw_l3 = json.load(open(os.path.join(D, "fm_sw_l3.json"), encoding="utf-8"))
    ths = json.load(open(os.path.join(D, "fm_ths_index.json"), encoding="utf-8"))
    nm_sw = {x["index_code"]: x["industry_name"] for x in sw_l3}
    nm_ths = {x["ts_code"]: x["name"] for x in ths}

    todo = {}
    for r in pass_base:
        todo[r["code"]] = {"name": r["name"], "type": r["type"]}
    for c in ROBOT:
        if c not in todo:
            nm = nm_sw.get(c) or nm_ths.get(c)
            todo[c] = {"name": nm, "type": "申万三级" if c.endswith(".SI") else "概念"}

    os.makedirs(OHLC, exist_ok=True)
    out = {}
    for i, (code, meta) in enumerate(todo.items(), 1):
        csv_p = os.path.join(OHLC, "%s.csv" % code.replace(".", "_"))
        if os.path.exists(csv_p):
            df = pd.read_csv(csv_p)
        else:
            api = "sw_daily" if code.endswith(".SI") else "ths_daily"
            d = call(api, {"ts_code": code, "start_date": START, "end_date": "20261031"})
            it = d.get("items", []) if d else []
            f = d.get("fields", []) if d else []
            recs = [dict(zip(f, r)) for r in it]
            recs.sort(key=lambda x: x["trade_date"])
            if len(recs) < 120:
                print("[warn] %s(%s) 仅 %d 根" % (meta["name"], code, len(recs)))
            with open(csv_p, "w", encoding="utf-8") as fh:
                fh.write("date,open,high,low,close,vol\n")
                for r in recs:
                    fh.write("%s,%s,%s,%s,%s,%s\n" % (r["trade_date"], r.get("open"), r.get("high"),
                                                      r.get("low"), r.get("close"), r.get("vol", 0)))
            df = pd.read_csv(csv_p)
        try:
            res = analyze(df, level="day", backtest=False, name="%s(%s)" % (meta["name"], code))
        except Exception as e:  # noqa
            print("[warn] 缠论失败 %s %s" % (code, repr(e)[:80]))
            out[code] = {**meta, "判定": "未取到", "位置": None, "说明": repr(e)[:60]}
            continue
        zs = (res.get("结构") or {}).get("中枢") or []
        sig = res.get("交易机会") or []
        close = float(df["close"].iloc[-1])
        last_zs = zs[-1] if zs else None
        if last_zs:
            zd, zg = float(last_zs["下沿ZD"]), float(last_zs["上沿ZG"])
            pos = "中枢上方" if close > zg else ("中枢内部" if close >= zd else "中枢下方")
        else:
            zd = zg = None
            pos = "无中枢"
        b1 = [s for s in sig if s.get("类型") == "B1"]
        ok = (pos != "中枢下方") or bool(b1)
        out[code] = {**meta, "close": round(close, 2), "位置": pos, "ZD": zd, "ZG": zg,
                     "距上沿": round((close / zg - 1) * 100, 2) if zg else None,
                     "B1数": len(b1), "买卖点总数": len(sig),
                     "判定": "通过" if ok else "不通过",
                     "说明": ("不在中枢下方（%s）" % pos) if pos != "中枢下方"
                           else ("中枢下方但出现一买 B1 %d 次" % len(b1) if b1 else "中枢下方且无一买")}
        if i % 10 == 0:
            print("  缠论进度 %d/%d" % (i, len(todo)))
    json.dump(out, open(os.path.join(D, "fm_chan.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    n_ok = sum(1 for v in out.values() if v["判定"] == "通过")
    print("缠论完成 %d 个，通过 %d 个" % (len(out), n_ok))
    for c, v in out.items():
        print("  %-11s %-14s %-5s %-8s B1=%d %s" % (c, v["name"], v.get("位置"),
                                              v["判定"], v.get("B1数", 0), v.get("说明", "")[:30]))


if __name__ == "__main__":
    main()