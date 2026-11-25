#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""将军四区间判定（期指贴水周期 regime）

区间定义
  1 贴水期    缩量降波、四品种贴水扩大、远月 back 深贴水
  2 散点化运行 贴水不再单向扩大、无一品种升水、风格与波动分位大分化
  3 升水加速   基差转正或近平水且升水扩大、放量上行、涨停与连板同步抬升
  4 升水失败   先出现升水再快速回吐、价格冲高回落失败、炸板与高熵端跌停抬升

与 structural_factors.IntervalDetector（风险释放 / 修复反弹 两区间）互不替代：
本模块只判定**期指贴水周期**，输入为日更取数脚本产出的事实包。

判定纪律
  - 只用事实包字段，缺项标 NA，不推断、不用旧值顶替；
  - 先给本期区间，再逐项给出排除其余三个区间的理由与当期数值；
  - 次月较前日 Δ、20 日年化波动分位属于**可选**字段，缺失时降级为
    绝对贴水水平 + QVIX 分位代理，并在 na 中如实列出。

用法
  python zone_regime.py --facts examples/zone_facts_20261116.json
  python zone_regime.py --facts facts.json --json
  python zone_regime.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

ZONE_LABELS = {
    1: "区间一 贴水期",
    2: "区间二 散点化运行",
    3: "区间三 升水加速",
    4: "区间四 升水失败",
}

# ---- 判据阈值（单位：% / 百分点 / 点） ----
TH_NEAR_FLAT = 0.35      # 当月基差绝对值 <= 0.35% 视为近平水
TH_DEEP_DISCOUNT = 0.80  # 次月贴水 >= 0.80% 视为深贴水
TH_PREMIUM = 0.10        # 基差 > +0.10% 视为升水
TH_DISP_DD = 5.0         # 低熵端与高熵端 20 日回撤差 >= 5pp 视为散点化
TH_DISP_QVIX = 25.0      # 各宽基 QVIX 分位极差 >= 25 个分位点视为散点化
TH_QVIX_HIGH = 80.0      # QVIX 分位 >= 80 视为高波/高熵
TH_QVIX_LOW = 30.0       # QVIX 分位 <= 30 视为低波
TH_TURNOVER_SHRINK = -5.0  # 成交较前日 <= -5% 视为缩量
TH_TURNOVER_EXPAND = 5.0   # 成交较前日 >= +5% 视为放量
TH_DELTA_MATERIAL = 2.0    # 次月基差单日变动 >= 2 点视为实质变动
TH_ZT_HIGH = 120           # 涨停家数 >= 120 视为情绪抬升
TH_ZT_PEAK = 180           # 硬阈值：涨停家数
TH_LB_PEAK = 47            # 硬阈值：连板高度

LOW_ENTROPY = ("000016.SH", "000300.SH")            # 上证50、沪深300
HIGH_ENTROPY = ("399006.SZ", "000852.SH", "000688.SH")  # 创业板、中证1000、科创50
INDEX_NAMES = {
    "000001.SH": "上证综指",
    "399001.SZ": "深证成指",
    "000300.SH": "沪深300",
    "000016.SH": "上证50",
    "000905.SH": "中证500",
    "000852.SH": "中证1000",
    "000688.SH": "科创50",
    "399006.SZ": "创业板",
}
ROOTS = ("IF", "IH", "IC", "IM")


def _f(x) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


