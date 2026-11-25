#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PMI 分项"剔除法"分析：剔除最强项看真实底色

输入：data/pmi.json（fetch_pmi.py 产出，含本月/上月分项原始值，均来自统计局发布页）
规则（按用户给定的判定顺序）：
  1. 计算候选分项环比：新订单、新出口订单、生产、服务业新订单、建筑业新订单（价格分项参与劈叉判定）
  2. 分支：
     a) 新出口订单涨幅最大且 Δ>+1.0pt 且贡献新订单涨幅 >50%  -> 内需剔出口
     b) 服务业新订单涨幅最大                              -> 制造剔服务
     c) 购进价格 Δ>+2pt 且 新订单 Δ<+1pt                  -> 量价劈叉（价涨量滞）
  3. 输出固定格式一句定性结论 + 阈值提醒
硬规则：原始分项缺失的月份输出「原文缺失，无法剔除」，禁止用上月值硬凑。
输出：data/pmi_analysis.json + 打印渲染文本（report_builder 直接读取）。

说明：PMI 分项本身即环比指数（官方口径），分项Δ = 本月值 − 上月值（pct）。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import DATA_DIR

# 制造 PMI 合成指数官方权重（新订单30/生产25/从业人员20/供应商配送15/原材料库存10）
PMI_WEIGHTS = {"new_orders": 0.30, "production": 0.25}


