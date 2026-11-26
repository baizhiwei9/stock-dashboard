#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
国债长短结构监控分析脚本（从统一数据层读取，不做联网）
============================================================
数据来源：treasury_data_output.json（由 treasury_data_fetch.py 统一抓取）

指标（6 项）：
  1 美联储短长比         = H.4.1 Table2（<=1年）/(1-5年 + 5-10年 + 10年以上)；周更
  2 财政部存量短长比     = MSPD Bills / (Notes + Bonds + TIPS)；月更
  3 滚动偿债比           = 未来12个月到期面值 / 总可交易面值；月更
  4 发债结构比           = 当月 auction gross Bills / gross 全部；周/月更
  5 久期 proxy           = 剩余期限面值加权平均（月）；月更
  6 政策信号             = Quarterly Refunding 措辞（需人工确认）

对比维度（v3 新增）：
  全部数量指标同时给出环比与同比：
    - H.4.1       周环比（上周发布）、季环比（13 周前）、年同比（52 周前）
    - MSPD        月环比（上月）、年同比（去年同月）
    - 滚动/久期     月环比（上月数据日重算）、年同比（去年同月数据日重算）
    - 发债结构比    月环比（上月整月）、年同比（去年同月整月）

口径约定：
  - FRN 不并入短端（第一版，纯 Bills 口径）
  - 短端 = 剩余期限 <= 1 年
  - 滚动偿债比 / 久期 proxy：分子与权重面值用 Auction 发行量（offering_amt）口径，
    该口径未含 FRN 与部分 CMB/早期证券，绝对值略低估（约 6-7%），看趋势为主；
    滚动偿债比分母用 MSPD 总可交易面值（更权威）。

阈值（可调）：
  - 美联储短债占比单季（13 周）上行 > 5.0 个百分点告警
  - T-bill 占可交易 > 20% 关注；> 23%（23-25% 区间）强警
  - 滚动偿债比 > 35% 告警
  - 平均剩余期限 < 65 个月告警

