# -*- coding: utf-8 -*-
"""缠论分型版校验：对入选个股（含同行业备用候选）判定中枢位置与买卖点

输出 chan_results.json
用法: python3 chan_check.py [--task-dir DIR] [--start 20250101]
"""
import argparse
import json
import os
import sys
import time

import pandas as pd
import requests

WS = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5e722c9a84ffeEPBhVrM9DzQb6b"
SKILL = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/skills/user/chan-fenxing-ban"
sys.path.insert(0, os.path.join(WS, "pylibs"))
sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f86bdd5bfffeXHo4SFOaOa2sWG/pylibs")
sys.path.insert(0, SKILL)
import ts_config  # noqa
from chan_fractal import analyze  # noqa

TOKEN, URL = ts_config.TS_TOKEN, ts_config.TS_HTTP_URL
S = requests.Session()

# 主选 6 只 + 同行业下一顺位备用候选
PRIMARY = {
    "电网设备与变压器": [("思源电气", "002028.SZ"), ("特变电工", "600089.SH")],
    "铝/电解铝": [("天山铝业", "002532.SZ"), ("云铝股份", "000807.SZ")],
    "化学原料": [("华鲁恒升", "600426.SH"), ("万华化学", "600309.SH")],
}
BACKUP = {
    "电网设备与变压器": [("平高电气", "600312.SH"), ("中国西电", "601179.SH")],
    "铝/电解铝": [("中国铝业", "601600.SH"), ("神火股份", "000933.SZ")],
    "化学原料": [("卫星化学", "002648.SZ")],
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-dir", default=".")
    ap.add_argument("--start", default="20250101")
    a = ap.parse_args()
    data_dir = os.path.join(a.task_dir, "data", "ohlcv")
    os.makedirs(data_dir, exist_ok=True)

    tdate = "20261030"
    rows_out = []
    todo = []
    for grp in (PRIMARY, BACKUP):
        for d, lst in grp.items():
            for nm, code in lst:
                todo.append((d, nm, code, "主选" if grp is PRIMARY else "备用"))

    for d, nm, code, kind in todo:
        f, it = call("daily", {"ts_code": code, "start_date": a.start, "end_date": tdate})
        rows = [dict(zip(f, r)) for r in it] if f else []
        rows.sort(key=lambda x: x["trade_date"])
        if len(rows) < 120:
            print("[warn] %s 数据不足 %d 根" % (code, len(rows)))
        csv_p = os.path.join(data_dir, "%s.csv" % code)
        with open(csv_p, "w", encoding="utf-8") as fh:
            fh.write("date,open,high,low,close,vol\n")
            for r in rows:
                fh.write("%s,%s,%s,%s,%s,%s\n" % (r["trade_date"], r["open"], r["high"],
                                                  r["low"], r["close"], r.get("vol", 0)))
        df = pd.read_csv(csv_p)
        res = analyze(df, level="day", backtest=False, name="%s(%s)" % (nm, code))
        zs = (res.get("结构") or {}).get("中枢") or []
        sig = res.get("交易机会") or []
        close = float(rows[-1]["close"])
        last_zs = zs[-1] if zs else None
        if last_zs:
            zd, zg = float(last_zs["下沿ZD"]), float(last_zs["上沿ZG"])
            if close > zg:
                pos = "中枢上方"
            elif close >= zd:
                pos = "中枢内部"
            else:
                pos = "中枢下方"
        else:
            zd = zg = None
            pos = "无中枢"
        b1 = [s for s in sig if s.get("类型") == "B1"]
        recent = sig[-4:]
        below_ok = (pos != "中枢下方") or bool(b1)
        out = {
            "direction": d, "name": nm, "code": code, "kind": kind,
            "bars": len(rows), "close": close, "中枢数": len(zs),
            "最近中枢": {"ZD": zd, "ZG": zg,
                     "区间": last_zs.get("起止时间")} if last_zs else None,
            "位置": pos,
            "距中枢上沿": round((close / zg - 1) * 100, 2) if zg else None,
            "距中枢下沿": round((close / zd - 1) * 100, 2) if zd else None,
            "笔数": len((res.get("结构") or {}).get("笔端点") or []),
            "买卖点总数": len(sig),
            "B1数": len(b1),
            "最近信号": [{"时间": s.get("时间"), "类型": s.get("类型"), "价格": s.get("价格")} for s in recent],
            "判定": "通过" if below_ok else "不通过",
            "判定说明": ("当前%s，满足“不在中枢下方”" % pos) if pos != "中枢下方"
                    else ("当前在中枢下方，但已出现一买（B1 %d 次）" % len(b1) if b1
                          else "当前在中枢下方且无 B1，不满足门槛"),
        }
        rows_out.append(out)
        print("%-8s %-9s %-6s 收盘 %8.2f 中枢数 %2d 位置 %-6s 距上沿 %s%% B1 %d | %s" % (
            nm, code, kind, close, len(zs), pos,
            ("%+.1f" % ((close / zg - 1) * 100)) if zg else "-", len(b1), out["判定"]))
        time.sleep(0.4)

    json.dump({"asof": tdate, "window_start": a.start, "macd": "20,40,5",
               "rule": "当前不在中枢下方，或位于中枢下方但出现一买(B1)",
               "results": rows_out},
              open(os.path.join(a.task_dir, "chan_results.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("written chan_results.json | 通过 %d / 共 %d" % (
        sum(1 for r in rows_out if r["判定"] == "通过"), len(rows_out)))


if __name__ == "__main__":
    main()