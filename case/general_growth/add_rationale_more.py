# -*- coding: utf-8 -*-
"""补齐 燃气轮机 的行业依据模板（其余 5 个行业已有模板）

用法: python3 add_rationale_more.py [--task-dir DIR]
"""
import argparse
import json
import os

NEW = {
    "燃气轮机": [
        "增长与订单：AI 算力带动数据中心用电，海外燃机需求快速上升——2026 年全球需求有望突破 117GW，"
        "而主流厂商产能仅 55—60GW；东方电气前三季新增生效订单 934.75 亿元、6 月末在手订单 1548.66 亿元；"
        "GE Vernova 26Q3 新签订单 16.6GW（同比 +124%）。",
        "估值匹配：行业中位 2027E PE 约 {pe} 倍，对应 2027E 净利增速中位约 {g}%。",
        "拓扑与资金：能源及重型设备 60 日邻居数 26（≥4 判聚集，与大盘同向）；"
        "近 {days} 个交易日主力净流入 {mf} 亿元（净占比 {rate}%）。",
    ],
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-dir", default=".")
    a = ap.parse_args()
    p = os.path.join(a.task_dir, "config.json")
    cfg = json.load(open(p, encoding="utf-8"))
    cfg.setdefault("rationale", {})
    for k, v in NEW.items():
        cfg["rationale"][k] = v
    json.dump(cfg, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    need = [r["direction"] for r in cfg["table"]]
    print("依据模板覆盖检查（全部选出行业）:")
    for d in need:
        print("  %-14s %s" % (d, "有" if d in cfg["rationale"] else "缺"))


if __name__ == "__main__":
    main()