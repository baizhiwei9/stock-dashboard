# -*- coding: utf-8 -*-
"""分图排序与分图规则的唯一实现（供 select_stocks / universe_chain / gen_chart / gate_table 共用）

买点等级：三买5 > 二买4 > 一买3 > 突破中枢2 > 中枢内部1 > 其他0
出图顺序：按买点等级降序；每图最多 max_stocks_per_chart（默认 20）只，超出按“第k张共n张”拆分。
"""
import math

BUY_NAME = {"B1": "一买", "B2": "二买", "B3": "三买"}
DEFAULT_CAP = 20
ORDER_RULE = ("出图顺序：按个股买点等级降序——三买 > 二买 > 一买 > 突破中枢 > 中枢内部 > 其他；"
              "每图最多 %d 只个股子图，超出按“第k张共n张”拆分；每图底部均带全市场收敛对照表。" % DEFAULT_CAP)


def bp_rank(s):
    """个股买点等级（用于分图排序，有买点者靠前）"""
    ch = s.get("chan") or {}
    lb = str(ch.get("最近买点") or "")
    st = str(ch.get("状态") or ch.get("位置") or "")
    if "三买" in lb:
        return 5
    if "二买" in lb:
        return 4
    if "一买" in lb:
        return 3
    if st == "突破中枢":
        return 2
    if st == "中枢内部":
        return 1
    return 0


RANK_NAME = {5: "三买", 4: "二买", 3: "一买", 2: "突破中枢", 1: "中枢内部", 0: "其他"}


def plan(directions, cap=DEFAULT_CAP):
    """生成分图计划：按买点等级降序排列 + 每图最多 cap 只切分"""
    items = []
    for d in directions:
        for s in d.get("stocks", []):
            items.append({"direction": d["direction"], "code": s["code"], "name": s["name"],
                          "rank": bp_rank(s)})
    items.sort(key=lambda x: -x["rank"])  # 稳定排序，同级保持原顺序
    chunks = [items[i:i + cap] for i in range(0, len(items), cap)] or [[]]
    return {"cap": cap,
            "rule": ORDER_RULE,
            "order": items,
            "n_images": len(chunks),
            "per_image": [len(c) for c in chunks],
            "images": [[{"direction": x["direction"], "code": x["code"], "name": x["name"],
                         "rank": x["rank"]} for x in c] for c in chunks]}


def n_images(n_stocks, cap=DEFAULT_CAP):
    return max(1, math.ceil(n_stocks / cap)) if cap else 1
