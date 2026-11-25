# -*- coding: utf-8 -*-
"""每日仓单与盈亏日报生成
==========================
输出（本地落盘 + 返回卡片内容）：
  1) 各品种因子明细表
  2) 多空推荐组合（含品种、方向、score、建议仓位比例）
  3) 板块分化度评估（score 标准差）
  4) 风险提示（信号矛盾、因子失效）
  5) 换仓建议（维持/微调/重建）
  6) 当日仓单：开仓/平仓/减仓/维持 + 当日盈亏 + 账户净值
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from sim_trade.config import (
    CHEM, SECTORS, UNIT_CAPITAL, TOTAL_CAPITAL, MARGIN_RATE, CONTRACT_MULTIPLIER,
    SIGMA_GATE, K, MAX_N, LAM, OUT_DIR, STATE_DIR, SECTOR_OF, NAME_OF, vname,
)


def sector_stats(snap: pd.DataFrame) -> dict:
    """板块内 score 截面标准差（分化度）"""
    out = {}
    for sec in SECTORS:
        g = snap[snap["sector"] == sec].dropna(subset=["score"])
        if len(g) >= 4:
            out[sec] = round(float(g["score"].std(ddof=1)), 3)
        else:
            out[sec] = None
    return out


def build_factor_table(snap: pd.DataFrame) -> pd.DataFrame:
    """因子明细表：库存分位z/展期z/基差z/结构斜率z/综合score/score_adj"""
    cols = ["variety", "sector", "score", "score_adj", "z_inv", "z_roll",
            "z_basis", "z_droll", "roll_annual", "basis_rate", "droll5", "atr20", "main_close"]
    t = snap[cols].copy()
    t = t.sort_values("score", ascending=False)
    for c in ["z_inv", "z_roll", "z_basis", "z_droll", "score", "score_adj"]:
        t[c] = t[c].round(3) if c in t else t[c]
    return t


def build_orders_report(orders: dict, prices: dict, mul: dict) -> str:
    lines = []
    for otype, label, sign in (("open", "开仓", 1), ("reduce", "减仓", -1),
                               ("close", "平仓", 0), ("hold", "维持", 0)):
        items = orders.get(otype, [])
        if not items:
            continue
        lines.append(f"\n### {label} ({len(items)} 条)")
        for od in items:
            v = od.get("variety") or "—"
            sec = SECTOR_OF.get(v, "—")
            direction = "多" if od.get("dir") == 1 else ("空" if od.get("dir") == -1 else "—")
            # 减仓记录用 _reduced_lots（本次减的净手数）
            lots = od.get("_reduced_lots", od.get("lots", 0))
            price = od.get("price")
            if price is None:
                price = od.get("entry_price")
            notional = lots * (price or 0) * mul.get(v, 1)
            note = f"{notional:,.0f} 元" if notional else "—"
            lines.append(
                f"- [{sec}] {vname(v)} {direction} {lots}手 @ {price} 名义{note}"
                f" ｜ 原因: {od.get('reason', '—')}")
    return "\n".join(lines)


def dir_action(od: dict) -> str:
    """把权重变动翻译成显式多空动作：开多/开空/平多/平空/加多/减多/加空/减空/反手为多/反手为空"""
    a, b = float(od.get("from", 0.0)), float(od.get("to", 0.0))
    if abs(a) < 1e-9 and abs(b) > 1e-9:
        return "开多" if b > 0 else "开空"
    if abs(b) < 1e-9 and abs(a) > 1e-9:
        return "平多" if a > 0 else "平空"
    if abs(a) > 1e-9 and abs(b) > 1e-9 and (a > 0) != (b > 0):
        return "反手为多" if b > 0 else "反手为空"
    side = "多" if b > 0 else "空"
    return ("加" if abs(b) > abs(a) else "减") + side


def build_risk_section(rr: dict) -> str:
    """带风控横截面对冲（对照单元）栏目正文"""
    sw = rr.get("switches", {}) or {}
    pos = rr.get("positions", {}) or {}
    longs = [k for k, v in pos.items() if v > 0]
    shorts = [k for k, v in pos.items() if v < 0]
    lot_long = sum(v for v in pos.values() if v > 0)
    lot_short = -sum(v for v in pos.values() if v < 0)
    lines = ["## 带风控横截面对冲（对照单元 · 名义敞口口径）",
             f"- 净值：**{rr['nav']:,.2f}** 元（本金 20 万，自 {rr.get('start_date', '—')} 起累计 {rr['cum']:+,.2f}）｜"
             f"当日盈亏 **{rr.get('pnl_yuan', 0):+,.2f} 元**（成本 {rr.get('cost', 0):.5f}）",
             f"- 总敞口：**{rr['gross']:.2f}** 倍（上限 2.0）｜拥挤度档位：**{sw.get('拥挤度档位', '—')}**"
             f"（绿=100%／黄=50%／红=25% 敞口）",
             f"- **多空结构**：做多 **{len(longs)} 腿**（{'、'.join(vname(x) for x in longs) or '—'}）｜做空 **{len(shorts)} 腿**"
             f"（{'、'.join(vname(x) for x in shorts) or '—'}）｜多腿合计 {lot_long:.3f}、空腿合计 {lot_short:.3f}、"
             f"净敞口 {lot_long - lot_short:+.3f}"]
    for k in ("单品种波动率缩放", "组合波动率目标", "分数Kelly", "相关性降杠杆",
              "回撤规则", "拥挤度缩放", "杠杆上限", "200日均线慢熊", "波动率快崩"):
        if k in sw:
            lines.append(f"- {k}：{sw[k]}")
    orders = rr.get("orders", {})
    for otype, label in (("open", "开仓"), ("close", "平仓"), ("adjust", "调整")):
        items = orders.get(otype, [])
        if not items:
            continue
        seg = {}
        for od in items:
            act = dir_action(od)
            seg[act] = seg.get(act, 0) + 1
        lines.append(f"### {label}（{len(items)} 条：" + "、".join(f"{k} {v} 笔" for k, v in seg.items()) + "）")
        for od in items:
            lines.append(f"- **{dir_action(od)}**｜[{od.get('sector', '—')}] {vname(od['variety'])}　"
                         f"权重 {od['from']:+.3f}→{od['to']:+.3f}　名义 {od.get('notional', 0):,.0f} 元")
    lines.append("\n_本单元与既有单元同一信号、各自 20 万起算（本单元自首次运行日起累计），差异只来自拥挤度开关与四层风控；"
                 "只做名义敞口权重与净值推进，不做手数取整与保证金账务。_")
    return "\n".join(lines)


def build_daily_report(res: dict, docs: dict = None, oi_res: dict = None,
                       crowd: dict = None, risk_res: dict = None) -> str:
    """生成日报全文（Markdown）。
    oi_res: 持仓量横截面对冲单元返回值；crowd: 因子拥挤度红绿灯；risk_res: 带风控对照单元返回值。"""
    d = res["date"]
    acc = res["acc"]
    snap = res["snap"].reset_index(drop=True)
    orders = res["orders"]
    prices = {r["variety"]: r["main_close"] for _, r in snap.iterrows()}
    stats = sector_stats(snap)
    table = build_factor_table(snap)

    lines = []
    title = "# 商品期货横截面多空对冲 · 模拟盘交易仓单"
    if crowd is not None and crowd.get("red"):
        title += f"　⚠️ 拥挤（红灯：{'／'.join(crowd['red'])}）"
    lines.append(title)
    lines.append(f"## 报告期 {d.date()} ｜ 板块内部对冲 ｜ 来自 DoMate\n")

    # 〇 因子拥挤度红绿灯（前置：红灯=拥挤，绿灯=不拥挤）
    if crowd is not None:
        lines.append("## 〇、因子拥挤度红绿灯（前置）")
        from sim_trade.crowding import format_lines as _cfl
        for _ln in _cfl(crowd):
            lines.append(f"- {_ln}")
        lines.append(f"- 结论：绿灯 {len(crowd['green'])}／黄灯 {len(crowd['yellow'])}／红灯 {len(crowd['red'])}"
                     f"｜{'允许开新仓' if crowd['allow'] else '拥挤/失效期，不建议开新仓'}"
                     f"（评估基准日 {crowd.get('eval_date', crowd['date'])}）")
        if crowd["allow"]:
            from sim_trade.engine import compute_target_positions as _ctp
            tg = _ctp(snap, UNIT_CAPITAL)
            dirs = {}
            for v2, t2 in tg.items():
                dirs.setdefault(SECTOR_OF[v2], {"多": [], "空": []})
                dirs[SECTOR_OF[v2]]["多" if t2["dir"] > 0 else "空"].append(v2)
            if dirs:
                lines.append("- 不拥挤，可做方向：" + "；".join(
                    f"{sec} 多 {'/'.join(vname(x) for x in d2['多']) or '—'}／空 {'/'.join(vname(x) for x in d2['空']) or '—'}"
                    for sec, d2 in dirs.items()))
            else:
                lines.append("- 不拥挤，但本期无合格腿（板块分化度不足）→ 建议空仓等待")
        else:
            lines.append("- 拥挤/失效期：不再提示新增方向，建议不开新仓（仅保留既有风控平/减仓）")
        lines.append("")
    lines.append(f"> 单元账户：化工 {UNIT_CAPITAL/1e4:.1f}万 + 农产品 {UNIT_CAPITAL/1e4:.1f}万 = 单元合计 "
                 f"**{acc['total_nav']:,.2f}** 元（与套利单元 20 万并列）｜ 当日盈亏 "
                 f"**{res['pnl_day']:+,.2f}** 元\n")

    # 1 因子明细
    lines.append("## 一、各品种因子明细表（score_adj = score − 0.6×momz20）")
    t = table.round(3).copy()
    for c in ["z_inv", "z_roll", "z_basis", "z_droll", "score", "score_adj"]:
        if c in t.columns:
            t[c] = t[c].map(lambda x: f"{x:+.2f}" if pd.notna(x) else "—")
    # 原始值列：库存分位(以z表示)、展期年化、基差率、结构斜率
    for c, fmt in (("roll_annual", "{:+.1%}"), ("basis_rate", "{:+.2%}"), ("droll5", "{:+.3f}")):
        if c in t.columns:
            t[c] = t[c].map(lambda x: fmt.format(x) if pd.notna(x) else "—")
    t = t.rename(columns={"z_inv": "库存z(低=紧)", "z_roll": "展期z", "z_basis": "基差z",
                          "z_droll": "斜率z", "roll_annual": "展期年化",
                          "basis_rate": "基差率", "droll5": "5日斜率"})
    lines.append("| 品种 | 板块 | 库存z(低=紧) | 展期年化 | 展期z | 基差率 | 基差z | 5日斜率 | 斜率z | score | score_adj |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for _, r in t.iterrows():
        lines.append(f"| {vname(r['variety'])} | {r['sector']} | {r['库存z(低=紧)']} | "
                     f"{r['展期年化']} | {r['展期z']} | {r['基差率']} | {r['基差z']} | "
                     f"{r['5日斜率']} | {r['斜率z']} | {r['score']} | {r['score_adj']} |")

    # 2 多空推荐
    lines.append("\n## 二、多空推荐组合（机会驱动：σ≥%.1f、k=%.1f、n≤%d）" % (SIGMA_GATE, K, MAX_N))
    from sim_trade.engine import compute_target_positions
    target = compute_target_positions(snap, UNIT_CAPITAL)
    if not target:
        lines.append("_本扫描期无交易机会（板块分化度不足或没有合格腿）_")
    else:
        lines.append("| 品种 | 板块 | 方向 | 建议仓位(%单元净值) | 目标名义(元) | score_adj |")
        lines.append("|---|---|---|---|---|---|")
        for v, t_ in sorted(target.items(), key=lambda kv: -kv[1]["score_adj"] * kv[1]["dir"]):
            pct = t_["weight"] * 100
            notional = t_["weight"] * UNIT_CAPITAL
            lines.append(f"| {vname(v)} | {SECTOR_OF[v]} | {'多' if t_['dir'] > 0 else '空'} | "
                         f"{pct:.1f}% | {notional:,.0f} | {t_['score_adj']:+.2f} |")

    # 3 分化度
    lines.append("\n## 三、板块分化度评估（score 截面标准差）")
    for sec in SECTORS:
        s = stats.get(sec)
        if s is None:
            lines.append(f"- {sec}：数据不足，无法评估")
        else:
            flag = "✅ 有足够分化，可对冲" if s >= SIGMA_GATE else "⚠️ 分化不足，无交易机会"
            lines.append(f"- {sec}：σ = **{s:.3f}**（门槛 {SIGMA_GATE}）{flag}")

    # 4 风险与换仓
    lines.append("\n## 四、风险提示与换仓建议")
    risk_notes = []
    # 信号矛盾：score 与展期方向不一致（因子冲突）
    for _, r in table.iterrows():
        if pd.notna(r["score"]) and pd.notna(r["z_roll"]):
            if abs(r["score"]) > 1.2 and (r["score"] * r["z_roll"]) < 0:
                risk_notes.append(f"{vname(r['variety'])}：score {r['score']:+.2f} 与展期 {r['z_roll']:+.2f} 方向矛盾")
            if pd.notna(r["z_inv"]) and abs(r["score"]) > 1.2 and (r["score"] * r["z_inv"]) < 0:
                risk_notes.append(f"{vname(r['variety'])}：score {r['score']:+.2f} 与库存 {r['z_inv']:+.2f} 方向矛盾")
    if risk_notes:
        lines.append("信号矛盾品种：")
        for n in risk_notes[:6]:
            lines.append(f"- {n}")
    else:
        lines.append("- 本期未见显著信号矛盾")
    # 结构翻转预警
    flip = [
        (r["variety"], r["z_roll"])
        for _, r in table.iterrows()
        if pd.notna(r["z_roll"]) and r["z_roll"] < -1.5
    ]
    if flip:
        lines.append("深contango（展期逆风，做空逻辑占优）：" + ", ".join(f"{vname(v)}(z={z:+.2f})" for v, z in flip))
    # 单边余腿：板块持仓只剩单侧（风控平仓后的残余，非主动开仓）
    sides = {}
    for v, pos in acc["positions"].items():
        sides.setdefault(SECTOR_OF[v], [0, 0])
        sides[SECTOR_OF[v]][0 if pos["dir"] > 0 else 1] += pos["lots"]
    one_sided = [sec for sec, (l, s) in sides.items() if (l > 0) != (s > 0)]
    for sec in one_sided:
        lines.append(f"- ⚠️ {sec}板块当前为**单边余腿**（风控平仓后残余）：非主动裸单边，将在下个双周调仓日收敛或重建")
    # 换仓建议
    if res["is_first"]:
        advice = "首日建仓：按推荐组合开仓"
    elif res["is_reb"]:
        advice = "双周调仓日：按最新组合重建/微调仓位"
    else:
        advice = "常规交易日：维持既有仓位，执行风控（score反转/结构翻转）"
    lines.append(f"- 换仓建议：**{advice}**")

    # 5 仓单与盈亏
    lines.append("\n## 五、当日仓单")
    maps = {"variety": "品种", "dir": "方向", "lots": "手数", "price": "价位",
            "weight": "仓位比例", "score_adj": "开仓score", "reason": "原因"}
    lines.append(build_orders_report(orders, prices, CONTRACT_MULTIPLIER))
    lines.append(f"\n### 当日盈亏 {res['pnl_day']:+,.2f} 元（含成本 {res['cost']:,.2f}）")

    # 持仓明细
    lines.append("\n### 当前持仓")
    if not acc["positions"]:
        lines.append("_空仓_")
    else:
        lines.append("| 品种 | 板块 | 方向 | 手数 | 开仓价 | 持仓净值占比 |")
        lines.append("|---|---|---|---|---|---|")
        for v, pos in sorted(acc["positions"].items()):
            notional = pos["lots"] * pos["entry_price"] * CONTRACT_MULTIPLIER[v]
            lines.append(f"| {vname(v)} | {SECTOR_OF[v]} | {'多' if pos['dir'] > 0 else '空'} | {pos['lots']} | "
                         f"{pos['entry_price']} | {notional / acc['total_nav'] * 100:.1f}% |")

    # 6 账户
    lines.append("\n## 六、账户净值")
    lines.append(f"- 化工单元：**{acc['nav']['化工']:,.2f}** 元（盈亏 {acc['nav']['化工'] - UNIT_CAPITAL:+,.2f}）")
    lines.append(f"- 农产品单元：**{acc['nav']['农产品']:,.2f}** 元（盈亏 {acc['nav']['农产品'] - UNIT_CAPITAL:+,.2f}）")
    lines.append(f"- 总净值：**{acc['total_nav']:,.2f}** 元（累计 {acc['total_nav'] - TOTAL_CAPITAL:+,.2f}）")
    lines.append(f"- 保证金占用估算：名义×{MARGIN_RATE:.0%}，总杠杆 = 多空名义合计/净值 ≤ 1.5 倍")

    text = "\n".join(lines)
    if risk_res is not None:
        text += "\n\n---\n\n" + build_risk_section(risk_res)
    if oi_res is not None:
        from sim_trade.oi_hedge import build_oi_report_section
        text += "\n\n---\n\n" + build_oi_report_section(oi_res)
    (OUT_DIR / f"daily_report_{d.date()}.md").write_text(text, encoding="utf-8")
    # 仓单 CSV
    rows = []
    for otype in ("open", "reduce", "close", "hold"):
        for od in orders.get(otype, []):
            rows.append({"date": str(d.date()), "type": otype, "variety": od.get("variety"),
                         "variety_name": NAME_OF.get(od.get("variety"), od.get("variety")),
                         "dir": "多" if od.get("dir") == 1 else ("空" if od.get("dir") == -1 else ""),
                         "lots": od.get("_reduced_lots", od.get("lots")),
                         "price": od.get("price") or od.get("entry_price"),
                         "reason": od.get("reason", "")})
    if rows:
        pd.DataFrame(rows).to_csv(OUT_DIR / f"orders_{d.date()}.csv", index=False, encoding="utf-8-sig")
    # 净值历史
    hist = pd.DataFrame(acc["history"])
    if not hist.empty:
        hist.to_csv(STATE_DIR / "nav_history.csv", index=False, encoding="utf-8-sig")
    # 持仓量单元：独立仓单 CSV + 净值历史（与横截面单元口径一致，独立落盘）
    if oi_res is not None:
        oi_orders = oi_res["orders"]
        oi_rows = []
        for otype in ("open", "reduce", "close", "hold"):
            for od in oi_orders.get(otype, []):
                oi_rows.append({"date": str(d.date()), "type": otype, "variety": od.get("variety"),
                                "variety_name": NAME_OF.get(od.get("variety"), od.get("variety")),
                                "dir": "多" if od.get("dir") == 1 else ("空" if od.get("dir") == -1 else ""),
                                "lots": od.get("_reduced_lots", od.get("lots")),
                                "price": od.get("price") or od.get("entry_price"),
                                "reason": od.get("reason", "")})
        if oi_rows:
            pd.DataFrame(oi_rows).to_csv(OUT_DIR / f"orders_oi_{d.date()}.csv", index=False, encoding="utf-8-sig")
        oi_hist = pd.DataFrame(oi_res["acc"]["history"])
        if not oi_hist.empty:
            oi_hist.to_csv(STATE_DIR / "nav_history_oi.csv", index=False, encoding="utf-8-sig")
    return text


if __name__ == "__main__":
    import sys
    from sim_trade.engine import load_account, run_daily
    import pandas as pd
    from sim_trade.config import FACTORS_FILE
    factors = pd.read_csv(FACTORS_FILE, parse_dates=["date"])
    d = pd.Timestamp(sys.argv[1]) if len(sys.argv) > 1 else factors["date"].max()
    res = run_daily(d, factors, force_rebalance=("--reb" in sys.argv), reset=("--reset" in sys.argv))
    print(build_daily_report(res))