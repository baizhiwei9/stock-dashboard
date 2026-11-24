# -*- coding: utf-8 -*-
"""债市分析 · 四层判定引擎（纯规则，无大模型）。

四层结构（沿用原 `bin/cn_bond_layered.py` 的判定口径，但数据全部来自 DataHub）：

    第①层 国债曲线：长端/短端在历史分布的什么位置，曲线陡平到什么程度
    第②层 信用利差：各评级档对同期限国债的利差，看评级分层还在不在
    第③层 同业存单：银行负债成本（NCD）相对国债的溢价，看息差压力
    第④层 资金结构：隔夜回购占比 / 非银流动性分层，看杠杆与去杠杆风险

另加（不属于四层，层级标签写「政策利率」）：

    第⑥段 对政策利率利差：10Y国债 / DR007 / NCD1Y 分别减**政策利率**
           （2026-11-02 新框架下 = 隔夜逆回购操作利率，每个工作日固定利率、
           数量招标；7 天期已改利率招标，无单一利率，不能当政策利率）

判定原则
--------
**全部用样本分位，不写死绝对阈值。** 因为不同时期利率中枢差异很大，
「1.75% 算高」这种硬编码过半年就失效。分位阈值写在本模块顶部，便于复盘时调。

已知样本限制（必须随结论一起给出）
----------------------------------
中国货币网只回溯约 3 个月，因此中债曲线的分位样本是「建库以来」，
不是一年。方向可靠（四档同步到极值这种结构特征真实），但绝对分位读数偏敏感。
`CN.*` 宏观、`RATE.US.*`、`CREDIT.US.*` 是多年历史，分位不受此限制。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# ---- 分位阈值（复盘时改这里，不改逻辑）----
TH = {
    "curve_low": 5.0,        # 国债 10Y 分位 ≤5% → 长端极值
    "curve_high": 95.0,      # ≥95% → 长端高位（债牛结束信号）
    "curve_flat": 10.0,      # 10Y-2Y 分位 ≤10% → 曲线极平
    "credit_extreme": 5.0,   # 各档信用利差分位 ≤5% → 压到极值
    "credit_extreme_cnt": 3, # ≥3 档同时到极值 → 评级分层消失（资产荒）
    "credit_no_tier_bp": 10, # AA 与 AAA 差 <10bp → 短端不分层
    "ncd_low": 20.0,         # NCD 利差分位 ≤20% → 负债成本被压到极低
    "ncd_high": 80.0,        # ≥80% → 负债端出问题
    "on_high": 80.0,         # 隔夜占比分位 ≥80% → 靠隔夜滚杠杆
    "r_dr_high": 80.0,       # R007-DR007 分位 ≥80% → 非银真分层
    # ---- 对政策利率利差（政策利率 = 隔夜逆回购操作利率 LIQ.CN.OMO.POLICY）----
    "policy_narrow": 10.0,   # 10Y−政策利率 分位 ≤10% → 长端对政策利率几乎无溢价，透支极致
    "policy_wide": 90.0,     # ≥90% → 对政策利率溢价厚，缓冲足
    "dr_policy_high": 85.0,  # DR007−政策利率 分位 ≥85% → 资金价格明显高于政策利率，资金面偏紧
}


@dataclass
class Verdict:
    """一条判定结论（层级 / 结论 / 提示 / 危险度）。"""
    layer: str
    text: str
    level: str = "info"          # red / orange / green / info
    detail: str = ""


@dataclass
class BondState:
    """债市四层快照。"""
    asof: Optional[str] = None
    source: str = "data_hub.edb (DataHub 三级灾备)"
    govt_curve: List[dict] = field(default_factory=list)
    govt_spreads: List[dict] = field(default_factory=list)
    credit: List[dict] = field(default_factory=list)
    credit_matrix: List[dict] = field(default_factory=list)
    ncd: List[dict] = field(default_factory=list)
    ncd_spread: Dict = field(default_factory=dict)
    funding: Dict[str, dict] = field(default_factory=dict)
    cbond_index: Optional[dict] = None
    macro: Dict[str, dict] = field(default_factory=dict)
    # 对政策利率利差（第⑥段）
    policy_spreads: List[dict] = field(default_factory=list)
    policy_rate: Optional[float] = None
    policy_rate_date: Optional[str] = None
    verdicts: List[Verdict] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "asof": self.asof, "source": self.source,
            "layer1_curve": self.govt_curve, "layer1_spreads": self.govt_spreads,
            "layer2_credit": self.credit, "layer2_matrix": self.credit_matrix,
            "layer3_ncd": self.ncd, "layer3_ncd_spread": self.ncd_spread,
            "layer4_funding": self.funding, "cbond_index": self.cbond_index,
            "macro": self.macro,
            "policy_rate": self.policy_rate,
            "policy_rate_date": self.policy_rate_date,
            "policy_spreads": self.policy_spreads,
            "verdicts": [{"layer": v.layer, "text": v.text, "level": v.level,
                          "detail": v.detail} for v in self.verdicts],
            "notes": self.notes,
        }


def _flag(p: Optional[float], low: float, high: float) -> str:
    """分位落在 [.., low] → red；[low, high] 内按区间；≥high → red。"""
    if p is None:
        return "info"
    if p <= low or p >= high:
        return "red"
    return "orange" if (p <= low * 4 or p >= high - (100 - high) * 4) else "green"


def build_state(src) -> BondState:
    """从 BondData 构建四层快照（src 为 data.BondData 实例）。"""
    S = BondState()

    # ---- 第①层 ----
    S.govt_curve = src.govt_curve()
    S.govt_spreads = src.govt_spreads()
    if S.govt_curve:
        S.asof = S.govt_curve[-1].get("date") or dt.date.today().isoformat()

    # ---- 第②层 ----
    S.credit = src.credit_spreads("3Y")
    S.credit_matrix = src.credit_matrix()

    # ---- 第③层 ----
    S.ncd = src.ncd()
    v, p = src.spread_last("BOND.CN.NCD.AAA.1Y", "BOND.CN.GOVT.1Y")
    S.ncd_spread = {"value_bp": round(v, 1) if v is not None else None, "pct": p}
    S.cbond_index = src.cbond_index()

    # ---- 第④层 ----
    S.funding = src.funding()

    # ---- 宏观（补充，不参与四层判定）----
    S.macro = src.macro()

    # ---- 第⑥段：对政策利率利差（新框架政策利率锚 = 隔夜逆回购操作利率）----
    pr = getattr(src, "policy_rate", None)
    if callable(pr):
        r = pr() or {}
    elif isinstance(pr, dict):
        r = pr
    else:
        r = {}
    S.policy_rate = r.get("value")
    S.policy_rate_date = r.get("date")
    ps = getattr(src, "policy_spreads", None)
    S.policy_spreads = ps() if callable(ps) else (ps or [])

    S.verdicts = judge(S)
    S.notes = _notes(S)
    return S


def judge(S: BondState) -> List[Verdict]:
    """把四层数值翻成结论。"""
    V: List[Verdict] = []
    g = {x["tenor"]: x for x in S.govt_curve}

    # ① 国债曲线
    y10 = g.get("10Y")
    if y10:
        p = y10.get("pct")
        if p is not None and p <= TH["curve_low"]:
            V.append(Verdict("第①层", f"国债 10Y {y10['value']:.4f}%（{p:.1f}% 分位）→ "
                              "**长端压到历史极值**", "red",
                              "债牛极致，但缓冲垫为零；任何资金扰动会直接砸长端"))
        elif p is not None and p >= TH["curve_high"]:
            V.append(Verdict("第①层", f"国债 10Y {y10['value']:.4f}%（{p:.1f}% 分位）→ "
                              "**长端到历史高位**", "red",
                              "资产荒松动 / 债牛结束的第一信号"))
        else:
            V.append(Verdict("第①层", f"国债 10Y {y10['value']:.4f}%（{p if p is not None else '-'}% 分位）→ 区间内", "info"))
    sp = {x["name"]: x for x in S.govt_spreads}
    if "10Y-2Y" in sp:
        x = sp["10Y-2Y"]
        p = x.get("pct")
        txt = f"期限利差 10Y-2Y {x['value_bp']:.1f}bp" + (f"（{p:.1f}% 分位）" if p is not None else "")
        if p is not None and p <= TH["curve_flat"]:
            V.append(Verdict("第①层", txt + " → **曲线极平，长端抢券特征**", "orange"))
        else:
            V.append(Verdict("第①层", txt, "info"))

    # ② 信用利差
    if S.credit:
        cr = {x["grade"]: x for x in S.credit}
        lows = [k for k in cr if cr[k]["pct"] is not None and cr[k]["pct"] <= TH["credit_extreme"]]
        txt = "、".join(f"{k} {cr[k]['spread_bp']:.0f}bp" for k in cr)
        if len(lows) >= TH["credit_extreme_cnt"]:
            V.append(Verdict("第②层", f"信用利差 3Y：{txt} → **{'/'.join(lows)} 同步压到极值，"
                              "评级分层消失（资产荒特征）**", "red",
                              "不是信用变好，是所有能买的都被买平了"))
        else:
            V.append(Verdict("第②层", f"信用利差 3Y：{txt}", "info"))
        if "AAA" in cr and "AA" in cr:
            gap = cr["AA"]["spread_bp"] - cr["AAA"]["spread_bp"]
            if gap < TH["credit_no_tier_bp"]:
                V.append(Verdict("第②层", f"AA 与 AAA 仅差 {gap:.0f}bp → **短端几乎不分层**", "orange"))
            else:
                V.append(Verdict("第②层", f"AA 比 AAA 高 {gap:.0f}bp → 分层仍在", "green"))

    # ③ 同业存单
    n = S.ncd_spread or {}
    if n.get("value_bp") is not None:
        p = n.get("pct")
        txt = f"同业存单 1Y−国债 1Y {n['value_bp']:.0f}bp" + (f"（{p:.1f}% 分位）" if p is not None else "")
        if p is not None and p <= TH["ncd_low"]:
            V.append(Verdict("第③层", txt + " → 银行负债成本被压到极低", "green"))
        elif p is not None and p >= TH["ncd_high"]:
            V.append(Verdict("第③层", txt + " → **银行负债端出问题**", "red",
                              "资产端收益率与负债端成本两头挤，息差压缩"))
        else:
            V.append(Verdict("第③层", txt + " → 负债成本仍偏高（NCD 绝对水平黏着）", "orange"))

    # ④ 资金结构
    f = S.funding or {}
    if "隔夜回购占比" in f:
        x = f["隔夜回购占比"]
        p = x.get("pct")
        txt = f"隔夜回购占比 {x['value']:.1f}%" + (f"（{p:.1f}% 分位）" if p is not None else "")
        if p is not None and p >= TH["on_high"]:
            V.append(Verdict("第④层", txt + " → **靠隔夜滚杠杆，去杠杆风险最高**", "red",
                              "高杠杆 + 低成本 = 极致 carry，扰动会直接砸长端"))
        else:
            V.append(Verdict("第④层", txt, "info"))
    if "R007-DR007" in f:
        x = f["R007-DR007"]
        p = x.get("pct")
        txt = f"非银分层 R007−DR007 {x['value']:.1f}bp" + (f"（{p:.1f}% 分位）" if p is not None else "")
        if p is not None and p >= TH["r_dr_high"]:
            V.append(Verdict("第④层", txt + " → **非银借钱变贵，去杠杆前兆**", "red"))
        else:
            V.append(Verdict("第④层", txt + " → 分层温和", "green"))

    # 货币政策工具（辅助判据，不做阈值判定，只陈述事实）
    m = S.macro or {}
    if "存款准备金率" in m:
        rr = m["存款准备金率"]
        V.append(Verdict("货币政策", f"存款准备金率 {rr['value']:.2f}%（{rr['date']}，大型金融机构）"
                          " → 最近一次调整后的水平", "info",
                          "事件型数据：只在降准/升准生效日更新，不是每日重算"))
    if "买断式逆回购余额" in m:
        ob = m["买断式逆回购余额"]
        V.append(Verdict("货币政策", f"买断式逆回购余额 {ob['value']:.0f}亿元（{ob['date']}）"
                          " → 中长期流动性投放存量", "info",
                          "用公告的真实到期日推算；MLF 已基本退役，这是现在的主力中长期工具"))

    # ⑥ 对政策利率利差（2026-11-02 新框架：政策利率 = 隔夜逆回购操作利率）
    # 这一层不属于原来四层，层级标签用「政策利率」，以免和 ①②③④ 混淆。
    ps = {x.get("name"): x for x in (S.policy_spreads or [])}
    _n_obs = max([int(x.get("n_obs") or 0) for x in (S.policy_spreads or [])] or [0])
    # 样本不足时结论照出，但**必须剧透样本量**——10 个点的「90% 分位」等于「倒数第一」，
    # 不说清楚就会被当成一年分位读（这是旧四层早就踩过的坑）。
    _thin = ("（⚠️样本仅 %d 个点，分位请当方向读）" % _n_obs) if 0 < _n_obs < 30 else ""
    if ps or S.policy_rate is not None:
        if S.policy_rate is not None:
            head = f"政策利率 {S.policy_rate:.2f}%"
            if S.policy_rate_date:
                head += f"（{S.policy_rate_date}）"
        else:
            head = "政策利率缺失（公告未解析到利率）"
        V.append(Verdict("政策利率", head + " ← 隔夜逆回购操作利率", "info",
                          "2026-11-02 新框架：每个工作日固定利率、数量招标；"
                          "7 天期已改为利率招标无单一利率，不能当政策利率"))
    x = ps.get("10Y-政策利率")
    if x and x.get("value_bp") is not None:
        p = x.get("pct")
        txt = (f"10Y 国债−政策利率 {x['value_bp']:.1f}bp"
               + (f"（{p:.1f}% 分位）" if p is not None else ""))
        if p is not None and p <= TH["policy_narrow"]:
            V.append(Verdict("政策利率", txt + " → **长端对政策利率几乎无溢价，透支极致**" + _thin,
                              "red", "缓冲垫为零：政策利率一旦不动/回升，长端先被砸"))
        elif p is not None and p >= TH["policy_wide"]:
            V.append(Verdict("政策利率", txt + " → 对政策利率溢价厚，缓冲足" + _thin, "green",
                              "长端还有安全垫，不必因为绝对点位低就恐慌"))
        else:
            V.append(Verdict("政策利率", txt + " → 区间内" + _thin, "info"))
    x = ps.get("DR007-政策利率")
    if x and x.get("value_bp") is not None:
        p = x.get("pct")
        txt = (f"DR007−政策利率 {x['value_bp']:.1f}bp"
               + (f"（{p:.1f}% 分位）" if p is not None else ""))
        if p is not None and p >= TH["dr_policy_high"]:
            V.append(Verdict("政策利率", txt + " → **资金价格明显高于政策利率，资金面偏紧**" + _thin,
                              "orange", "DR007 在政策利率上方持续偏高 = 银行间边际紧"))
        else:
            V.append(Verdict("政策利率", txt + " → 资金价格贴近政策利率" + _thin, "info"))
    x = ps.get("NCD1Y-政策利率")
    if x and x.get("value_bp") is not None:
        # 只陈述：NCD 相对政策利率的合理溢价本身就随负债恐慌/宽松大幅漂移，
        # 没有稳定的阈值锚，硬设线反而会频繁误报，所以这里不做阈值判定。
        p = x.get("pct")
        V.append(Verdict("政策利率",
                          f"NCD1Y−政策利率 {x['value_bp']:.1f}bp"
                          + (f"（{p:.1f}% 分位）" if p is not None else ""), "info"))

    # 中债综合指数（债基/理财基准）
    c = S.cbond_index
    if c and c.get("gap_to_ma20_pct") is not None:
        gap = c["gap_to_ma20_pct"]
        lvl = "red" if gap <= -0.5 else ("orange" if gap < 0 else "green")
        V.append(Verdict("债基基准", f"中债新综合 {c['value']}（MA20 {c['ma20']}，"
                          f"偏离 {gap:+.3f}%）→ " +
                          ("**跌破 MA20，债基净值开始回撤**" if gap <= -0.5
                           else ("MA20 下方，观察" if gap < 0 else "MA20 上方，趋势健康")), lvl))
    return V


def _notes(S: BondState) -> List[str]:
    """固定的口径说明（每次结论都要带上，防止被误读成一年分位）。"""
    n = ["分位样本：中债曲线为「建库以来」（源站仅回溯约 3 个月，不是一年），"
         "宏观/美国利率信用为多年历史。"]
    missing = [x["tenor"] for x in S.govt_curve if not x.get("pct")]
    if missing:
        n.append(f"国债 {','.join(missing)} 分位样本不足，未给判定。")
    if not S.credit:
        n.append("信用利差数据缺失，第②层未判定。")
    if not S.ncd_spread.get("value_bp"):
        n.append("NCD 利差数据缺失，第③层未判定。")
    if S.policy_spreads:
        n_obs = max((x.get("n_obs") or 0) for x in S.policy_spreads)
        n.append(f"对政策利率利差（第⑥段）的减项是隔夜逆回购操作利率，该序列只在有公告的"
                 f"操作日有观测点、不 forward-fill；且新框架 2026-11-02 才启用，"
                 f"当前派生样本只有 {n_obs} 个观测，分位几乎等于「这几天的相对位置」，"
                 "方向可读、绝对分位很敏感——结论请配合绝对 bp 一起看，随历史累积才有统计意义。")
    else:
        n.append("对政策利率利差数据缺失（LIQ.CN.OMO.POLICY 或派生序列未就绪），第⑥段未判定。")
    return n


def summary_line(S: BondState) -> str:
    """一句话总判定（给推送标题用）。"""
    levels = [v.level for v in S.verdicts]
    red = levels.count("red")
    orange = levels.count("orange")
    if red >= 2:
        return f"🔴 {red} 项红色预警"
    if red == 1:
        return f"🟠 1 项红色 + {orange} 项橙色"
    if orange >= 2:
        return f"🟡 {orange} 项橙色"
    return "🟢 无极端读数"
