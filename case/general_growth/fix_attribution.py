# -*- coding: utf-8 -*-
"""按多归属口径重算个股归属核验（不取数）：把每只入选个股的全部所属行业/概念与其增速-估值匹配写入 config

用于把既有 config.directions 升级为“个股按全部所属行业/概念逐项核验匹配”。
用法: python3 fix_attribution.py [--task-dir DIR]
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from select_stocks import attributions, build_rev  # noqa


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-dir", default=".")
    a = ap.parse_args()
    T = a.task_dir
    dd = os.path.join(T, "data")
    cfg = json.load(open(os.path.join(T, "config.json"), encoding="utf-8"))
    screen = {r["code"]: r for r in json.load(open(os.path.join(dd, "fm_screen.json"), encoding="utf-8"))}
    nm_sw = {x["index_code"]: x["industry_name"] for x in json.load(open(os.path.join(dd, "fm_sw_l3.json"), encoding="utf-8"))}
    nm_ths = {x["ts_code"]: x["name"] for x in json.load(open(os.path.join(dd, "fm_ths_index.json"), encoding="utf-8"))}
    sw_mem = json.load(open(os.path.join(dd, "fm_sw_members.json"), encoding="utf-8"))
    ths_mem = json.load(open(os.path.join(dd, "fm_ths_members.json"), encoding="utf-8"))
    rev = build_rev(sw_mem, ths_mem, nm_sw, nm_ths)
    chain_map = {r["code"]: r["chain_pass"] for r in cfg.get("table", [])}

    for d in cfg.get("directions", []):
        for s in d["stocks"]:
            att = attributions(s["code"], rev, screen, chain_map)
            okn = sum(1 for x in att if x["匹配"] == "匹配")
            s["归属"] = {"归属数": len(att), "归属匹配数": okn, "归属不匹配数": len(att) - okn,
                         "归属匹配率": round(okn / len(att) * 100, 1) if att else None, "明细": att}
            top = "、".join("%s" % x["归属对象"] for x in att[:3])
            print("  %-6s 归属 %2d 个（匹配 %2d）｜ 示例：%s" % (s["name"], len(att), okn, top))

    cfg["attribution_rule"] = ("个股多归属核验：每只入选个股按其**全部所属行业/概念**（申万 2021 三级＋同花顺概念，"
                              "剔除指数样本股/成份股/两融/互联互通等非主题归属）逐项核验增速-估值匹配（PE_TTM中位≤净利TTM同比中位，"
                              "即 PEG≤1），给出归属对象数、匹配数与匹配率并逐项列示；不得只按被选中的那一个对象说明。")
    json.dump(cfg, open(os.path.join(T, "config.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("已写入 config.directions 的个股多归属核验")


if __name__ == "__main__":
    main()