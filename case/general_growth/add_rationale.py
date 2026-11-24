# -*- coding: utf-8 -*-
"""补充新入选行业（覆铜板链、创新药）的行业依据文案模板

用法: python3 add_rationale.py [--task-dir DIR]
"""
import argparse
import json
import os

NEW = {
    "覆铜板/电子布/CCL": [
        "增长与订单：金安国纪前三季归母 19.24 亿元（+1012.67%）、毛利率 41.69%（去年同期 12.11%）；"
        "生益科技上半年营收 190.26 亿元（+50.05%）；TPCA 估 2027 年玻纤布／CCL／ABF 载板缺口或达 20%。",
        "估值匹配：行业中位 2027E PE 约 {pe} 倍，对应 2027E 净利增速中位约 {g}%，增长高于估值倍数。",
        "拓扑与资金：印制电路板、被动元件 60 日邻居数 26、12（≥4 判聚集，与大盘同向）；"
        "近 {days} 个交易日主力净流入 {mf} 亿元（净占比 {rate}%）。",
    ],
    "创新药": [
        "增长与订单：恒瑞医药前三季归母 69.23 亿元（+20.38%）、对外许可收入 27.81 亿元（+31.12%）；"
        "百济神州 2027E 一致预期增速 63.7%。",
        "估值匹配：行业中位 2027E PE 约 {pe} 倍，对应 2027E 增速中位约 {g}%。",
        "拓扑与资金：化学制剂、其他生物制品 60 日邻居数 5、6（判聚集）；"
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
    print("已补充依据模板:", list(NEW.keys()))
    print("当前拥有依据模板的方向:", [d["direction"] for d in cfg["directions"]
                                if d["direction"] in cfg["rationale"]])


if __name__ == "__main__":
    main()