def load_pmi():
    path = os.path.join(DATA_DIR, "pmi.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_subitems(d):
    """计算各候选分项环比（本月-上月）。返回 {名称: {cur, prev, mom}}。"""
    m = d.get("manu", {})
    mp = m.get("prev", {}) or {}
    s = d.get("services", {})
    c = d.get("construction", {})
    items = {}
    def _add(name, cur, prev):
        if cur is None or prev is None:
            items[name] = {"cur": cur, "prev": prev, "mom": None, "missing": True}
        else:
            items[name] = {"cur": cur, "prev": prev, "mom": round(cur - prev, 1), "missing": False}

    _add("制造业新订单", m.get("new_orders"), mp.get("new_orders"))
    _add("新出口订单", m.get("new_export"), mp.get("new_export"))
    _add("生产", m.get("production"), mp.get("production"))
    _add("服务业新订单", s.get("new_orders"), s.get("new_orders_prev"))
    _add("建筑业新订单", c.get("new_orders"), c.get("new_orders_prev"))
    _add("主要原材料购进价格", m.get("input_price"), mp.get("input_price"))
    return items


def analyze(d):
    if not d or not d.get("manu") or "new_orders" not in d["manu"]:
        return {"missing": True,
                "text": "【原文缺失，无法剔除】本期 PMI 分项原始数据未取到（pmi.json 无本月分项），未用上月值硬凑。"}

    month = d.get("month", "")
    items = build_subitems(d)
    missing_any = any(v.get("missing") for k, v in items.items())
    if missing_any:
        missing_names = [k for k, v in items.items() if v.get("missing")]
        return {"missing": True, "month": month, "items": items,
                "text": f"【原文缺失，无法剔除】{month} PMI 分项缺失：{('、'.join(missing_names))}，未用上月值硬凑。"}

    no = items["制造业新订单"]["mom"]   # 新订单Δ
    ne = items["新出口订单"]["mom"]     # 新出口Δ
    prod = items["生产"]["mom"]         # 生产Δ
    svc = items["服务业新订单"]["mom"]  # 服务业新订单Δ
    bld = items["建筑业新订单"]["mom"]  # 建筑业新订单Δ
    price = items["主要原材料购进价格"]["mom"]  # 购进价格Δ

    # 候选（不含价格）：找环比涨幅最大分项
    cand = {"新订单": no, "新出口订单": ne, "生产": prod,
            "服务业新订单": svc, "建筑业新订单": bld}
    max_name = max(cand, key=lambda k: cand[k])
    max_mom = cand[max_name]

    # 贡献度：分项Δ ÷ 新订单总Δ（>50% 标"主要由XX拉动"；新订单Δ<=0 时需求未扩张，该口径不适用）
    contrib = None
    if no > 0 and max_mom > 0:
        contrib = max_mom / no * 100.0

    branch = ""
    alerts = []
    real_baseline = None

    # 分支 a: 新出口拉动
    if ne > 1.0 and max_name == "新出口订单" and contrib is not None and contrib > 50.0:
        dom = items["制造业新订单"]["cur"] - (items["新出口订单"]["cur"] - items["新出口订单"]["prev"])
        real_baseline = round(dom, 1)
        branch = "new_export"
        # 阈值提醒
        if real_baseline < 49.5:
            alerts.append("⚠️ 内需未真复苏，T 不追空")
        elif real_baseline > 50.5:
            alerts.append("内需独立走强，可考虑 T 反弹空")
        main = "新出口订单"
        base_label = "内需剔出口后"
    # 分支 b: 服务业新订单拉动
    elif max_name == "服务业新订单":
        spill = max(0.0, svc - 0.0)  # 服务业Δ 的溢出部分近似 = 服务业新订单Δ 本身
        dom = items["制造业新订单"]["cur"] - spill
        real_baseline = round(dom, 1)
        branch = "services"
        if real_baseline < 49.5:
            alerts.append("⚠️ 内需未真复苏，T 不追空")
        elif real_baseline > 50.5:
            alerts.append("内需独立走强，可考虑 T 反弹空")
        main = "服务业新订单"
        base_label = "制造剔服务后"
    # 分支 c: 量价劈叉
    elif price > 2.0 and no < 1.0:
        branch = "price_divergence"
        real_baseline = items["制造业新订单"]["cur"]
        alerts.append("⚠️ 滞胀信号，债震荡偏多不做空")
        # 用官方 PMI 权重近似拆分主要拉动（新订单30/生产25）
        w_orders = PMI_WEIGHTS["new_orders"] * no
        w_prod = PMI_WEIGHTS["production"] * prod
        main = "生产" if w_prod > abs(w_orders) else "新订单/生产"
        base_label = "新订单（真实需求底色）"
    else:
        # 无明确分支：报告新订单本身为底色
        branch = "none"
        real_baseline = items["制造业新订单"]["cur"]
        main = max_name
        base_label = "新订单"

    # 扩张/收缩/持平
    if real_baseline is None:
        status = "持平"
    elif real_baseline > 50:
        status = "扩张"
    elif real_baseline < 50:
        status = "收缩"
    else:
        status = "持平"

    # 对债含义
    if branch == "price_divergence":
        bond = "顺风"
    elif real_baseline is not None and real_baseline < 49.5:
        bond = "顺风"
    elif real_baseline is not None and real_baseline > 50.5:
        bond = "逆风"
    else:
        bond = "中性"

    if max_mom is not None and max_mom <= 0:
        verdict = (
            f"本月 PMI 分项全面回落，其中【{main}】降幅最小（Δ{max_mom:+.1f}pt），"
            f"剔除价格因素后{base_label}真实底色约【{real_baseline}】，"
            f"处于【{status}】，对债的含义是【{bond}】。"
        )
    else:
        verdict = (
            f"本月 PMI 回升主要由【{main}】拉动，剔除后{base_label}真实底色约【{real_baseline}】，"
            f"处于【{status}】，对债的含义是【{bond}】。"
        )
    if branch == "price_divergence":
        verdict = (
            f"本月 PMI 回升主要由【{main}】拉动，剔除后内需真实底色约【{real_baseline}】，"
            f"处于【{status}】，对债的含义是【{bond}】。量价劈叉：涨价非需求驱动"
            f"（价涨量滞：购进价格Δ{price:+.1f}pt vs 新订单Δ{no:+.1f}pt；"
            f"分项环比：新订单{no:+.1f}、新出口{ne:+.1f}、生产{prod:+.1f}、"
            f"服务业新订单{svc:+.1f}、建筑业新订单{bld:+.1f}）。"
        )

    return {
        "missing": False,
        "month": month,
        "items": items,
        "max_mom_item": max_name,
        "max_mom": max_mom,
        "contrib_pct": round(contrib, 1) if contrib is not None else None,
        "branch": branch,
        "real_baseline": real_baseline,
        "base_label": base_label,
        "status": status,
        "bond": bond,
        "alerts": alerts,
        "verdict": verdict,
        "text": verdict + ("\n" + "；".join(alerts) if alerts else ""),
    }


def main():
    d = load_pmi()
    res = analyze(d)
    path = os.path.join(DATA_DIR, "pmi_analysis.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print(res.get("text", "无"))
    print("pmi_analysis.json 已保存")


if __name__ == "__main__":
    main()