# -*- coding: utf-8 -*-
"""按自洽口径重算个股缠论标注（不取数）：state 为主标签，买点仅在类型与状态自洽时附加

用于把既有 config.directions 的标注升级为新口径（三买需突破中枢上沿等）。
用法: python3 fix_labels.py [--task-dir DIR]
"""
import argparse
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from select_stocks import chan_label, consistent_buys, dstr  # noqa


def last_close(task, code):
    p = os.path.join(task, "data", "ohlcv", "%s.csv" % code.replace(".", "_"))
    rows = [r for r in csv.DictReader(open(p, encoding="utf-8")) if r.get("close")]
    return float(rows[-1]["close"]), rows[-1]["date"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-dir", default=".")
    a = ap.parse_args()
    T = a.task_dir
    cfg = json.load(open(os.path.join(T, "config.json"), encoding="utf-8"))
    for d in cfg.get("directions", []):
        for s in d["stocks"]:
            close, dt = last_close(T, s["code"])
            cs = s.get("chan_struct") or {}
            zs = cs.get("中枢") or []
            sig = cs.get("买卖点") or []
            pos, label = chan_label(close, zs, sig)
            lz = zs[-1] if zs else None
            cbs = ["%s@%s" % (x["类型"], dstr(x["时间"])) for x in consistent_buys(close, zs, sig)]
            s["chan"] = {"位置": pos, "状态": pos, "一致买点": cbs,
                         "ZD": float(lz.get("ZD", lz.get("下沿ZD"))) if lz else None,
                         "ZG": float(lz.get("ZG", lz.get("上沿ZG"))) if lz else None,
                         "最近买点": label, "说明": "结构自洽标注：%s" % label}
            s["close"] = round(close, 2)
            print("  %-8s %-6s 现价%7.2f → %s" % (d["direction"][:6], s["name"], close, label))
    cfg["chan_label_rule"] = ("当前中枢状态为主标签（突破中枢／中枢内部／跌破中枢／无中枢）；"
                             "买点仅在类型与状态自洽时附加：三买需信号价突破中枢上沿、二买需守住下沿、"
                             "一买用于跌破中枢或无中枢；不自洽时只给状态。")
    json.dump(cfg, open(os.path.join(T, "config.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("已按自洽口径更新 config.directions 的缠论标注")


if __name__ == "__main__":
    main()