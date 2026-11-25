# -*- coding: utf-8 -*-
"""PMI 阈值判定 + 仓位分级 + 分析报告生成模块

输入：data/pmi_latest.json（pmi_fetch.py 产出）
输出：
  - data/pmi_analysis.json（结构化判定结果，供卡片/邮件复用）
  - output/pmi_report_{YYYYMM}.txt（分析报告文本）

阈值表（用户原始限定，数字不许改）：
  1. 新订单 > 50
  2. 新订单环比 > +1.0pt
  3. 新订单 MA12 同比 > 0（代理 = MA12(t) − MA12(t−12)）
  4. 新订单 − 新出口订单 > +5
  5. 新订单 − 生产 > +2
  6. 服务业新订单 > 52 或 < 48 【正常/强/弱】
  7. 基础原材料新订单 > 51 或 < 49 【正常/强/弱】

触发动作映射：
  A. 新订单<50 且环比<−1.0            -> 周度国债期货偏多，T 不追空等回踩做多
  B. 新订单>50 且环比>+1.0 且 MA12同比>0 -> 周度债偏空，T 反弹空
  C. 新订单−生产>+2                    -> 补库周期启动，利空债利多工业品
  D. 服务业新订单<48                   -> 消费塌避险买债
  E. 基础原材料新订单<49               -> 商品需求证伪，空黑色/有色

仓位分级（本次新增；可解释加权口径）：
  - 核心方向动作 A/B/E 权重 2；次要动作 C/D 权重 1
  - 总分 0 -> 绿（正常）；1-2 -> 黄（警戒）；>=3 -> 红（高风险）
  - 黄及以上触发邮件提醒

硬规则：
  - 原文未给字段标【原文缺失】，绝不编造
  - MA12 同比历史不足标【需补12个月序列】
  - 阈值数字不许改
"""
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import DATA_DIR, OUT_DIR

PMI_JSON = os.path.join(DATA_DIR, "pmi_latest.json")
ANALYSIS_JSON = os.path.join(DATA_DIR, "pmi_analysis.json")

CORE_ACTIONS = {"A", "B", "E"}  # 核心方向动作，权重 2
MINOR_ACTIONS = {"C", "D"}       # 次要动作，权重 1

ACTIONS_DESC = {
    "A": "周度国债期货偏多，T 合约不追空、等回踩做多",
    "B": "周度债偏空，T 合约反弹空",
    "C": "补库周期启动，利空债偏利多工业品",
    "D": "消费塌，避险配置买债",
    "E": "商品需求证伪，空黑色/有色",
}


def _median(vals):
    s = sorted(vals)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def ma12_series(series, key="new_orders"):
    """返回 {month: MA12}；要求该月往前连续 12 个月都有值。"""
    months = sorted(series.keys())
    out = {}
    for i in range(11, len(months)):
        win = months[i - 11:i + 1]
        vals = [series[m].get(key) for m in win]
        if all(v is not None for v in vals):
            out[months[i]] = sum(vals) / len(vals)
    return out


