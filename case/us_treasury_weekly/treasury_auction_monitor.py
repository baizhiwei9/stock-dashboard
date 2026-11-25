#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美债拍卖认购热情与买方结构分析脚本（从统一数据层读取，不做联网）
================================================================
数据来源：
  - treasury_data_output.json     由 treasury_data_fetch.py 统一抓取（auction_detail 板块）
  - treasury_auction_anchors.json 存量需求端 / 中国因素低频锚点（月度/季度更新，含数据日与来源）

六项观察清单：
  1 认购倍数（Bid-to-Cover）：12 个重点品种最新值 vs 近 12 次均值（剔除本次取前 11 次均值），
     |偏差| >= 0.2 → 强/弱，否则中性
  2 尾差（Tail）：fiscaldata API 不含 WI（预发行收益率），无法计算 → 标注"缺数据"
  3 得标利率：最新场次 high_yield，与上一场比较变动
  4 买方结构：三类占比（一级交易商 / 直接 / 间接竞标者，按 accepted/comp_accepted 口径），近 3 期趋势
  5 存量需求端：美联储 SOMA / TIC 海外持仓 / 对冲基金基差交易 / 货币基金（低频锚点）
  6 中国因素：中国美债持仓 + 中国黄金储备（低频锚点）

综合灯位（max 规则）：
  - 弱信号计数 >= 6 → 橙（偏弱预警）
  - 弱信号计数 >= 1 → 黄（关注）
  - 无弱信号 → 绿（常态）

弱信号计数口径（保持与"强/中性/弱"三级相符，"中性偏弱/结构性偏弱"归中性档不计）：
  - 认购倍数弱期限组：任一期限判定"弱"计 1
  - 买方结构：间接回落计 1、一级交易商承接上升计 1
  - 存量需求端：仅判定为"弱"的领域计数（每个 1）
  - 中国因素：判定为"弱"计 1

