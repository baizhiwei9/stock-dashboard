# -*- coding: utf-8 -*-
"""全市场链路：把筛选起点从「方向池」改为全市场行业+概念清单，并写入 config

步骤：L1 全市场 → L2 全产业链（重点行业展开）→ L3 条件过滤 → L4 拓扑标注 → L5 缠论
写入 config: universe / table / focus_check / chain / selection_basis
用法: python3 universe_chain.py [--task-dir DIR]
"""
import argparse
import json
import os

FOCUS_ORDER = ["机器人", "AI硬件", "存储", "光模块"]


def load(p, d=None):
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-dir", default=".")
    a = ap.parse_args()
    T = a.task_dir
    dd = os.path.join(T, "data")
    cfg = json.load(open(os.path.join(T, "config.json"), encoding="utf-8"))

    screen = load(os.path.join(dd, "fm_screen.json"), [])
    chan = load(os.path.join(dd, "fm_chan.json"), {})
    ths_topo = load(os.path.join(dd, "ths_topology.json"), {"topology": {}})["topology"]
    res = load(os.path.join(dd, "sw_topology_src.json"), {"industries": []})
    sw_topo = {x["name"]: x.get("neighbors") for x in res["industries"]}
    fcfg = load(os.path.join(dd, "重点行业口径层级.json"), {"focus": {}})

    def topo(r):
        if r["type"] == "申万三级":
            nb = sw_topo.get(r["name"])
            return (nb, ("聚集" if nb >= 4 else "扩散") if nb is not None else "—")
        t = ths_topo.get(r["code"])
        return ((t["neighbors"], t["judge"]) if t else (None, "—"))

    rows = []
    for r in screen:
        nb, judge = topo(r)
        ch = chan.get(r["code"]) or {}
        base = r["pass_base"]
        cv = ch.get("判定", "未取到")
        chain_pass = bool(base and cv == "通过")
        rows.append({
            "name": r["name"], "kind": r["type"], "code": r["code"],
            "judge": judge, "neighbors": nb, "g_ttm": r["g_median_pct"],
            "pe_ttm": r["pe_median"], "amt5": r["amt5_yi"], "mv_yi": r["mv_yi"],
            "n_members": r["n_members"], "pos_share": r["pos_share_pct"],
            "chan_pos": ch.get("位置", "未取到"), "chan_verdict": cv,
            "cond_pass": base, "chain_pass": chain_pass,
            "cond_fail": [k for k, v in [("体量", r["C1"]), ("盈利", r["C2"]),
                                         ("估值", r["C3"]), ("容量", r["C6"])] if not v],
        })
    cond_rows = [x for x in rows if x["cond_pass"]]
    cond_rows.sort(key=lambda x: (not x["chain_pass"], x["judge"] != "聚集",
                                  -(x["g_ttm"] or 0)))

    # 重点行业全链路核查
    belong = {}
    for f, objs in fcfg.get("focus", {}).items():
        for grp in ("sw_l3", "concept"):
            for o in objs[grp]:
                belong.setdefault(o["code"], {"name": o["name"], "tags": []})
                if f not in belong[o["code"]]["tags"]:
                    belong[o["code"]]["tags"].append(f)
    by_code = {r["code"]: r for r in rows}
    focus_rows = []
    for code, info in belong.items():
        r = by_code.get(code)
        if not r:
            focus_rows.append({"focus": "、".join(info["tags"]), "name": info["name"], "kind": "—",
                               "layer": "L1 全市场", "reason": "不在全市场清单内"})
            continue
        fail = r["cond_fail"]
        if fail:
            layer, reason = "L3 条件过滤", "、".join(
                {"体量": "C1 体量不足", "盈利": "C2 盈利不达标（净利润TTM同比中位≤0 或正增长占比<50%）",
                 "估值": "C3 估值偏高（PE_TTM中位>净利润增速中位）", "容量": "C6 容量不足"}[x] for x in fail)
        elif r["chan_verdict"] == "不通过":
            layer, reason = "L5 缠论", "缠论不通过（指数位于中枢下方且无一买）"
        elif r["chan_verdict"] == "未取到":
            layer, reason = "L5 缠论", "指数行情取不到，缠论待核"
        else:
            layer, reason = "全链通过", "六项条件全部满足"
        focus_rows.append({"focus": "、".join(info["tags"]), "name": r["name"], "kind": r["kind"],
                           "code": code, "g_ttm": r["g_ttm"], "pe_ttm": r["pe_ttm"],
                           "pos_share": r["pos_share"], "mv_yi": r["mv_yi"], "n_members": r["n_members"],
                           "judge": r["judge"], "neighbors": r["neighbors"],
                           "chan_pos": r["chan_pos"], "chan_verdict": r["chan_verdict"],
                           "layer": layer, "reason": reason})

    o = {"全市场": len(rows), "申万三级": sum(1 for r in rows if r["kind"] == "申万三级"),
         "同花顺概念": sum(1 for r in rows if r["kind"] == "概念"),
         "C1体量通过": sum(1 for r in rows if "体量" not in r["cond_fail"]),
         "C2盈利通过": sum(1 for r in rows if "盈利" not in r["cond_fail"]),
         "C3估值通过": sum(1 for r in rows if "估值" not in r["cond_fail"]),
         "C6容量通过": sum(1 for r in rows if "容量" not in r["cond_fail"]),
         "条件全过": len(cond_rows), "全链通过": sum(1 for r in cond_rows if r["chain_pass"]),
         "缠论待核": sum(1 for r in cond_rows if r["chan_verdict"] == "未取到"),
         "缠论不通过": sum(1 for r in cond_rows if r["chan_verdict"] == "不通过")}

    import chart_order
    capp = int(cfg.get("max_stocks_per_chart") or chart_order.DEFAULT_CAP)
    plan = chart_order.plan(cfg.get("directions", []), capp)
    json.dump(plan, open(os.path.join(dd, "chart_plan.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    cfg["chart_order_rule"] = chart_order.ORDER_RULE
    cfg["max_stocks_per_chart"] = plan["cap"]
    cfg.setdefault("chart_variant", "按买点排序")

    cfg["universe"] = o
    cfg["table"] = cond_rows
    cfg["focus_check"] = focus_rows
    cfg["table_asof"] = cfg.get("asof", "2026-10-30")
    cfg["selection_basis"] = (
        "起点＝全市场行业与概念清单（申万 2021 三级 %d 个 ＋ 同花顺概念 %d 个，共 %d）；"
        "按体量/盈利/估值/容量四项条件过滤后余 %d 个；拓扑结构按 60 日收益相关邻居数标注；"
        "行业与概念指数过缠论后余 %d 个（另有 %d 个因指数行情取不到待核）。"
        % (o["申万三级"], o["同花顺概念"], o["全市场"], o["条件全过"], o["全链通过"], o["缠论待核"]))
    steps = [
        "① 全市场：起点为申万 2021 三级行业 %d 个 ＋ 同花顺概念 %d 个，共 %d 个对象（不再以给定方向池为起点）。"
        % (o["申万三级"], o["同花顺概念"], o["全市场"]),
        "② 全产业链：对重点行业（机器人、AI 硬件、存储、光模块）展开到对应申万三级行业与同花顺概念，"
        "逐层核查并记录被剔除的层级与原因。",
        "③ 行业概念（条件过滤）：C1 体量（合计总市值≥500 亿且有效成分≥5）／C2 盈利（净利润TTM同比中位>0 且正增长占比≥50%%）"
        "／C3 估值（PE_TTM中位≤净利润增速中位）／C6 容量（近 5 日成交额≥20 亿）；通过 %d 个"
        "（单项通过数：体量 %d、盈利 %d、估值 %d、容量 %d）。"
        % (o["条件全过"], o["C1体量通过"], o["C2盈利通过"], o["C3估值通过"], o["C6容量通过"]),
        "④ 拓扑结构：对通过对象计算 60 日收益相关系数大于 0.8 的邻居数，≥4 判“聚集”、≤3 判“扩散”，"
        "作为结构标注并列展示（不删行，取舍交下游）。",
        "⑤ 缠论过滤：对行业/概念指数跑缠论分型版（MACD 20/40/5），要求收盘不在最近中枢下方、"
        "或位于中枢下方但出现一买（B1）；通过 %d 个，另 %d 个因指数行情取不到待核。" % (o["全链通过"], o["缠论待核"]),
        "⑥ 行业内选股：在缠论通过的对象内按候选顺位取个股——要求卖方研报覆盖 ≥6 家、增速与估值匹配"
        "（PEG≤1：2027E PE≤2027E 净利增速且增速>0；若净利TTM同比>0，再加 PE_TTM≤净利TTM同比），每对象至多 2 只。",
        "⑦ 个股多归属核验：个股往往同属多个行业/概念，须按每只个股的**全部所属行业/概念**（申万 2021 三级＋同花顺概念，"
        "剔除指数样本股/成份股/两融/互联互通等非主题归属）逐项核验增速-估值匹配（PEG≤1），给出归属数、匹配数与匹配率并逐项列示；"
        "不得只按被选中的那一个归属对象说明。",
        "⑦ 个股级缠论过滤：对入选个股逐只做同样的缠论判定，不满足者用同口径下一顺位候选替换并复检。",
        "⑧ 展示范围与分图（规则内置）：展示全部全链通过对象（本轮 %d 个、个股 %d 只，不再按增速截断）；"
        "每图最多 %d 只个股子图，按个股买点等级降序（三买 > 二买 > 一买 > 突破中枢 > 中枢内部 > 其他）"
        "切分为 %d 张（第k张共n张），每图底部均带全市场收敛对照表。" % (
            len(cfg.get("directions", [])), len(plan["order"]), plan["cap"], plan["n_images"]),
    ]
    chain = cfg.get("chain") or {}
    chain["steps"] = steps
    chain["verify"] = [
        "全市场口径交叉核对：对象数、各条件通过数、最终通过数由脚本从同一份冻结快照计算，正文与表格引用同一份数字；",
        "重点行业逐层可追溯：每个重点行业对象在明细表中标出被剔除的层级（L3 条件/L5 缠论）与具体原因，无“未说明即剔除”；",
        "一致预期口径：个股入选取卖方研报预测中位数且要求机构覆盖不少于 6 家，覆盖不足的个股不计入；",
        "增速-估值匹配：个股层强制 PEG≤1（2027E PE≤2027E净利增速；净利TTM同比>0 时再加 PE_TTM≤净利TTM同比），"
        "不匹配者剔除并顺位替换；行业/概念层由 C3（PE_TTM中位≤净利增速中位）承担，两者均为硬条件；",
        "个股多归属：每只入选个股在对照表/个股表与报告正文中列出全部所属行业/概念及各自的增速-估值匹配结论，并给出匹配率；"
        "核验不得只覆盖被选中的单一归属。",
        "缠论标注自洽：个股子图均画出缠论结构（中枢矩形＋上/下沿虚线、笔折线、买卖点标记），标注以当前中枢状态为主"
        "（突破中枢／中枢内部／跌破中枢／无中枢），买点仅在类型与状态自洽时标注（三买需突破中枢上沿、二买需守住下沿、"
        "一买用于跌破中枢或无中枢）；gate_table.py 对该一致性做硬校验。",
        "缠论口径：行业级与个股级均用缠论分型版（MACD 20/40/5、信号当根确认），判定“不在中枢下方或出现一买”；",
        "缺口如实登记：指数行情取不到的对象标“待核”，不以估计值或 0 冒充；",
        "分图口径：出图顺序与分图数量由 chart_order.plan 统一生成并写入 data/chart_plan.json（按买点等级降序、每图最多 %d 只），"
        "gen_chart.py 按同一计划出图，gate_table.py 校验分图数量、单图只数上限与排序单调性。" % capp,
    ]
    chain["falsify"] = {x["name"]: [x["reason"] if x["layer"] != "全链通过"
                                    else "若跌破最近中枢下沿且不出现一买，则本轮入选失效"]
                        for x in focus_rows if x.get("reason")}
    cfg["chain"] = chain
    json.dump(cfg, open(os.path.join(T, "config.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    json.dump({"universe": o, "table": cond_rows, "focus_check": focus_rows},
              open(os.path.join(dd, "universe_chain.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("全市场统计:", json.dumps(o, ensure_ascii=False))
    print("对照表行 %d，重点行业核查行 %d" % (len(cond_rows), len(focus_rows)))


if __name__ == "__main__":
    main()