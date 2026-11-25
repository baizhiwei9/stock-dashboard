#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""融资比值分位与杠杆率阈值提醒模块 -> data/ratio_alert.json

口径（全部基于近一年＝最近 242 个完整交易日，窗口起止与起点当日数据一并落盘）：
  ① 融资余额 ÷ 两市成交额     近一年分位 ≥90 → 风险（太火）；≤10 → 机会（太惨）
  ② 融资余额 ÷ 全A流通市值     近一年分位 ≥90 → 风险（太火）；≤10 → 机会（太惨）
     （该指标即「杠杆率」）
  ③ 杠杆率绝对值 > 2.8%        → 风险（绝对水平已达要求提醒的位置）
  ④ 227% 担保比例等效信号      触发 → 风险（触发式信号，不适用分位）

同时产出「两融统计汇总表」（summary_rows）：覆盖全部两融栏目，每一栏均给出
  当前值 / 近一年分位数 / 判定口径 / 窗口起点日（2025-10-23）当日值 / 标记 / **正常范围**。

「正常范围」口径（供读者判断当前值是否处于正常/安全区间）：
  以该指标**近一年（242 交易日）自身分布**的 P10~P90 分位带为正常区间，中位数 P50 为「正常中枢」；
  当前值落在正常范围内＝正常，高于上沿＝偏热（风险），低于下沿＝偏冷（机会）。
  触发式指标（227% 等效信号）正常状态＝未触发；自高点回落以窗口内逐日滚动回落幅度的 P10~P90 为正常带。
  该口径由本脚本每次运行确定性重算并落盘（不沿用旧分位）。

统一标记规则（表头声明、逐行口径可核）：
  近一年分位 ≥90 → 风险 🔴⭐（太火）；≤10 → 机会 🟢🌙（太惨）；10 < 分位 < 90 → 中性 ➖。
  显式阈值项（杠杆率绝对值 >2.8、227% 等效信号、自高点回落 ≥30%）按各自阈值判定，不再看分位。

分位定义：窗口内 ≤ 当前值的交易日占比 ×100。
数据源：data/long_series.csv（两融汇总 SSE+SZSE、两市成交额 上证综指+深证综指、全A流通市值 SH+SZ 求和）
        data/margin_module.json（227% 等效信号、自高点回落，均为同一基准日快照）。

用法：
  python ratio_alert.py            # 计算并落盘 data/ratio_alert.json
  python ratio_alert.py --print    # 额外打印可读摘要