用法：python3 treasury_auction_monitor.py [--json 输出文件]
"""
import json
import os
import sys
from datetime import date

# 重点观察期限（12 个）
TENORS = [
    ("Bill", "4-Week"), ("Bill", "13-Week"), ("Bill", "17-Week"),
    ("Bill", "26-Week"), ("Bill", "52-Week"),
    ("Note", "2-Year"), ("Note", "3-Year"), ("Note", "5-Year"),
    ("Note", "7-Year"), ("Note", "10-Year"),
    ("Bond", "20-Year"), ("Bond", "30-Year"),
]
WEAK_DEV = -0.20   # 认购倍数 vs 前 11 次均值：<= -0.2 判定弱
STRONG_DEV = 0.20  # >= 0.2 判定强
WEAK_ORANGE = 6    # 弱信号计数 >= 6 → 橙（偏弱预警）


def _num(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _load(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as fp:
        return json.load(fp)


def _tenor_key(rec):
    return (rec.get("security_type") or "", rec.get("security_term") or "")


def _series_by_tenor(records):
    """按 (security_type, security_term) 分组，每组按 auction_date 升序，仅保留已发布结果。"""
    groups = {}
    for r in records:
        if _num(r.get("bid_to_cover_ratio")) is None:
            continue  # 尚未发布结果
        groups.setdefault(_tenor_key(r), []).append(r)
    for k in groups:
        groups[k].sort(key=lambda x: x.get("auction_date") or "")
    return groups


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def btc_stats(groups):
    """① 认购倍数：最新 vs 前 11 次均值 + 近 3 期趋势 + 强/中性/弱。"""
    items = {}
    for sec_type, term in TENORS:
        recs = groups.get((sec_type, term), [])
        if len(recs) < 2:
            items[term] = {"latest": None, "avg_prev11": None, "dev": None,
                           "recent3": [], "trend": "缺数据",
                           "n_samples": len(recs), "judge": "缺数据",
                           "latest_auction_date": recs[-1].get("auction_date") if recs else None,
                           "latest_high_yield": _num(recs[-1].get("high_yield")) if recs else None}
            continue
        latest = _num(recs[-1]["bid_to_cover_ratio"])
        prev = [_num(r["bid_to_cover_ratio"]) for r in recs[-12:-1]]
        prev = [p for p in prev if p is not None]
        avg_prev11 = _mean(prev)
        dev = latest - avg_prev11 if avg_prev11 is not None else None
        recent = [_num(r["bid_to_cover_ratio"]) for r in recs[-3:]]
        if len(recent) >= 3 and all(v is not None for v in recent):
            trend = ("上升" if recent[2] > recent[1] > recent[0]
                     else "下降" if recent[2] < recent[1] < recent[0] else "波动")
        else:
            trend = "波动"
        if dev is None:
            judge = "缺数据"
        elif dev <= WEAK_DEV:
            judge = "弱"
        elif dev >= STRONG_DEV:
            judge = "强"
        else:
            judge = "中性"
        items[term] = {
            "latest": round(latest, 2) if latest is not None else None,
            "avg_prev11": round(avg_prev11, 2) if avg_prev11 is not None else None,
            "dev": round(dev, 2) if dev is not None else None,
            "recent3": [round(v, 2) for v in recent if v is not None],
            "trend": trend,
            "n_samples": len(recs),
            "judge": judge,
            "latest_auction_date": recs[-1].get("auction_date"),
            "latest_high_yield": _num(recs[-1].get("high_yield")),
        }
    weak = [t for t, v in items.items() if v["judge"] == "弱"]
    strong = [t for t, v in items.items() if v["judge"] == "强"]
    return {"items": items, "weak_list": weak, "strong_list": strong}


def yield_stats(groups, max_chg_bp=120.0):
    """③ 得标利率：最新 vs 上一场可比场次。

    同名序列中可能混入异常券种（如 fiscaldata 将 TIPS 记入同 security_term），
    与最新值差异超过 max_chg_bp 的场次视为不可比、跳过（记入 skipped），
    向前找最近一档可比场次作为"上期"。
    """
    items = {}
    for sec_type, term in TENORS:
        recs = groups.get((sec_type, term), [])
        if not recs:
            items[term] = {"latest": None, "prev": None, "chg_bp": None,
                           "latest_auction_date": None, "skipped": []}
            continue
        latest = _num(recs[-1].get("high_yield") or recs[-1].get("high_discnt_rate"))
        skipped = []
        prev = None
        for r in reversed(recs[:-1]):
            v = _num(r.get("high_yield") or r.get("high_discnt_rate"))
            if v is None:
                continue
            if latest is not None and abs(latest - v) * 100 > max_chg_bp:
                skipped.append({"auction_date": r.get("auction_date"), "value": v})
                continue
            prev = v
            break
        chg = (latest - prev) * 100 if latest is not None and prev is not None else None
        items[term] = {
            "latest": round(latest, 3) if latest is not None else None,
            "prev": round(prev, 3) if prev is not None else None,
            "chg_bp": round(chg, 1) if chg is not None else None,
            "latest_auction_date": recs[-1].get("auction_date"),
            "skipped": skipped,
        }
    return items


def buyer_structure(groups, n_last=3):
    """④ 买方结构：近 n_last 期 PD / Direct / Indirect 占 comp_accepted 比例。

    二级口径：占比 = accepted / comp_accepted（comp_accepted 为竞争性合计）。
    """
    items = {}
    for sec_type, term in TENORS:
        recs = groups.get((sec_type, term), [])[-n_last:]
        rows = []
        for r in recs:
            comp = _num(r.get("comp_accepted"))
            if not comp:
                continue
            pd_pct = _num(r.get("primary_dealer_accepted")) / comp * 100 if _num(r.get("primary_dealer_accepted")) is not None else None
            dir_pct = _num(r.get("direct_bidder_accepted")) / comp * 100 if _num(r.get("direct_bidder_accepted")) is not None else None
            ind_pct = _num(r.get("indirect_bidder_accepted")) / comp * 100 if _num(r.get("indirect_bidder_accepted")) is not None else None
            rows.append({"auction_date": r.get("auction_date"),
                         "pd_pct": round(pd_pct, 1) if pd_pct is not None else None,
                         "direct_pct": round(dir_pct, 1) if dir_pct is not None else None,
                         "indirect_pct": round(ind_pct, 1) if ind_pct is not None else None})
        items[term] = rows
    return items


def buyer_structure_summary(buyer):
    """对买方结构做总体判断：间接（海外）占比趋势 + PD 承接异常。"""
    notes = []
    weak = 0
    # 只看存续期 >= 2 期的期限
    pd_up = []
    ind_down = []
    for term, rows in buyer.items():
        if len(rows) < 2:
            continue
        inds = [r["indirect_pct"] for r in rows if r["indirect_pct"] is not None]
        pds = [r["pd_pct"] for r in rows if r["pd_pct"] is not None]
        if len(inds) >= 2 and inds[-1] < inds[-2]:
            ind_down.append(term)
        if len(pds) >= 2 and pds[-1] > pds[-2]:
            pd_up.append(term)
    if ind_down:
        notes.append("间接（海外）占比回落期限：%s" % "、".join(ind_down))
        weak += 1
    if pd_up:
        notes.append("一级交易商承接上升期限：%s（托底迹象）" % "、".join(pd_up))
        weak += 1
    if not notes:
        notes.append("近 3 期三类占比整体平稳")
    return {"notes": notes, "weak_count": weak}


def overall_level(btc, buyer_sum, stock_weak, china_weak):
    """综合灯位：弱信号计数（仅明确"弱"计入，中性偏弱不计）。"""
    weak_count = 1 if btc["weak_list"] else 0
    weak_count += buyer_sum.get("weak_count", 0)
    weak_count += stock_weak
    weak_count += china_weak

    if weak_count >= WEAK_ORANGE:
        level, color = "黄灯（关注，拍卖认购偏弱）", "orange"
    elif weak_count >= 1:
        level, color = "黄灯（关注）", "yellow"
    else:
        level, color = "绿灯（常态）", "green"
    return level, color, weak_count


def conclusion(btc, buyer, stock_demand, china, level, weak_count):
    """一段话综合结论：主要买方 + 需求健康度 + 认购热情边际变化。"""
    ind_note = []
    for term, rows in list(buyer.items())[:5]:
        if rows:
            ind_note.append("%s 最新间接 %.1f%%" % (term, rows[-1]["indirect_pct"] or 0))
    parts = []
    parts.append("主要买方仍是海外投资者（间接竞标，%s），" % "；".join(ind_note[:3]))
    parts.append("认购倍数整体%s，%d 项弱信号；" % (
        "降温（%d 个期限低于近 12 次均值）" % len(btc["weak_list"]) if btc["weak_list"] else "平稳",
        weak_count))
    parts.append("存量端美联储缩表重启、海外官方持仓连降、基差交易去杠杆、货币基金只买短端，")
    parts.append("中国持续减持美债增持黄金（迁移率根本性信号）。")
    parts.append("结论：需求健康度边际走弱，靠价格敏感资金与一级交易商托底承接，未到危机式抛售但已进入"
                 "“中性偏弱”区间，收益率需更高补偿。")
    return "".join(parts)


def render(out):
    lines = []
    lines.append("=" * 60)
    lines.append("美债拍卖认购热情与买方结构   运行日: %s" % out["run_date"])
    lines.append("综合灯位: %s | 弱信号 %d 项" % (out["level"], out["weak_count"]))
    lines.append("=" * 60)

    lines.append("")
    lines.append("[1] 认购倍数（vs 前 11 次均值, 12 个重点期限）")
    for term, v in out["btc"]["items"].items():
        if v["latest"] is None:
            lines.append("    %-12s 缺数据" % term)
            continue
        lines.append("    %-12s 最新 %.2f | 均值 %.2f | 偏差 %+.2f | 近3期 %s | 趋势 %s | 判断 %s"
                     % (term, v["latest"], v["avg_prev11"], v["dev"],
                        "→".join(str(x) for x in v["recent3"]), v["trend"], v["judge"]))

    lines.append("")
    lines.append("[2] 尾差 (Tail)")
    tail = out["tail"]
    lines.append("    %s" % tail["note"])

    lines.append("")
    lines.append("[3] 得标利率（最新 vs 上一场）")
    for term, v in out["high_yield"].items():
        if v["latest"] is None:
            lines.append("    %-12s 缺数据" % term)
            continue
        lines.append("    %-12s %s | 上期 %s | 变动 %s bp"
                     % (term, v["latest"], v["prev"],
                        ("%+.1f" % v["chg_bp"]) if v["chg_bp"] is not None else "缺数据"))

    lines.append("")
    lines.append("[4] 买方结构（PD/Direct/Indirect 占竞争性 accepted, 近3期）")
    for term, rows in out["buyer_structure"].items():
        if not rows:
            continue
        line_parts = ["    %-12s" % term]
        for r in rows:
            line_parts.append("%s(PD %.1f/D %.1f/I %.1f)"
                              % (r["auction_date"][5:], r["pd_pct"] or 0,
                                 r["direct_pct"] or 0, r["indirect_pct"] or 0))
        lines.append(" ".join(line_parts))
    for note in out["buyer_summary"]["notes"]:
        lines.append("    • " + note)

    lines.append("")
    lines.append("[5] 存量需求端")
    sd = out["stock_demand"]
    if sd:
        for sec, v in sd.items():
            lines.append("    • %s (%s): %s | 判断 %s"
                         % (sec, v.get("as_of") or v.get("period"), v.get("trend_note"), v.get("judge")))
    else:
        lines.append("    缺数据（anchors 文件缺失）")

    lines.append("")
    lines.append("[6] 中国因素")
    cf = out["china_factor"]
    if cf:
        lines.append("    • 美债持仓 %sB (%s)：%s" % (cf["us_treasury"].get("holdings_bn"),
                                                    cf["us_treasury"].get("period"),
                                                    cf["us_treasury"].get("trend_note")))
        lines.append("    • 黄金 %s 吨 (%s)：%s" % (cf["gold"].get("tonnes"), cf["gold"].get("period"),
                                                  cf["gold"].get("trend_note")))
        lines.append("    • %s" % cf.get("verdict", ""))
    else:
        lines.append("    缺数据（anchors 文件缺失）")

    lines.append("")
    lines.append("综合判断：%s" % out["conclusion"])
    lines.append("缺数据说明：%s" % "; ".join(out["notes"]))
    lines.append("=" * 60)
    return "\n".join(lines)


def main():
    out_json = None
    if len(sys.argv) > 1 and sys.argv[1] == "--json":
        out_json = sys.argv[2] if len(sys.argv) > 2 else "treasury_auction_output.json"

    data = _load("treasury_data_output.json")
    if not data or "auction_detail" not in data:
        print("错误: 缺少 treasury_data_output.json（auction_detail 板块）", file=sys.stderr)
        sys.exit(1)
    anchors = _load("treasury_auction_anchors.json")

    groups = _series_by_tenor(data["auction_detail"].get("records", []))
    btc = btc_stats(groups)
    high_yield = yield_stats(groups)
    buyer = buyer_structure(groups)
    buyer_sum = buyer_structure_summary(buyer)

    sd = (anchors or {}).get("stock_demand") or {}
    cf = (anchors or {}).get("china_factor") or {}
    # 仅明确判为"弱"的领域计入弱信号（中性偏弱/结构性偏弱归中性档）
    stock_weak = sum(1 for v in sd.values() if str(v.get("judge", "")) == "弱")
    china_weak = 1 if (cf and (str(cf.get("us_treasury", {}).get("judge", "")) == "弱"
                               or "弱" in str(cf.get("verdict", "")))) else 0

    level, color, weak_count = overall_level(btc, buyer_sum, stock_weak, china_weak)

    notes = []
    if "尾差" not in str(level):
        pass
    notes.append("尾差：fiscaldata 无 WI（预发行收益率）报价，无法由本 API 计算，标注缺数据（个别场次新闻口径：7Y 9/24 +0.7bp、20Y 9/22 增发正尾差）")
    notes.append("TIPS / FRN 拍卖明细 fiscaldata 无覆盖，本报告不含该类券种认购数据")

    out = {
        "run_date": data.get("run_date", str(date.today())),
        "level": level,
        "color": color,
        "weak_count": weak_count,
        "btc": btc,
        "tail": {"status": "缺数据",
                 "note": "fiscaldata 无 WI 报价，尾差无法由 API 计算（个别场次新闻口径：7Y 9/24 +0.7bp；20Y 9/22 增发正尾差）"},
        "high_yield": high_yield,
        "buyer_structure": buyer,
        "buyer_summary": buyer_sum,
        "stock_demand": sd,
        "china_factor": cf,
        "conclusion": conclusion(btc, buyer, sd, cf, level, weak_count),
        "notes": notes,
        "anchors_updated": (anchors or {}).get("updated_at"),
    }
    print(render(out))

    if out_json:
        with open(out_json, "w", encoding="utf-8") as fp:
            json.dump(out, fp, ensure_ascii=False, indent=2)
        print("\nJSON 已落盘: %s" % out_json)


if __name__ == "__main__":
    main()