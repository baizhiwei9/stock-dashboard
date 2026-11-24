# -*- coding: utf-8 -*-
"""生成「行业筛选链条」文案（选／验证／证伪），写入 config.json 的 chain 段并落一份 Markdown

用法: python3 build_chain.py [--task-dir DIR]
"""
import argparse
import json
import os

TABLE_ORDER = ["覆铜板/电子布/CCL", "电网设备与变压器", "燃气轮机", "铝/电解铝", "创新药", "化学原料"]
FALSIFY = {
    "电网设备与变压器": [
        "证伪线：海外新增订单或长协不落地、项目交付延期，或国内电网招标出现价格战；",
        "跟踪项：季度新增与在手订单、变压器月度出口金额、海外数据中心资本开支指引。",
    ],
    "铝/电解铝": [
        "证伪线：国内产能管控松动，或需求（地产、出口）快速走弱、氧化铝反弹挤压价差，单吨盈利跌破 8000 元；",
        "跟踪项：电解铝与氧化铝价差、社会库存与 LME 库存、产能运行率。",
    ],
    "化学原料": [
        "证伪线：同行集中扩产、下游需求转弱，或公司自述“涨价到头”；",
        "跟踪项：重点品种价差与开工率、库存天数、季度毛利率。",
    ],
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-dir", default=".")
    a = ap.parse_args()
    p = os.path.join(a.task_dir, "config.json")
    cfg = json.load(open(p, encoding="utf-8"))
    tbl = {r["direction"]: r for r in cfg["table"]}

    agg = [d for d in TABLE_ORDER if tbl[d]["judge"] == "聚集"]
    dif = [d for d in TABLE_ORDER if tbl[d]["judge"] == "扩散"]
    steps = [
        "① 候选池：以你给定的方向池为起点（PCB／CCL／电子布、ABF 与 IC 载板、钻针与打孔设备、存储、"
        "燃气轮机、电网设备与变压器、大储与储能上游、铝、创新药、机器人、端侧 AI、商业航天），"
        "并按你的授权增补化学原料；行业口径统一落到申万 2021 三级（316 个唯一指数）以便同口径计算。",
        "② 第一道刀（体量／竞争力／基本面）：剔除利润池与全球地位不达标、或缺少可验证分子的方向——"
        "端侧 AI（无一致口径）、商业航天（星座完成度不足 3%、订单只有局部证据）、"
        "机器人（宇树三季报仍亏损，叙事先于兑现）、大储与储能上游（电芯同质化、含税价环比下行、同行一起扩产）、"
        "存储（模组单季净利环比回落超三成、涨价斜率收窄）、钻针与打孔设备（容量不足，只宜交易）。",
        "③ 第二道刀（拓扑结构）：对保留方向对应的申万三级指数计算最近 60 日收益相关矩阵的邻居数"
        "（相关系数大于 0.8 的行业个数），方向取对应三级指数中位数，≥4 判“聚集”、≤3 判“扩散”。"
        "结果：聚集 %d 个（%s）、扩散 %d 个（%s）。" % (
            len(agg), "、".join(agg), len(dif), "、".join(dif)),
        "④ 第三道刀（只在扩散方向里选＋估值匹配）：本轮只取扩散方向，因为聚集意味着与大盘同向、"
        "资金集体进出，而扩散意味着走势与大盘脱钩、有独立资金的容忍度；"
        "再要求“增长与订单能对上估值”，最终保留：" +
        "；".join("%s（2027E PE 中位 %.1f 倍／增速中位 %+.1f%%）" % (
            d, tbl[d]["pe27_median"], tbl[d]["g27_median"]) for d in dif) + "。",
        "⑤ 第四道刀（行业内选股）：在入选行业内按卖方一致预期 2027E 净利增速排序取前二，"
        "并列时取 2027E PE 更低者，得到 3 个行业 × 2 只 = 6 只。",
        "⑥ 第五道刀（缠论买卖点）：对这 6 只做缠论分型版结构判定，要求“当前不在中枢下方，"
        "或位于中枢下方但已出现一买（B1）”，不满足者用同行业下一顺位候选替换并复检。",
    ]
    verify = [
        "数值交叉核对：现价与估值由 daily_basic 原始响应独立复算，结果与名单一致；",
        "预期口径：一致预期取卖方研报预测中位数且要求机构覆盖不少于 6 家，覆盖不足的个股不计入；",
        "同源单一真源：对照表与名单共用同一份 config，报告内所有数字由脚本计算并落盘，不手填；",
        "结构可复现：邻居数按“相关系数大于 0.8 计数”固定阈值复算，并用类内相关性抽样校验判定方向；",
        "资金流口径分离：主力净流入按申万三级成分股聚合（大单＋特大单买卖差），与个股口径分开披露。",
    ]
    cfg["chain"] = {"steps": steps, "verify": verify,
                    "falsify": {d: FALSIFY[d] for d in dif}}
    json.dump(cfg, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    md = ["# 行业筛选链条（选／验证／证伪）", "", "## 一、怎么选出来的"]
    md += ["%d. %s" % (i + 1, s) for i, s in enumerate(steps)]
    md += ["", "## 二、怎么验证", ""] + ["- " + v for v in verify]
    md += ["", "## 三、怎么证伪", ""]
    for d in dif:
        md.append("**%s**" % d)
        md += ["- " + x for x in FALSIFY[d]]
        md.append("")
    with open(os.path.join(a.task_dir, "筛选链条.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(md))
    print("筛选链条写入 config.chain（步骤 %d、验证 %d、证伪 %d 个行业）" % (
        len(steps), len(verify), len(dif)))
    for s in steps:
        print(" · " + s[:70] + ("…" if len(s) > 70 else ""))


if __name__ == "__main__":
    main()