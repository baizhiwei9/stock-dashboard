# -*- coding: utf-8 -*-
"""定稿「将军基本面拓扑选股」名单：扩散行业 → 行业内按一致预期2027E增速取前二

输出 config.json（行业/个股名单 + 现价 + 估值 + 入选依据 + 口径）
"""
import json
import os

WS = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5e722c9a84ffeEPBhVrM9DzQb6b"
OUT = os.path.join(WS, "将军基本面拓扑选股")
os.makedirs(OUT, exist_ok=True)

merged = json.load(open(os.path.join(WS, "merged_verdict.json"), encoding="utf-8"))
cons = {r["code"]: r for r in json.load(open(os.path.join(WS, "consensus_v2.json"), encoding="utf-8"))["rows"]}
flow = json.load(open(os.path.join(WS, "industry_moneyflow.json"), encoding="utf-8"))

# 扩散行业 → 该行业下已核对过的候选池（含一致预期覆盖 >=6 家者）
CANDIDATES = {
    "电网设备与变压器": ["002028.SZ", "600089.SH", "600312.SH", "601179.SH"],
    "铝/电解铝": ["000807.SZ", "002532.SZ", "601600.SH", "000933.SZ"],
    "化学原料": ["600309.SH", "600426.SH", "002648.SZ"],
}
REASON = {
    "002028.SZ": "订单增速超年度目标、海外高端变压器扩产，2027E 增速行业第一",
    "600089.SH": "上半年国际市场产品签约 16.18 亿美元、同比 +44%，估值最低档",
    "000807.SZ": "前三季归母同比 +164%（铝企利润弹性最大），2027E PE 铝企最低",
    "002532.SZ": "2027E 增速铝企最高，承诺 2026 年分红不低于归母净利 50%",
    "600309.SH": "全球 MDI 龙头，前三季归母 156.58 亿元、同比 +70.99%",
    "600426.SH": "2027E 增速化工候选第一，煤化工成本优势",
}
MIN_ORGS = 6

mv = {m["direction"]: m for m in merged["merged"]}
directions = []
for d in ["电网设备与变压器", "铝/电解铝", "化学原料"]:
    m = mv[d]
    assert m["judge"] == "扩散", d + " 非扩散行业，需复核"
    rows = []
    for c in CANDIDATES[d]:
        r = cons.get(c)
        if not r or r["orgs"] < MIN_ORGS or r["g_27"] is None:
            continue
        rows.append({
            "name": r["name"], "code": c, "close": r["close"],
            "pe_ttm": r["pe_ttm"], "pe27E": r["cons"]["2027Q4"]["pe_E"],
            "np27E_yi": r["cons"]["2027Q4"]["np_yi"], "g27": r["g_27"],
            "orgs": r["orgs"], "reason": REASON.get(c, ""),
        })
    rows.sort(key=lambda x: (-(x["g27"] if x["g27"] is not None else -999), x["pe27E"] or 99))
    picked = rows[:2]
    directions.append({
        "direction": d,
        "indices": m["indices"],
        "median_neighbors": m["median_neighbors"],
        "judge": m["judge"],
        "flow_4d_yi": flow["directions"][d]["mf5_yi"],
        "flow_rate": flow["directions"][d]["rate5"],
        "stocks": picked,
        "ranking_note": "行业内按一致预期 2027E 净利增速降序取前二",
    })
    print("== %s（%s，邻数中位 %.1f，4日主力净流入 %.2f 亿）" % (
        d, m["judge"], m["median_neighbors"], flow["directions"][d]["mf5_yi"]))
    for s in picked:
        print("   %-8s %-10s 现价 %7.2f  2027E PE %5.2f  2027E增速 %+5.1f%%  机构 %d 家 | %s" % (
            s["name"], s["code"], s["close"], s["pe27E"], s["g27"], s["orgs"], s["reason"]))

cfg = {
    "report_name": "将军基本面拓扑选股",
    "schedule": "每周日 17:00 (Asia/Shanghai)",
    "asof": "2026-10-30",
    "selection_basis": "先取拓扑口径的扩散行业，再在行业内按卖方一致预期 2027E 归母净利增速取前二；"
                       "增速并列时取 2027E PE 更低者。名单为 2026-10-31 定稿，周度运行只刷新价格与一致预期。",
    "sources": {"拓扑": "tda行业轮动 compute.py（申万 2021 三级，邻居数口径）",
                "一致预期": "TuShare 代理 report_rc（卖方预测中位数）+ daily_basic（估值）",
                "资金流": "TuShare 代理 index_member_all + moneyflow + daily（申万三级成分聚合）"},
    "directions": directions,
}
with open(os.path.join(OUT, "config.json"), "w", encoding="utf-8") as fh:
    json.dump(cfg, fh, ensure_ascii=False, indent=1)
print("written", os.path.join(OUT, "config.json"), "| 行业", len(directions), "| 个股", sum(len(d["stocks"]) for d in directions))