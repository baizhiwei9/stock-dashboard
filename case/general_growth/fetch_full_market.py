# -*- coding: utf-8 -*-
"""全市场取数：申万三级＋同花顺概念的成分映射、全市场估值（两时点）、近5日成交额

落盘到 data/ 下，可断点续跑；失败项登记到 fm_gaps.json
用法: python3 fetch_full_market.py [--out-dir DIR]
"""
import argparse
import json
import os
import sys
import time

import requests

sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/pylibs")
sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f86bdd5bfffeXHo4SFOaOa2sWG/pylibs")
import ts_config  # noqa

TOKEN, URL = ts_config.TS_TOKEN, ts_config.TS_HTTP_URL
S = requests.Session()
GAPS = []


def call(api, params, retry=4):
    last = ""
    for i in range(retry + 1):
        try:
            time.sleep(0.7)
            r = S.post(URL, json={"api_name": api, "token": TOKEN, "params": params,
                                  "fields": ""}, timeout=60)
            j = r.json()
            if j.get("code") == 0:
                return j["data"]
            last = "code=%s msg=%s" % (j.get("code"), (j.get("msg") or "")[:60])
            if "过快" in (j.get("msg") or "") or "超限" in (j.get("msg") or ""):
                time.sleep(12 + 8 * i)
                continue
            break
        except Exception as e:  # noqa
            last = repr(e)[:120]
            time.sleep(3 + 3 * i)
    GAPS.append({"api": api, "params": params, "err": last})
    return None


def save(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False)
    os.replace(tmp, path)


def load(path, default):
    if os.path.exists(path):
        try:
            return json.load(open(path, encoding="utf-8"))
        except Exception:  # noqa
            return default
    return default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="data")
    a = ap.parse_args()
    d = a.out_dir
    os.makedirs(d, exist_ok=True)

    # 1) 行业/概念清单
    p = os.path.join(d, "fm_sw_l3.json")
    if not os.path.exists(p):
        r = call("index_classify", {"level": "L3", "src": "SW2021"})
        rows = [dict(zip(r["fields"], x)) for x in r["items"]] if r else []
        save(p, rows)
    sw = load(p, [])
    p2 = os.path.join(d, "fm_ths_index.json")
    if not os.path.exists(p2):
        r = call("ths_index", {"exchange": "A", "type": "N"})
        rows = [dict(zip(r["fields"], x)) for x in r["items"]] if r else []
        save(p2, rows)
    ths = load(p2, [])
    print("清单：申万三级 %d 个、同花顺概念 %d 个" % (len(sw), len(ths)))

    # 2) 全市场估值（两时点）与近 5 日成交额
    for dt in ["20261030", "20251031"]:
        p3 = os.path.join(d, "fm_basic_%s.json" % dt)
        if os.path.exists(p3):
            continue
        r = call("daily_basic", {"trade_date": dt, "limit": 7000})
        rows = {x[0]: dict(zip(r["fields"], x)) for x in r["items"]} if r else {}
        save(p3, rows)
        print("daily_basic %s：%d 行" % (dt, len(rows)))

    days = ["20261026", "20261027", "20261028", "20261029", "20261030"]
    p4 = os.path.join(d, "fm_amount_last5.json")
    amt = load(p4, {})
    for dt in days:
        if dt in amt and amt[dt]:
            continue
        r = call("daily", {"trade_date": dt, "limit": 7000})
        rows = {x[0]: dict(zip(r["fields"], x)) for x in r["items"]} if r else {}
        if len(rows) < 3000:
            print("[缺口] %s 成交额仅 %d 行，剔除" % (dt, len(rows)))
            continue
        amt[dt] = rows
        save(p4, amt)
        print("daily %s：%d 行" % (dt, len(rows)))

    # 3) 申万三级成分
    p5 = os.path.join(d, "fm_sw_members.json")
    mem = load(p5, {})
    todo = [x for x in sw if x["index_code"] not in mem]
    print("申万三级成分待取：%d 个" % len(todo))
    for i, x in enumerate(todo, 1):
        r = call("index_member_all", {"l3_code": x["index_code"], "limit": 3000})
        rows = [dict(zip(r["fields"], y)) for y in r["items"]] if r else []
        codes = sorted({y["ts_code"] for y in rows if str(y.get("is_new", "")) in ("Y", "1", "True", "")})
        mem[x["index_code"]] = {"name": x["industry_name"], "codes": codes}
        if i % 25 == 0:
            save(p5, mem)
            print("  申万成分进度 %d/%d" % (i, len(todo)))
    save(p5, mem)

    # 4) 同花顺概念成分
    p6 = os.path.join(d, "fm_ths_members.json")
    tmem = load(p6, {})
    todo2 = [x for x in ths if x["ts_code"] not in tmem]
    print("概念成分待取：%d 个" % len(todo2))
    for i, x in enumerate(todo2, 1):
        r = call("ths_member", {"ts_code": x["ts_code"]})
        rows = [dict(zip(r["fields"], y)) for y in r["items"]] if r else []
        codes = sorted({y.get("con_code") or y.get("ts_code") for y in rows if (y.get("con_code") or y.get("ts_code"))})
        tmem[x["ts_code"]] = {"name": x["name"], "codes": codes}
        if i % 25 == 0:
            save(p6, tmem)
            print("  概念成分进度 %d/%d" % (i, len(todo2)))
    save(p6, tmem)

    save(os.path.join(d, "fm_gaps.json"), GAPS)
    n_sw_empty = sum(1 for v in mem.values() if not v["codes"])
    n_ths_empty = sum(1 for v in tmem.values() if not v["codes"])
    print("完成：申万成分 %d 个（空 %d）、概念成分 %d 个（空 %d）、缺口 %d 项" % (
        len(mem), n_sw_empty, len(tmem), n_ths_empty, len(GAPS)))


if __name__ == "__main__":
    main()