def evaluate(series, latest, missing):
    months = sorted(series.keys())
    latest_month = months[-1]
    prev_month = months[-2] if len(months) >= 2 else None

    def val(key):
        return latest.get(key)

    new_orders = val("new_orders")
    production = val("production")
    new_export = val("new_export_orders")
    service_no = val("service_new_orders")
    basic_rm_no = latest.get("basic_rm_new_orders")  # 正文可能缺失

    # 环比
    mom = None
    if new_orders is not None and prev_month:
        pv = series[prev_month].get("new_orders")
        if pv is not None:
            mom = round(new_orders - pv, 2)

    # MA12 同比：代理 = MA12(t) - MA12(t-12)
    ma12 = ma12_series(series)
    ma12_now = ma12.get(latest_month)
    t_prev = months[-13] if len(months) >= 13 else None
    ma12_prev = ma12.get(t_prev) if t_prev else None
    ma12_yoy = None
    ma12_yoy_ok = False
    if ma12_now is not None and ma12_prev is not None:
        ma12_yoy = round(ma12_now - ma12_prev, 2)
        ma12_yoy_ok = True

    # ---- 7 项阈值判定 ----
    checks = []
    checks.append({
        "id": 1, "name": "新订单 > 50",
        "key": "new_orders", "value": new_orders,
        "formula": f"新订单 = {fmt(new_orders)}", "hit": new_orders is not None and new_orders > 50,
        "note": ""})
    checks.append({
        "id": 2, "name": "新订单环比 > +1.0pt",
        "key": "mom", "value": mom,
        "formula": f"环比 = {fmt(mom)}" + (f"（{prev_month}→{latest_month}）" if prev_month else ""),
        "hit": mom is not None and mom > 1.0, "note": ""})
    if ma12_yoy_ok:
        checks.append({
            "id": 3, "name": "新订单 MA12 同比 > 0",
            "key": "ma12_yoy", "value": ma12_yoy,
            "formula": f"MA12同比 = {fmt(ma12_yoy)}（MA12({latest_month})−MA12({t_prev})）",
            "hit": ma12_yoy is not None and ma12_yoy > 0, "note": ""})
    else:
        checks.append({
            "id": 3, "name": "新订单 MA12 同比 > 0",
            "key": "ma12_yoy", "value": None,
            "formula": "【需补12个月序列】",
            "hit": None, "note": "历史序列不足 24 个月，无法计算 MA12 同比"})
    checks.append({
        "id": 4, "name": "新订单 − 新出口订单 > +5",
        "key": "no_minus_nexp", "value": (round(new_orders - new_export, 2)
                                          if new_orders is not None and new_export is not None else None),
        "formula": f"{fmt(new_orders)} − {fmt(new_export)} = {fmt(round(new_orders - new_export, 2) if new_orders is not None and new_export is not None else None)}",
        "hit": (new_orders is not None and new_export is not None
                and new_orders - new_export > 5), "note": ""})
    checks.append({
        "id": 5, "name": "新订单 − 生产 > +2",
        "key": "no_minus_prod", "value": (round(new_orders - production, 2)
                                          if new_orders is not None and production is not None else None),
        "formula": f"{fmt(new_orders)} − {fmt(production)} = {fmt(round(new_orders - production, 2) if new_orders is not None and production is not None else None)}",
        "hit": (new_orders is not None and production is not None
                and new_orders - production > 2), "note": ""})
    # 服务业新订单：>52 强 / 48-52 正常 / <48 弱
    if service_no is not None:
        if service_no > 52:
            svc_state, svc_hit = "强", True
        elif service_no < 48:
            svc_state, svc_hit = "弱", True
        else:
            svc_state, svc_hit = "正常", False
        checks.append({
            "id": 6, "name": "服务业新订单（>52 强 / <48 弱）",
            "key": "service_new_orders", "value": service_no,
            "formula": f"服务业新订单 = {fmt(service_no)}", "hit": svc_hit,
            "note": f"状态：{svc_state}"})
    else:
        checks.append({
            "id": 6, "name": "服务业新订单（>52 强 / <48 弱）",
            "key": "service_new_orders", "value": None,
            "formula": "【原文缺失】", "hit": None, "note": "正文未给出，无法判定"})
    # 基础原材料新订单：>51 强 / <49 弱
    if basic_rm_no is not None:
        if basic_rm_no > 51:
            brm_state, brm_hit = "强", True
        elif basic_rm_no < 49:
            brm_state, brm_hit = "弱", True
        else:
            brm_state, brm_hit = "正常", False
        checks.append({
            "id": 7, "name": "基础原材料新订单（>51 强 / <49 弱）",
            "key": "basic_rm_new_orders", "value": basic_rm_no,
            "formula": f"基础原材料新订单 = {fmt(basic_rm_no)}", "hit": brm_hit,
            "note": f"状态：{brm_state}"})
    else:
        checks.append({
            "id": 7, "name": "基础原材料新订单（>51 强 / <49 弱）",
            "key": "basic_rm_new_orders", "value": None,
            "formula": "【原文缺失】", "hit": None, "note": "正文未给出，无法判定"})

    # ---- 5 类触发动作 ----
    actions = {}  # id -> bool/None；None 表示因缺失无法判定
    actions["A"] = new_orders is not None and mom is not None and new_orders < 50 and mom < -1.0
    actions["B"] = (new_orders is not None and mom is not None and ma12_yoy is not None
                    and new_orders > 50 and mom > 1.0 and ma12_yoy > 0)
    actions["C"] = new_orders is not None and production is not None and new_orders - production > 2
    actions["D"] = service_no is not None and service_no < 48
    if basic_rm_no is None:
        actions["E"] = None  # 原文缺失，无法判定
    else:
        actions["E"] = basic_rm_no < 49

    hit_actions = [k for k, v in actions.items() if v is True]
    unknown_actions = [k for k, v in actions.items() if v is None]

    # ---- 仓位分级 ----
    score = sum(2 if k in CORE_ACTIONS else 1 for k in hit_actions)
    if score >= 3:
        position = "红"
    elif score >= 1:
        position = "黄"
    else:
        position = "绿"
    position_label = {"绿": "正常", "黄": "警戒", "红": "高风险"}[position]
    alert_email = position in ("黄", "红")

    result = {
        "data_month": latest_month,
        "prev_month": prev_month,
        "pmi": val("pmi"),
        "business_activity": val("business_activity"),
        "n_new_orders": val("n_new_orders"),
        "checks": checks,
        "actions": {k: {"hit": v, "desc": ACTIONS_DESC[k]} for k, v in actions.items()},
        "hit_actions": {k: ACTIONS_DESC[k] for k in hit_actions},
        "position": position,
        "position_label": position_label,
        "position_score": score,
        "alert_email": alert_email,
        "missing": missing,
        "ma12_yoy": ma12_yoy,
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    return result


def fmt(v, nd=1):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def build_report(r):
    month = r["data_month"]
    L = []
    L.append(f"PMI 月度分析报告（{month} 数据）")
    L.append(f"数据月份：{month} | 生成时间：{r['generated_at']}")
    L.append(f"制造业PMI：{fmt(r['pmi'])} | 非制造业商务活动：{fmt(r['business_activity'])} | 非制造业新订单：{fmt(r['n_new_orders'])}")
    L.append("")
    L.append("一、阈值判定清单（7 项）")
    for c in r["checks"]:
        if c["hit"] is None:
            mark = "▲"
        elif c["hit"]:
            mark = "✓"
        else:
            mark = "✗"
        note = f"（{c['note']}）" if c.get("note") else ""
        L.append(f"  {mark} {c['name']}：{c['formula']}{note}")
    L.append("")
    L.append("二、触发动作")
    if r["hit_actions"]:
        for k, desc in r["hit_actions"].items():
            L.append(f"  ▶ {k}. {desc}")
    else:
        L.append("  无触发动作")
    for k in r["actions"]:
        if r["actions"][k]["hit"] is None:
            L.append(f"  ▲ {k}. {r['actions'][k]['desc']}【原文缺失，无法判定】")
    L.append("")
    L.append("三、仓位分级（绿/黄/红）")
    L.append(f"  当前仓位：{r['position']}（{r['position_label']}），加权分 {r['position_score']}"
             f"（核心动作 A/B/E 权重2，次要 C/D 权重1）")
    L.append("")
    L.append("四、数据缺口")
    gaps = list(r["missing"])
    for c in r["checks"]:
        if "原文缺失" in c.get("formula", ""):
            gaps.append(f"{c['name']}")
    if gaps:
        L.append("  " + "；".join(gaps) + "【原文缺失】")
    else:
        L.append("  无")
    L.append("")
    L.append(f"结论：{'触发邮件提醒（黄色及以上仓位）' if r['alert_email'] else '未触发邮件（绿色仓位）'}")
    return "\n".join(L)


def main():
    with open(PMI_JSON, encoding="utf-8") as f:
        data = json.load(f)
    r = evaluate(data["series"], data["latest"], data.get("missing", []))
    with open(ANALYSIS_JSON, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=2)
    report = build_report(r)
    os.makedirs(OUT_DIR, exist_ok=True)
    month_compact = r["data_month"].replace("-", "")
    out_path = os.path.join(OUT_DIR, f"pmi_report_{month_compact}.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report)

    print("== PMI 判定摘要 ==")
    print("月份:", r["data_month"], "| 新订单:", fmt(r["checks"][0]["value"]),
          "| 环比:", fmt(r["checks"][1]["value"]), "| MA12同比:", fmt(r["ma12_yoy"]))
    print("命中阈值:", [c["id"] for c in r["checks"] if c["hit"]],
          "| 触发动作:", list(r["hit_actions"].keys()) or "无")
    print("仓位:", r["position"], f"(分{r['position_score']})", "| 触发邮件:", r["alert_email"])
    print("报告输出:", out_path)


if __name__ == "__main__":
    main()