@dataclass
class Signals:
    """从事实包提取的判据信号，缺失项为 None。"""

    data_date: str = ""
    near_bases: Dict[str, Optional[float]] = field(default_factory=dict)
    next_bases: Dict[str, Optional[float]] = field(default_factory=dict)
    next_deltas: Dict[str, Optional[float]] = field(default_factory=dict)
    premium: Dict[str, Optional[float]] = field(default_factory=dict)
    near_flat_count: int = 0
    deep_discount_count: int = 0
    premium_count: int = 0
    dd20: Dict[str, Optional[float]] = field(default_factory=dict)
    qvix_pct: Dict[str, Optional[float]] = field(default_factory=dict)
    dispersion_dd: Optional[float] = None
    dispersion_qvix: Optional[float] = None
    dispersion_pair: str = ""
    qvix_max: Optional[float] = None
    qvix_min: Optional[float] = None
    turnover_chg: Optional[float] = None
    zt: Optional[int] = None
    top_lb: Optional[int] = None
    na: List[str] = field(default_factory=list)

    # 派生状态
    @property
    def any_premium(self) -> bool:
        return self.premium_count > 0

    @property
    def has_delta(self) -> bool:
        return any(v is not None for v in self.next_deltas.values())

    @property
    def delta_all_up(self) -> Optional[bool]:
        vals = [v for v in self.next_deltas.values() if v is not None]
        if not vals:
            return None
        return all(v > 0 for v in vals)

    @property
    def delta_all_down(self) -> Optional[bool]:
        vals = [v for v in self.next_deltas.values() if v is not None]
        if not vals:
            return None
        return all(v < -TH_DELTA_MATERIAL for v in vals)

    @property
    def vol_high(self) -> Optional[bool]:
        return None if self.qvix_max is None else self.qvix_max >= TH_QVIX_HIGH

    @property
    def vol_low(self) -> Optional[bool]:
        return None if self.qvix_max is None else self.qvix_max <= TH_QVIX_LOW

    @property
    def shrink(self) -> Optional[bool]:
        return None if self.turnover_chg is None else self.turnover_chg <= TH_TURNOVER_SHRINK

    @property
    def expand(self) -> Optional[bool]:
        return None if self.turnover_chg is None else self.turnover_chg >= TH_TURNOVER_EXPAND

    @property
    def dispersion(self) -> bool:
        return bool(
            (self.dispersion_dd is not None and self.dispersion_dd >= TH_DISP_DD)
            or (self.dispersion_qvix is not None and self.dispersion_qvix >= TH_DISP_QVIX)
        )


def extract_signals(facts: dict) -> Signals:
    """把事实包折算成判据信号。缺字段一律记入 na，不做推断。"""
    sig = Signals(data_date=str(facts.get("data_date") or ""))
    fut = facts.get("futures") or {}
    idx = facts.get("index") or {}
    qvix = facts.get("qvix") or {}
    turnover = facts.get("turnover") or {}
    senti = facts.get("sentiment") or {}

    for root in ROOTS:
        near = _f((fut.get(f"{root}_0") or {}).get("basis_pct"))
        nxt = _f((fut.get(f"{root}_1") or {}).get("basis_pct"))
        sig.near_bases[root] = near
        sig.next_bases[root] = nxt
        delta = _f((fut.get(f"{root}_1") or {}).get("delta_pts"))
        sig.next_deltas[root] = delta
    for root, pct in sig.next_bases.items():
        if pct is not None and pct > TH_PREMIUM:
            sig.premium[root] = pct
    sig.premium_count = len(sig.premium)
    sig.near_flat_count = sum(1 for v in sig.near_bases.values() if v is not None and abs(v) <= TH_NEAR_FLAT)
    sig.deep_discount_count = sum(
        1 for v in sig.next_bases.values() if v is not None and v <= -TH_DEEP_DISCOUNT
    )
    if sig.near_flat_count < 4:
        sig.na.append(f"当月基差缺失或不足四品种（仅 {sig.near_flat_count}/4 近平水）")
    if sig.deep_discount_count < 4:
        sig.na.append(f"次月基差缺失或不足四品种（仅 {sig.deep_discount_count}/4 深贴水）")
    if not sig.has_delta:
        sig.na.append("四品种次月基差较前日Δ：事实包未提供，贴水方向改用绝对水平与 QVIX 分位代理")

    for code in LOW_ENTROPY + HIGH_ENTROPY:
        sig.dd20[code] = _f((idx.get(code) or {}).get("dd20"))
    lows = [sig.dd20[c] for c in LOW_ENTROPY if sig.dd20.get(c) is not None]
    highs = [sig.dd20[c] for c in HIGH_ENTROPY if sig.dd20.get(c) is not None]
    if lows and highs:
        # 低熵锚用上证50（缺失时退回沪深300），高熵锚用跌得最深的高熵指数
        low_code = "000016.SH" if sig.dd20.get("000016.SH") is not None else "000300.SH"
        low_val = sig.dd20.get(low_code)
        high_code = min(
            (c for c in HIGH_ENTROPY if sig.dd20.get(c) is not None),
            key=lambda c: sig.dd20[c],
        )
        if low_val is not None:
            sig.dispersion_dd = round(low_val - sig.dd20[high_code], 2)
            sig.dispersion_pair = f"{INDEX_NAMES.get(low_code, low_code)} vs {INDEX_NAMES.get(high_code, high_code)}"

    for name, item in qvix.items():
        sig.qvix_pct[name] = _f((item or {}).get("pct_252"))
    vals = [v for v in sig.qvix_pct.values() if v is not None]
    if vals:
        sig.qvix_max, sig.qvix_min = max(vals), min(vals)
        sig.dispersion_qvix = round(sig.qvix_max - sig.qvix_min, 2)
    if not vals:
        sig.na.append("QVIX 分位缺失，波动状态无法判定")

    total_yi = _f(turnover.get("total_yi"))
    prev_yi = _f(turnover.get("prev_yi"))
    if total_yi and prev_yi:
        sig.turnover_chg = round((total_yi / prev_yi - 1) * 100, 2)
    if sig.turnover_chg is None:
        sig.na.append("两市成交及较前日增减：缺失，缩量/放量无法验证")

    sig.zt = senti.get("zt")
    sig.top_lb = senti.get("top_lb")
    if sig.zt is None or sig.top_lb is None:
        sig.na.append("涨停家数与连板高度：缺失，情绪高峰与硬阈值无法量化")
    return sig