用法：python3 treasury_maturity_monitor.py [--json 输出文件]
"""
import json
import sys
from datetime import date

THRESHOLDS = {
    "fed_short_share_qoq_pct": 5.0,     # 美联储短债占比单季上行（百分点）
    "tbill_share_watch": 20.0,          # T-bill 占可交易，关注线
    "tbill_share_alert": 23.0,          # T-bill 占可交易，强警线（23-25%）
    "rollover_12m_pct": 35.0,           # 未来12个月到期占比告警线
    "avg_maturity_watch_months": 65.0,  # 平均剩余期限告警线（月）
}


# ---------------- 灯位判定 ----------------
def judge(ind):
    t = THRESHOLDS
    checks = []

    fed_qoq = ind["fed"]["compare"]["qoq"]["change_pct"]
    tbill = ind["treasury"]["tbill_share_pct"]
    roll = ind["rollover_12m_pct"]
    dur = ind["avg_maturity_months"]

    checks.append(("美联储短债占比单季变化", fed_qoq,
                   "告警(>%s)" % t["fed_short_share_qoq_pct"],
                   fed_qoq > t["fed_short_share_qoq_pct"]))
    checks.append(("T-bill占可交易", tbill,
                   "关注(>%s)/强警(>%s)" % (t["tbill_share_watch"], t["tbill_share_alert"]),
                   tbill > t["tbill_share_alert"]))
    checks.append(("未来12个月到期占比", roll,
                   "告警(>%s)" % t["rollover_12m_pct"],
                   roll > t["rollover_12m_pct"]))
    checks.append(("平均剩余期限(月)", dur,
                   "告警(<%s)" % t["avg_maturity_watch_months"],
                   dur < t["avg_maturity_watch_months"]))

    red = checks[0][3] or checks[1][3] or checks[2][3] or checks[3][3]
    watch = tbill > t["tbill_share_watch"] or roll > t["rollover_12m_pct"] - 5.0
    if red:
        level = "红灯（强预警）"
    elif watch:
        level = "黄灯（关注）"
    else:
        level = "绿灯（常态）"
    return checks, level


# ---------------- 主流程：从统一数据层组装 ----------------
def _tbl_summary(x):
    if x is None:
        return None
    return {"record_date": x["record_date"], "bills_mil": x["bills_mil"],
            "notes_mil": x["notes_mil"], "bonds_mil": x["bonds_mil"],
            "tips_mil": x["tips_mil"], "frn_mil": x["frn_mil"],
            "marketable_mil": x["marketable_mil"], "tbill_share_pct": x["tbill_share_pct"],
            "ratio_short_long": x["ratio_short_long"]}


def _tbl_rollover(x):
    if x is None:
        return None
    return {"record_date": x["record_date"], "rollover_12m_pct": x["rollover_12m_pct"],
            "avg_maturity_months": x["avg_maturity_months"], "n_cusips": x["n_cusips"],
            "maturing_in_12m_cusips": x["maturing_in_12m_cusips"],
            "offering_12m_mil": x["offering_12m_mil"], "marketable_mil": x["marketable_mil"]}


def _tbl_auction(x):
    return {"period": x["period"], "n_auctions": x["n_auctions"],
            "bills_share_pct": x["bills_share_pct"], "bills_mil": x["bills_mil"],
            "total_mil": x["total_mil"]}


def collect_indicators(data):
    """data 来自统一数据层 treasury_data_output.json，只做判断组装，不再联网。"""
    h, m, rd, au = data["h41"], data["mspd"], data["rollover_duration"], data["auction_structure"]

    # ------- H.4.1：当期 + 周环比 / 季环比 / 年同比 -------
    cur, wk, qoq, yoy = h["current"], h["wow"], h["qoq"], h["yoy"]
    fed = {
        "release_date": cur["release_date"],
        "short_share_pct": cur["short_share_pct"],
        "ratio_short_long": cur["ratio_short_long"],
        "compare": {
            "wow": {"date": wk["release_date"], "short_share_pct": wk["short_share_pct"],
                    "change_pct": cur["short_share_pct"] - wk["short_share_pct"]},
            "qoq": {"date": qoq["release_date"], "short_share_pct": qoq["short_share_pct"],
                    "change_pct": cur["short_share_pct"] - qoq["short_share_pct"]},
            "yoy": {"date": yoy["release_date"], "short_share_pct": yoy["short_share_pct"],
                    "change_pct": cur["short_share_pct"] - yoy["short_share_pct"]},
        },
    }

    # ------- MSPD：当期 + 上月 / 去年同月 -------
    treasury = _tbl_summary(m["current"])
    treasury["compare"] = {"mom": _tbl_summary(m["prev"]), "yoy": _tbl_summary(m["yoy"])}

    # ------- 滚动偿债比 / 久期：当期 + 上月 / 去年同月 -------
    rollover = _tbl_rollover(rd["current"])
    rollover["compare"] = {"mom": _tbl_rollover(rd["prev"]), "yoy": _tbl_rollover(rd["yoy"])}

    # ------- 发债结构：当月 / 上月整月 / 去年同月整月 -------
    auction_month = _tbl_auction(au["current"])
    auction_month["compare"] = {"mom": _tbl_auction(au["prev"]), "yoy": _tbl_auction(au["yoy"])}

    checks, level = judge({
        "fed": fed,
        "treasury": treasury,
        "rollover_12m_pct": rd["current"]["rollover_12m_pct"],
        "avg_maturity_months": rd["current"]["avg_maturity_months"],
    })

    return {
        "run_date": data["run_date"],
        "level": level,
        "fed": fed,
        "treasury": treasury,
        "rollover": rollover,
        "auction_month": auction_month,
        "policy_signal": {
            "status": "待人工确认",
            "note": "Quarterly Refunding（2/5/8/11 月初发布）措辞需人工核对关键词：维持coupon / 扩大bill / 暂停长债增量",
        },
        "checks": [{"name": c[0], "value": round(c[1], 2), "rule": c[2], "hit": c[3]} for c in checks],
    }


# ---------------- 展示 ----------------
def _chg_line(label, c, unit="个百分点"):
    if c is None:
        return "    %s: 无数据" % label
    return "    %s(%s): %.2f%% | 变化 %+.2f %s" % (
        label, c["date"], c["short_share_pct"], c["change_pct"], unit)


def render(ind):
    f, t = ind["fed"], ind["treasury"]
    rv, au = ind["rollover"], ind["auction_month"]
    lines = []
    lines.append("=" * 60)
    lines.append("美债监控 - 长短结构   运行日: %s" % ind["run_date"])
    lines.append("综合灯位: %s" % ind["level"])
    lines.append("=" * 60)

    lines.append("")
    lines.append("[1] 美联储短长比 (H.4.1, 周更)")
    lines.append("    当期(%s): 短债占比 %.2f%% | 短长比(短/长) %.3f" % (
        f["release_date"], f["short_share_pct"], f["ratio_short_long"]))
    lines.append(_chg_line("周环比", f["compare"]["wow"]))
    lines.append(_chg_line("季环比", f["compare"]["qoq"]))
    lines.append(_chg_line("年同比", f["compare"]["yoy"]))

    lines.append("")
    lines.append("[2] 财政部存量短长比 (MSPD %s, 月更, 单位十亿美元)" % t["record_date"])
    lines.append("    Bills %.0f | Notes %.0f | Bonds %.0f | TIPS %.0f | FRN %.0f | 可交易合计 %.0f"
                 % (t["bills_mil"] / 1e3, t["notes_mil"] / 1e3, t["bonds_mil"] / 1e3,
                    t["tips_mil"] / 1e3, t["frn_mil"] / 1e3, t["marketable_mil"] / 1e3))
    lines.append("    T-bill 占可交易 %.2f%% | 短长比(Bills/(Notes+Bonds+TIPS)) %.2f"
                 % (t["tbill_share_pct"], t["ratio_short_long"]))

    def _tbl_chg(label, c):
        if c is None or c["tbill_share_pct"] is None:
            return "    %s: 无数据" % label
        return "    %s(%s): T-bill 占比 %.2f%% | 变化 %+.2f 个百分点 | 短长比 %.2f" % (
            label, c["record_date"], c["tbill_share_pct"],
            t["tbill_share_pct"] - c["tbill_share_pct"], c["ratio_short_long"])

    lines.append(_tbl_chg("月环比", t["compare"]["mom"]))
    lines.append(_tbl_chg("年同比", t["compare"]["yoy"]))

    lines.append("")
    lines.append("[3] 滚动偿债比 (数据日 %s, 月更)" % rv["record_date"])
    lines.append("    未来12个月到期占比 %.2f%% （%d 个 CUSIP 到期 / 总计 %d 个）"
                 % (rv["rollover_12m_pct"], rv["maturing_in_12m_cusips"], rv["n_cusips"]))
    lines.append("    分子(Auction 口径) $%s BN / 分母(MSPD 总可交易) $%s BN"
                 % (_fmt(rv["offering_12m_mil"] / 1e3, 0), _fmt(rv["marketable_mil"] / 1e3, 0)))

    def _rv_chg(label, c):
        if c is None or c["rollover_12m_pct"] is None:
            return "    %s: 无数据" % label
        return "    %s(%s): %.2f%% | 变化 %+.2f 个百分点" % (
            label, c["record_date"], c["rollover_12m_pct"],
            rv["rollover_12m_pct"] - c["rollover_12m_pct"])

    lines.append(_rv_chg("月环比", rv["compare"]["mom"]))
    lines.append(_rv_chg("年同比", rv["compare"]["yoy"]))
    lines.append("    口径说明: 分子未含 FRN 与部分 CMB/早期证券，绝对值略低估，看趋势为主")

    lines.append("")
    lines.append("[4] 发债结构比 (Auction, %s 已拍 %d 场)" % (au["period"], au["n_auctions"]))
    if au["bills_share_pct"] is not None:
        lines.append("    gross Bills 占比 %.2f%% (Bills $%s BN / 全部 $%s BN)"
                     % (au["bills_share_pct"], _fmt(au["bills_mil"] / 1e3, 0),
                        _fmt(au["total_mil"] / 1e3, 0)))
    else:
        lines.append("    本月暂无已完成拍卖")

    def _au_chg(label, c):
        if c is None or c["bills_share_pct"] is None:
            return "    %s: 无数据或当月无拍卖" % label
        return "    %s(%s): %.2f%% (Bills $%s BN / 全部 $%s BN, %d 场)" % (
            label, c["period"], c["bills_share_pct"],
            _fmt(c["bills_mil"] / 1e3, 0), _fmt(c["total_mil"] / 1e3, 0), c["n_auctions"])

    lines.append(_au_chg("月环比", au["compare"]["mom"]))
    lines.append(_au_chg("年同比", au["compare"]["yoy"]))
    lines.append("    口径说明: 月内日历效应显著（coupon 拍卖集中在下半月），当月值与整月不可直接比，看季内累计")

    lines.append("")
    lines.append("[5] 久期 proxy (数据日 %s, 剩余期限面值加权, 月更)" % rv["record_date"])
    lines.append("    平均剩余期限 %.1f 个月" % rv["avg_maturity_months"])

    def _dur_chg(label, c):
        if c is None or c["avg_maturity_months"] is None:
            return "    %s: 无数据" % label
        return "    %s(%s): %.1f 个月 | 变化 %+.1f 个月" % (
            label, c["record_date"], c["avg_maturity_months"],
            rv["avg_maturity_months"] - c["avg_maturity_months"])

    lines.append(_dur_chg("月环比", rv["compare"]["mom"]))
    lines.append(_dur_chg("年同比", rv["compare"]["yoy"]))
    lines.append("    口径说明: Auction 发行量加权（未含 FRN/部分 CMB），绝对值略低估，看趋势为主")

    lines.append("")
    lines.append("[6] 政策信号 (Quarterly Refunding, 季度/事件)")
    lines.append("    %s: %s" % (ind["policy_signal"]["status"], ind["policy_signal"]["note"]))

    lines.append("")
    lines.append("阈值判定:")
    for c in ind["checks"]:
        mark = "触发" if c["hit"] else "正常"
        lines.append("    [%s] %s = %s （规则: %s）" % (mark, c["name"], c["value"], c["rule"]))
    lines.append("=" * 60)
    return "\n".join(lines)


def _fmt(x, nd=0):
    if x is None:
        return "-"
    return format(x, ",.{}f".format(nd))


def main():
    out_json = None
    if len(sys.argv) > 1 and sys.argv[1] == "--json":
        out_json = sys.argv[2] if len(sys.argv) > 2 else "treasury_maturity_output.json"

    with open("treasury_data_output.json", encoding="utf-8") as fp:
        data = json.load(fp)
    ind = collect_indicators(data)
    print(render(ind))

    if out_json:
        with open(out_json, "w", encoding="utf-8") as fp:
            json.dump(ind, fp, ensure_ascii=False, indent=2)
        print("\nJSON 已落盘: %s" % out_json)


if __name__ == "__main__":
    main()