"""
import argparse
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, "data")
SERIES = os.path.join(D, "long_series.csv")
MMOD = os.path.join(D, "margin_module.json")
OUT = os.path.join(D, "ratio_alert.json")

W_1Y = 242          # 近一年分位窗口（交易日）
LEV_ABS = 2.8       # 杠杆率绝对值提醒阈值（%）
PCT_HI = 90.0       # 分位上限阈值
PCT_LO = 10.0       # 分位下限阈值
PEAK_DROP = 30.0    # 融资余额自区间高点回落幅度提醒阈值（%）

import pandas as pd  # noqa: E402


def load_series():
    d = pd.read_csv(SERIES)
    d["trade_date"] = d.trade_date.astype(int)
    d = d[d.complete == True].sort_values("trade_date")   # noqa: E712
    return d


def ratios(d):
    """逐日派生比值与规模序列（列名 A_/B_/C_/D_ 与既有工程保持一致）。"""
    o = pd.DataFrame(index=d.trade_date.astype(int))
    o["rzye_yi"] = d.rzye.values / 1e8            # 融资余额（亿元）
    o["rzrqye_yi"] = d.rzrqye.values / 1e8        # 两融余额（亿元）
    o["rqye_yi"] = d.rqye.values / 1e8            # 融券余额（亿元）
    o["rzmre_yi"] = d.rzmre.values / 1e8          # 融资买入额（亿元）
    o["rzche_yi"] = d.rzche.values / 1e8          # 融资偿还额（亿元）
    o["netbuy_yi"] = (d.rzmre.values - d.rzche.values) / 1e8   # 融资净买入（亿元）
    o["netbuy20_yi"] = pd.Series(o.netbuy_yi.values, index=o.index).rolling(20).sum()
    o["amt_yi"] = d.amt.values / 1e5              # 两市成交额（亿元）
    o["circ_yi"] = d.circ_mv_wan.values * 1e4 / 1e8            # 全A流通市值（亿元）
    o["A_turn_pct"] = d.rzmre.values / (d.amt.values * 1000) * 100      # 融资买入额/成交额
    o["B_rzamt_pct"] = d.rzye.values / (d.amt.values * 1000) * 100      # 融资余额/成交额
    o["C_lev_pct"] = d.rzye.values / (d.circ_mv_wan.values * 1e4) * 100  # 融资余额/流通市值（杠杆率）
    o["D_rzrq_pct"] = d.rzrqye.values / (d.circ_mv_wan.values * 1e4) * 100
    o["rzye_dod_pct"] = pd.Series(o.rzye_yi.values, index=o.index).pct_change() * 100   # 融资余额日环比
    _peak = pd.Series(o.rzye_yi.values, index=o.index).cummax()                        # 滚动高点
    o["rzye_drop_pct"] = (o.rzye_yi.values - _peak.values) / _peak.values * 100         # 自滚动高点回落（%）
    return o


def pct_rank(s):
    """窗口内 ≤ 当前值的占比 ×100。"""
    s = s.dropna()
    if not len(s):
        return None
    v = s.iloc[-1]
    return float((s <= v).mean() * 100.0)


def band(s):
    """返回正常范围分位带 (P10, P50, P90)（线性插值，dropna）。"""
    s = s.dropna()
    if not len(s):
        return None, None, None
    return (float(s.quantile(0.10)), float(s.quantile(0.50)), float(s.quantile(0.90)))


def fmt_band(lo, med, hi, unit):
    """正常范围展示：亿元取 0 位小数、% 取 2 位小数，形如「低 ~ 高（中位 X）」。"""
    if lo is None:
        return "—"
    nd = 0 if unit == "亿元" else 2
    return f"{fmt(lo, nd)} ~ {fmt(hi, nd)}（中位 {fmt(med, nd)}）"


def judge_pct(p):
    """分位双轨判定：≥90 风险、≤10 机会。"""
    if p is None:
        return "neu", "无分位口径"
    if p >= PCT_HI:
        return "risk", "太火"
    if p <= PCT_LO:
        return "opp", "太惨"
    return "neu", "中性"


def fmt(v, nd):
    return "—" if v is None else f"{v:,.{nd}f}"


def main():
    ap = argparse.ArgumentParser(description="融资比值分位与杠杆率阈值提醒")
    ap.add_argument("--print", dest="show", action="store_true")
    a = ap.parse_args()

    d = load_series()
    r = ratios(d)
    win = r.tail(W_1Y)
    as_of = int(r.index[-1])
    first = int(win.index[0])
    last = int(win.index[-1])

    L = win.iloc[-1]
    F = win.iloc[0]      # 窗口最早一天（起点日）数据
    mmod = json.load(open(MMOD, encoding="utf-8")) if os.path.exists(MMOD) else {}
    mk = mmod.get("market", {})
    drop_peak = mk.get("rzye_drop_from_peak_pct")           # 已含负号（%）
    sig227 = bool(mmod.get("risk_track1", {}).get("sig227_triggered"))

    rules = []

    def add(key, name, col, unit, thr_text, kind, label, basis, extra=None):
        rules.append({"key": key, "name": name, "value": float(getattr(L, col)), "unit": unit,
                      "percentile_1y": float(pct_rank(win[col])), "threshold": thr_text,
                      "kind": kind, "label": label, "basis": basis, "extra": extra or {}})

    # ① 融资余额 ÷ 两市成交额
    p_b = pct_rank(win.B_rzamt_pct); k_b, l_b = judge_pct(p_b)
    add("B_rzamt", "融资余额 ÷ 两市成交额", "B_rzamt_pct", "%", f"分位 ≥{PCT_HI:.0f} 风险 / ≤{PCT_LO:.0f} 机会",
        k_b, l_b, f"近一年分位 {p_b:.2f}（窗口 {first}~{last}，共 {len(win)} 个交易日）"
        + ("，已达高位区间（太火）" if k_b == "risk" else "，已至低位区间（太惨）" if k_b == "opp" else "，处中性区间"))

    # ② 融资余额 ÷ 全A流通市值（＝杠杆率）分位
    p_c = pct_rank(win.C_lev_pct); k_c, l_c = judge_pct(p_c)
    add("C_lev_pct", "杠杆率（融资余额 ÷ 全A流通市值）· 分位", "C_lev_pct", "%",
        f"分位 ≥{PCT_HI:.0f} 风险 / ≤{PCT_LO:.0f} 机会", k_c, l_c,
        f"近一年分位 {p_c:.2f}（窗口 {first}~{last}，共 {len(win)} 个交易日）"
        + ("，已达高位区间（太火）" if k_c == "risk" else "，已至低位区间（太惨）" if k_c == "opp" else "，处中性区间"))

    # ③ 杠杆率绝对值 > 2.8
    v_c = float(L.C_lev_pct)
    k_a, l_a = ("risk", "绝对值高位") if v_c > LEV_ABS else ("neu", "绝对值未触发")
    add("C_lev_abs", "杠杆率（绝对值）", "C_lev_pct", "%", f"绝对值 >{LEV_ABS:.1f} 提醒", k_a, l_a,
        f"当前 {v_c:.4f}%，提醒阈值 {LEV_ABS:.1f}%"
        + (f"，已超阈值 {v_c - LEV_ABS:+.4f} 个百分点" if v_c > LEV_ABS else f"，距阈值还有 {LEV_ABS - v_c:.4f} 个百分点"))

    # ④ 227% 担保比例等效信号（触发式，无分位口径）
    rules.append({
        "key": "SIG227", "name": "227% 担保比例等效信号", "value": "触发" if sig227 else "未触发",
        "unit": "状态", "percentile_1y": None, "threshold": "触发 → 风险",
        "kind": ("risk" if sig227 else "neu"), "label": ("触发" if sig227 else "未触发"),
        "basis": "等效口径：融资余额日环比连续 2 日 <-0.3% 且加速（accel_out）叠加 "
                 "融资买入占比较 5 日前回落 ≥1pct 或分位 <20（turn_plunge）；"
                 "本工程无担保品市值，故以该等效信号代理浙商证券 227% 阈值（触发式信号，不适用分位）",
        "extra": {"not_percentile": True, "display": "触发" if sig227 else "未触发"}})

    n_risk = sum(1 for x in rules if x["kind"] == "risk")
    n_opp = sum(1 for x in rules if x["kind"] == "opp")

    # ---------------- 两融统计汇总表（覆盖全部两融栏目） ----------------
    def row(name, col, unit, nd, rule, override=None, start_note=None, cur_display=None,
            normal_note=None, band_col="__auto__"):
        p = pct_rank(win[col]) if col else None
        kind, label = judge_pct(p)
        if override is not None:
            kind, label = override
        bc = col if band_col == "__auto__" else band_col
        lo = med = hi = None
        normal_display = "—"
        if bc and bc in win.columns:
            lo, med, hi = band(win[bc])
            normal_display = fmt_band(lo, med, hi, unit)
        if normal_note is None:
            normal_note = f"该指标近一年自身分布的 P10~P90 分位带（中位 P50）；窗口 {first}~{last}"
        return {
            "indicator": name, "unit": unit,
            "value": (float(cur_display) if cur_display is not None
                      else (float(getattr(L, col)) if col else None)),
            "value_display": fmt(float(getattr(L, col)), nd) if col else "—",
            "percentile_1y": (None if p is None else float(round(p, 4))),
            "percentile_display": ("—" if p is None else f"P{p:.2f}"),
            "rule": rule, "kind": kind, "label": label,
            "normal_display": normal_display,
            "normal_low": (None if lo is None else round(lo, 4)),
            "normal_med": (None if med is None else round(med, 4)),
            "normal_high": (None if hi is None else round(hi, 4)),
            "normal_note": normal_note,
            "start_date": first,
            "start_display": (start_note if start_note is not None
                              else (fmt(float(getattr(F, col)), nd) if col else "—")),
        }

    rule_p = f"近一年分位 ≥{PCT_HI:.0f} 风险 / ≤{PCT_LO:.0f} 机会"
    summary = []
    summary.append(row("杠杆率（融资余额 ÷ 全A流通市值）", "C_lev_pct", "%", 4,
                       f"{rule_p}；另：绝对值 >{LEV_ABS:.1f}% 提醒（当前 {v_c:.4f}%，"
                       + ("已超阈值" if v_c > LEV_ABS else f"距阈值 {LEV_ABS - v_c:.4f}pct") + "）"))
    summary.append(row("融资余额", "rzye_yi", "亿元", 2, rule_p))
    summary.append(row("融资余额日环比", "rzye_dod_pct", "%", 4, rule_p))
    summary.append(row("融资买入额占比（融资买入额 ÷ 两市成交额）", "A_turn_pct", "%", 4, rule_p))
    summary.append(row("融资余额 ÷ 两市成交额", "B_rzamt_pct", "%", 4, rule_p))
    summary.append(row("融资买入额", "rzmre_yi", "亿元", 2, rule_p))
    summary.append(row("融资偿还额", "rzche_yi", "亿元", 2, rule_p))
    summary.append(row("融资净买入（当日）", "netbuy_yi", "亿元", 2, rule_p))
    summary.append(row("融资净买入（20 日累计）", "netbuy20_yi", "亿元", 2, rule_p))
    summary.append(row("两融余额", "rzrqye_yi", "亿元", 2, rule_p))
    summary.append(row("融券余额", "rqye_yi", "亿元", 2, rule_p))
    summary.append(row("两融余额 ÷ 全A流通市值", "D_rzrq_pct", "%", 4, rule_p))
    summary.append(row("227% 担保比例等效信号", None, "状态", 0, "触发式：触发 → 风险（不适用分位）",
                       override=("risk", "触发") if sig227 else ("neu", "未触发"),
                       start_note="不适用（触发式信号，无分位口径）",
                       cur_display=1.0 if sig227 else 0.0))
    summary[-1]["value_display"] = "触发" if sig227 else "未触发"
    summary[-1]["normal_display"] = "未触发（触发即为风险）"
    summary[-1]["normal_note"] = "触发式指标：正常状态＝未触发；不适用分位带"
    summary.append(row("融资余额自区间高点回落", None, "%", 2,
                       f"绝对值 ≥{PEAK_DROP:.0f}% 风险（当前 {drop_peak:.2f}%，高点 {mk.get('rzye_peak_date')} / {mk.get('rzye_peak_yi')} 亿）",
                       override=("risk", "回撤超阈值") if (drop_peak is not None and abs(drop_peak) >= PEAK_DROP)
                       else ("neu", "回撤未超阈值"),
                       start_note="不适用（高点口径跨窗口，非分位序列）",
                       cur_display=float(drop_peak) if drop_peak is not None else None,
                       band_col="rzye_drop_pct",
                       normal_note=f"窗口内逐日「自滚动高点回落幅度」的 P10~P90 分位带（中位 P50）；窗口 {first}~{last}"))
    summary[-1]["value_display"] = "—" if drop_peak is None else f"{drop_peak:.2f}"

    s_risk = sum(1 for x in summary if x["kind"] == "risk")
    s_opp = sum(1 for x in summary if x["kind"] == "opp")

    payload = {
        "as_of": as_of,
        "window": {"n_trading_days": int(len(win)), "first_date": first, "last_date": last,
                   "definition": f"近一年 = 最近 {W_1Y} 个完整交易日",
                   "percentile_rule": "窗口内 ≤ 当前值的交易日占比 ×100"},
        "thresholds": {"pct_high": PCT_HI, "pct_low": PCT_LO, "lev_abs": LEV_ABS, "peak_drop": PEAK_DROP},
        "mark_rule": (f"近一年分位 ≥{PCT_HI:.0f} → 风险 🔴⭐（太火）；≤{PCT_LO:.0f} → 机会 🟢🌙（太惨）；"
                      f"10 < 分位 < 90 → 中性 ➖；显式阈值项（杠杆率绝对值 >{LEV_ABS:.1f}、227% 等效信号、"
                      f"自高点回落 ≥{PEAK_DROP:.0f}%）按各自阈值判定"),
        "rules": rules,
        "normal_range_rule": (
            f"正常范围 = 该指标近一年（最近 {W_1Y} 个完整交易日，即 {first}~{last}）自身分布的 P10~P90 分位带，"
            f"中位数 P50 为正常中枢；当前值落带内＝正常、高于上沿＝偏热（风险）、低于下沿＝偏冷（机会）。"
            f"触发式指标（227% 等效信号）正常状态＝未触发；自高点回落以窗口内逐日滚动回落幅度的 P10~P90 为正常带。"
            f"该口径与汇总表分位判定同源，每次运行确定性重算并落盘，不沿用旧分位。"),
        "summary_rows": summary,
        "summary_stats": {"n_rows": len(summary), "n_risk": s_risk, "n_opp": s_opp,
                          "n_neu": len(summary) - s_risk - s_opp},
        "n_risk": n_risk, "n_opp": n_opp, "triggered": bool(n_risk + n_opp > 0),
        "start_point": {
            "trade_date": first,
            "融资余额_亿": round(float(F.rzye_yi), 2),
            "两市成交额_亿": round(float(F.amt_yi), 2),
            "全A流通市值_亿": round(float(F.circ_yi), 2),
            "融资余额÷两市成交额_%": round(float(F.B_rzamt_pct), 4),
            "杠杆率_%": round(float(F.C_lev_pct), 4),
            "A_融资买入额÷成交额_%": round(float(F.A_turn_pct), 4),
            "D_两融余额÷流通市值_%": round(float(F.D_rzrq_pct), 4),
        },
        "current": {
            "trade_date": as_of,
            "融资余额_亿": round(float(L.rzye_yi), 2),
            "两市成交额_亿": round(float(L.amt_yi), 2),
            "全A流通市值_亿": round(float(L.circ_yi), 2),
            "融资余额÷两市成交额_%": round(float(L.B_rzamt_pct), 4),
            "杠杆率_%": round(float(L.C_lev_pct), 4),
            "A_融资买入额÷成交额_%": round(float(L.A_turn_pct), 4),
            "D_两融余额÷流通市值_%": round(float(L.D_rzrq_pct), 4),
            "两融余额_亿": round(float(L.rzrqye_yi), 2),
            "融券余额_亿": round(float(L.rqye_yi), 2),
            "融资买入额_亿": round(float(L.rzmre_yi), 2),
            "融资偿还额_亿": round(float(L.rzche_yi), 2),
            "融资净买入_亿": round(float(L.netbuy_yi), 2),
        },
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    json.dump(payload, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    if a.show:
        print(f"as_of {as_of} | 窗口 {first}~{last} 共 {len(win)} 个交易日")
        for x in rules:
            mk_ = "🔴⭐" if x["kind"] == "risk" else ("🟢🌙" if x["kind"] == "opp" else "➖")
            vs = (f"{x['value']:.4f}" if isinstance(x["value"], (int, float)) else str(x["value"]))
            ps = ("—" if x["percentile_1y"] is None else f"{x['percentile_1y']:.2f}")
            print(f"  {mk_} {x['name']}: {vs}{x['unit']}"
                  f" | 近一年分位 {ps} | {x['label']}")
            print(f"      {x['basis']}")
        print(f"  触发：风险 {n_risk} 项 / 机会 {n_opp} 项 → {'有提醒' if payload['triggered'] else '无提醒'}")
        print(f"  起点日 {first}：融资余额 {F.rzye_yi:.2f}亿 成交额 {F.amt_yi:.2f}亿 "
              f"流通市值 {F.circ_yi:.2f}亿 | B {F.B_rzamt_pct:.4f}% | 杠杆率 {F.C_lev_pct:.4f}%")
        print(f"\n== 两融统计汇总表（{len(summary)} 栏，窗口起点日 {first}）==")
        for x in summary:
            mk_ = "🔴⭐" if x["kind"] == "risk" else ("🟢🌙" if x["kind"] == "opp" else "➖")
            print(f"  {mk_} {x['indicator']}: {x['value_display']}{x['unit']}"
                  f" | 分位 {x['percentile_display']} | 起点值 {x['start_display']} | {x['label']}")
    print(f"ratio_alert.json 落盘：触发风险 {n_risk} / 机会 {n_opp}；汇总表 {len(summary)} 栏"
          f"（风险 {s_risk} / 机会 {s_opp} / 中性 {len(summary)-s_risk-s_opp}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())