def _fmt(v: Optional[float], suffix: str = "%") -> str:
    return "NA" if v is None else f"{v:.2f}{suffix}"


def judge(sig: Signals, prev_zone: Optional[int] = None, prev_premium: bool = False) -> dict:
    """四区间判定。返回 zone / 依据 / 排除理由 / NA 清单。"""
    if all(v is None for v in sig.next_bases.values()):
        return {
            "zone": None,
            "label": "NA（基差缺失，不判定）",
            "data_date": sig.data_date,
            "reason": "四品种基差全部缺失，无法判定周期位置",
            "basis": "当月/次月基差均为 NA",
            "non_1": None,
            "non_3": None,
            "non_4": None,
            "hard_thresholds": [],
            "na": sig.na + ["基差全缺，区间判定跳过"],
            "confidence": "low",
        }

    basis_txt = (
        f"当月 " + "/".join(f"{r} {_fmt(sig.near_bases.get(r))}" for r in ROOTS)
        + "；次月 " + "/".join(f"{r} {_fmt(sig.next_bases.get(r))}" for r in ROOTS)
    )

    # —— 区间三：有品种升水且在加速 ——
    zone3 = sig.any_premium and (
        sig.premium_count >= 2 or bool(sig.expand) or (sig.zt or 0) >= TH_ZT_HIGH
    )
    # —— 区间四：上一期已是升水加速，本期升水消失/回吐 ——
    zone4 = (not sig.any_premium) and (prev_zone == 3 or prev_premium)
    # —— 区间一：深贴水 + 缩量或低波 + 贴水未收窄 ——
    calm = bool(sig.shrink) or bool(sig.vol_low)
    zone1 = (
        sig.deep_discount_count >= 3
        and calm
        and not (sig.delta_all_up is True)
    )

    if zone3:
        zone = 3
        reason = (
            f"{sig.premium_count} 个品种基差转升水（"
            + "、".join(f"{r} {_fmt(v)}" for r, v in sig.premium.items())
            + "），且"
            + ("放量" if sig.expand else ("涨停抬升" if (sig.zt or 0) >= TH_ZT_HIGH else "升水品种扩散"))
        )
    elif zone4:
        zone = 4
        reason = (
            f"上期区间 {'三' if prev_zone == 3 else '（升水）'} 之后基差回吐：本期无一品种升水，"
            f"次月最深 {_fmt(min(v for v in sig.next_bases.values() if v is not None) if any(v is not None for v in sig.next_bases.values()) else None)}"
        )
    elif zone1:
        zone = 1
        reason = (
            f"四品种中 {sig.deep_discount_count} 个次月深贴水（>= {TH_DEEP_DISCOUNT:.2f}%），"
            f"且{'缩量' if sig.shrink else '低波'}（QVIX 分位最高 {_fmt(sig.qvix_max, '%')}），贴水未确认收窄"
        )
    else:
        zone = 2
        reason = (
            f"贴水未单向扩大（Δ "
            + ("全线为正" if sig.delta_all_up else ("缺失→用绝对水平代理" if not sig.has_delta else "分化"))
            + f"），无一品种升水，散点化成立（{sig.dispersion_pair or '回撤'} 差 {_fmt(sig.dispersion_dd, 'pp')}、QVIX 分位极差 {_fmt(sig.dispersion_qvix, '个分位点')}）"
        )

    # —— 排除其余三个区间（带当期数值） ——
    non = {}
    if zone != 1:
        bits = []
        if sig.delta_all_up:
            bits.append("次月Δ全线为正，贴水未扩大")
        if not sig.has_delta:
            bits.append("Δ 缺失，改用当月绝对水平")
        if sig.near_flat_count >= 3:
            bits.append(f"当月 {sig.near_flat_count}/4 近平水")
        if sig.vol_high:
            bits.append(f"QVIX 分位最高 {_fmt(sig.qvix_max)} 处高位，非降波")
        if sig.shrink is False:
            bits.append("成交未缩量")
        if sig.shrink is None:
            bits.append("缩量无法验证（NA）")
        non[1] = "；".join(bits) or "缩量降波与贴水扩大均不成立"
    if zone != 3:
        if not sig.any_premium:
            non[3] = f"四品种次月均贴水（最深 {_fmt(min(v for v in sig.next_bases.values() if v is not None) if any(v is not None for v in sig.next_bases.values()) else None)}），无升水加速"
        else:
            non[3] = f"升水品种仅 {sig.premium_count} 个且无放量/涨停抬升确认"
    if zone != 4:
        non[4] = "本期或上期均无升水前置，不存在升水回吐" if (prev_zone != 3 and not prev_premium) else "升水尚未被证伪"

    hard = []
    if sig.zt is not None and sig.zt >= TH_ZT_PEAK:
        hard.append(f"涨停 {sig.zt} >= {TH_ZT_PEAK}")
    if sig.top_lb is not None and sig.top_lb >= TH_LB_PEAK:
        hard.append(f"连板 {sig.top_lb} >= {TH_LB_PEAK}")

    confidence = "high"
    if not sig.has_delta or sig.qvix_max is None or sig.zt is None:
        confidence = "medium"
    if zone == 2 and not sig.dispersion and not sig.near_flat_count >= 3:
        confidence = "low"

    return {
        "zone": zone,
        "label": ZONE_LABELS[zone],
        "data_date": sig.data_date,
        "reason": reason,
        "basis": basis_txt,
        "non_1": non.get(1),
        "non_3": non.get(3),
        "non_4": non.get(4),
        "hard_thresholds": hard,
        "na": sig.na,
        "confidence": confidence,
    }


def load_facts(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def judge_facts_file(path: str, prev_zone: Optional[int] = None, prev_premium: bool = False) -> dict:
    return judge(extract_signals(load_facts(path)), prev_zone=prev_zone, prev_premium=prev_premium)


def format_verdict(v: dict) -> str:
    lines = [
        f"数据日 {v['data_date']} → {v['label']}（置信度 {v['confidence']}）",
        f"依据：{v['reason']}",
        f"基差：{v['basis']}",
        f"非区间一：{v['non_1'] or '-'}",
        f"非区间三：{v['non_3'] or '-'}",
        f"非区间四：{v['non_4'] or '-'}",
    ]
    if v["hard_thresholds"]:
        lines.append("硬阈值触发：" + "；".join(v["hard_thresholds"]))
    if v["na"]:
        lines.append("NA：" + "；".join(v["na"]))
    return "\n".join(lines)


def _selftest() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    sample = os.path.join(here, "examples", "zone_facts_20261116.json")
    v = judge_facts_file(sample)
    print(format_verdict(v))
    assert v["zone"] == 2, f"样例应判区间二，实际 {v['zone']}"
    assert "NA" in format_verdict(v)
    print("\nselftest OK")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="将军四区间（期指贴水周期）判定")
    ap.add_argument("--facts", help="事实包 JSON 路径（fetch_zone_facts.py 产出）")
    ap.add_argument("--prev-zone", type=int, choices=[1, 2, 3, 4], default=None, help="上次判定区间")
    ap.add_argument("--prev-premium", action="store_true", help="上期是否存在升水")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--selftest", action="store_true", help="用内置样例自检")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    if not args.facts:
        ap.error("需要 --facts 或 --selftest")
    v = judge_facts_file(args.facts, prev_zone=args.prev_zone, prev_premium=args.prev_premium)
    print(json.dumps(v, ensure_ascii=False, indent=2) if args.json else format_verdict(v))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())