#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""横截面多空对冲·模拟盘 —— 飞书仓单卡片生成与推送
==============================================
复用 feishu_pusher 的卡片构建工具与发送器，按用户既定偏好（
标题开头说明报告主题、署名 DoMate 置于标题末尾、卡片列格式、
方向显式文字提示、推送状态过滤）构建每日开仓/平仓仓单卡片。
"""
import datetime
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from feishu_pusher import (
    make_header, make_text_element, make_column_set, make_divider, make_note,
    send_feishu_card,
)
from sim_trade.config import UNIT_CAPITAL, TOTAL_CAPITAL, SECTOR_OF, CONTRACT_MULTIPLIER, vname


def _dir_str(d):
    return "做多" if d == 1 else ("做空" if d == -1 else "—")


def _fmt_order_items(orders, prices):
    """开/减/平仓单 → 文本行列表（含品种·板块·方向·手数·价位·名义·原因）"""
    lines = []
    for otype, label in (("open", "◆ 开仓"), ("reduce", "▼ 减仓"),
                         ("close", "✕ 平仓"), ("hold", "○ 维持")):
        for od in orders.get(otype, []):
            v = od.get("variety") or "—"
            if not v or v not in SECTOR_OF:
                continue
            lots = od.get("_reduced_lots", od.get("lots", 0))
            price = od.get("price") or od.get("entry_price") or 0
            notional = lots * price * CONTRACT_MULTIPLIER.get(v, 1)
            sec = SECTOR_OF[v]
            reason = od.get("reason") or "—"
            lines.append(f"{label}｜[{sec}] {vname(v)} {_dir_str(od.get('dir'))} {lots}手 "
                         f"@{price:,.0f} 名义{notional:,.0f}｜{reason}")
    if not lines:
        lines.append("— 今日无交易动作 —")
    return lines


def build_sim_card(res, prices_md=None, oi_res=None, crowd=None, risk_res=None):
    """构建模拟盘仓单卡片。res 为 run_daily 返回值；oi_res 为持仓量单元返回值；
    crowd 为因子拥挤度红绿灯（前置展示）；risk_res 为带风控对照单元返回值。"""
    d = res["date"]
    acc = res["acc"]
    date_display = str(d.date())
    snap = res["snap"]
    _crowd_red = bool(crowd and crowd.get("red"))

    elements = []
    elements.append(make_text_element(
        f"**商品期货横截面多空对冲·模拟盘交易仓单** ｜ {date_display} ｜ **DoMate**"
        + (f"\n⚠️ **拥挤**（红灯：{'／'.join(crowd['red'])}）" if _crowd_red else "")
    ))
    elements.append(make_divider())

    # 〇 因子拥挤度红绿灯（前置，红灯=拥挤 / 绿灯=不拥挤）
    if crowd is not None:
        from sim_trade.crowding import format_lines as _cfl
        elements.append(make_text_element("**因子拥挤度红绿灯**\n" + "\n".join(_cfl(crowd))))
        elements.append(make_text_element(
            f"**结论**：绿灯 {len(crowd['green'])}／黄灯 {len(crowd['yellow'])}／红灯 {len(crowd['red'])} ｜ "
            + ("**允许开新仓**" if crowd["allow"] else "**拥挤/失效期，不建议开新仓**")
            + f"（评估基准日 {crowd.get('eval_date', crowd['date'])}）"))
        if crowd["allow"]:
            from sim_trade.engine import compute_target_positions as _ctp
            tg = _ctp(snap, UNIT_CAPITAL)
            dirs = {}
            for v2, t2 in tg.items():
                dirs.setdefault(SECTOR_OF[v2], {"多": [], "空": []})
                dirs[SECTOR_OF[v2]]["多" if t2["dir"] > 0 else "空"].append(v2)
            if dirs:
                elements.append(make_text_element("**不拥挤，可做方向**：" + "；".join(
                    f"{sec} 多 {'/'.join(vname(x) for x in x['多']) or '—'}／空 {'/'.join(vname(x) for x in x['空']) or '—'}"
                    for sec, x in dirs.items())))
            else:
                elements.append(make_text_element("**不拥挤**，但本期无合格腿（板块分化度不足）→ 建议空仓等待"))
        else:
            elements.append(make_text_element("**拥挤/失效期**：不再提示新增方向，建议不开新仓（仅保留既有风控平/减仓）"))
        elements.append(make_divider())

    # 一、账户概况
    elements.append(make_column_set([
        (f"总净值\n**{acc['total_nav']:,.0f}**", 'weighted', 1),
        (f"当日盈亏\n**{res['pnl_day']:+,.0f}**", 'weighted', 1),
        (f"累计盈亏\n**{acc['total_nav'] - TOTAL_CAPITAL:+,.0f}**", 'weighted', 1),
    ]))

    # 二、板块分化度
    s = {}
    for sec in ("化工", "农产品"):
        g = snap[snap["sector"] == sec].dropna(subset=["score"])
        s[sec] = round(float(g["score"].std(ddof=1)), 2) if len(g) >= 4 else None
    chem_flag = "✅有机会" if (s["化工"] is not None and s["化工"] >= 0.8) else "⚠️分化不足"
    agri_flag = "✅有机会" if (s["农产品"] is not None and s["农产品"] >= 0.8) else "⚠️分化不足"
    elements.append(make_text_element(
        f"**板块分化度**（score σ，门槛 0.8）：化工 {s['化工']} {chem_flag} ｜ "
        f"农产品 {s['农产品']} {agri_flag}"
    ))

    # 三、今日仓单
    elements.append(make_divider())
    elements.append(make_text_element("**今日仓单**"))
    lines = _fmt_order_items(res["orders"], None)
    elements.append(make_text_element("\n".join(lines)))

    # 四、当前持仓
    elements.append(make_divider())
    elements.append(make_text_element("**当前持仓**"))
    if not acc["positions"]:
        elements.append(make_text_element("— 空仓 —"))
    else:
        pos_lines = []
        for v, pos in sorted(acc["positions"].items()):
            nv = pos["lots"] * pos["entry_price"] * CONTRACT_MULTIPLIER[v]
            pct = nv / acc["total_nav"] * 100
            pos_lines.append(f"[{SECTOR_OF[v]}] {vname(v)} {_dir_str(pos['dir'])} {pos['lots']}手 "
                             f"@ {pos['entry_price']:,.0f}｜占比 {pct:.1f}%")
        elements.append(make_text_element("\n".join(pos_lines)))

    # 五、风险提示（简版）
    elements.append(make_divider())
    elements.append(make_text_element("**风险提示**"))
    risk_lines = []
    for v, pos in acc["positions"].items():
        if pos.get("entry_roll", 0) > 0:
            risk_lines.append(f"⚠️ {vname(v)} 开仓时 back 结构，若转 contango 将强制降至 30% 仓位")
    if not risk_lines:
        risk_lines.append("持仓品种暂无明显结构风险；关注各品种 score 日度反转")
    elements.append(make_text_element("\n".join(risk_lines)))

    elements.append(make_note(
        "10万/板块单元·单元合计20万｜保证金≥10%｜ATR20倒数加权｜多空合计杠杆≤1.5倍｜"
        "双周调仓｜与套利单元(20万)合并推送｜仅供参考，不构成投资建议"
    ))

    # 六、持仓量横截面对冲栏目（独立单元，可空）
    if oi_res is not None:
        oa = oi_res["acc"]
        elements.append(make_divider())
        elements.append(make_text_element(
            f"**持仓量横截面对冲**（OI_ROC40 板块内 z 权重 · 周频调仓）｜ "
            f"净值 **{oa['total_nav']:,.0f}** ｜ 当日盈亏 **{oi_res['pnl_day']:+,.0f}**"
        ))
        oi_lines = []
        for otype, label in (("open", "◆ 开仓"), ("reduce", "▼ 减仓"),
                             ("close", "✕ 平仓"), ("hold", "○ 维持")):
            for od in oi_res["orders"].get(otype, []):
                v = od.get("variety") or "—"
                if not v or v not in SECTOR_OF:
                    continue
                lots = od.get("_reduced_lots", od.get("lots", 0))
                price = od.get("price") or od.get("entry_price") or 0
                notional = lots * price * CONTRACT_MULTIPLIER.get(v, 1)
                oi_lines.append(f"{label}｜[{SECTOR_OF[v]}] {vname(v)} {_dir_str(od.get('dir'))} {lots}手 "
                                f"@{price:,.0f} 名义{notional:,.0f}｜{od.get('reason') or '—'}")
        if not oi_lines:
            oi_lines.append("— 今日无交易动作 —")
        elements.append(make_text_element("\n".join(oi_lines[:8])))
        if not oa["positions"]:
            elements.append(make_text_element("— 持仓量单元空仓 —"))
        else:
            pos_lines = []
            for v, pos in sorted(oa["positions"].items(), key=lambda kv: -abs(kv[1]["weight"]))[:10]:
                nv = pos["lots"] * pos["entry_price"] * CONTRACT_MULTIPLIER[v]
                pos_lines.append(f"[{SECTOR_OF[v]}] {vname(v)} {_dir_str(pos['dir'])} {pos['lots']}手 "
                                 f"@ {pos['entry_price']:,.0f}｜名义 {nv:,.0f}")
            elements.append(make_text_element("\n".join(pos_lines)))
        elements.append(make_note(
            "持仓量单元独立核算：100万/板块×2=200万｜权重=z/Σ|z|（多正空负）｜周频调仓｜仅供参考，不构成投资建议"
        ))

    # 七、带风控横截面对冲（对照单元）
    if risk_res is not None:
        rr = risk_res
        sw = rr.get("switches", {}) or {}
        elements.append(make_divider())
        elements.append(make_text_element(
            f"**带风控横截面对冲（对照单元）**｜ 净值 **{rr['nav']:,.0f}** ｜ 当日盈亏 "
            f"**{rr.get('pnl_yuan', 0):+,.0f} 元** ｜ 自 {rr.get('start_date', '—')} 起累计 **{rr['cum']:+,.0f}**"))
        elements.append(make_text_element(
            f"拥挤度档位 **{sw.get('拥挤度档位', '—')}**（绿100%／黄50%／红25%）｜ "
            f"总敞口 **{rr['gross']:.2f}** 倍（上限 2.0）\n"
            + "；".join(f"{k} {sw[k]}" for k in ("组合波动率目标", "分数Kelly", "相关性降杠杆",
                                                "回撤规则", "拥挤度缩放") if k in sw)))
        ol = []
        from sim_trade.report import dir_action
        for otype, label in (("open", "◆ 开仓"), ("close", "✕ 平仓"), ("adjust", "↻ 调整")):
            for od in rr["orders"].get(otype, []):
                ol.append(f"{label}｜**{dir_action(od)}**｜[{od.get('sector', '—')}] {vname(od['variety'])} "
                          f"{od['from']:+.3f}→{od['to']:+.3f}　名义{od.get('notional', 0):,.0f}")
        if not ol:
            ol = ["— 今日无交易动作 —"]
        _pos = rr.get("positions", {}) or {}
        _lg = [k for k, v in _pos.items() if v > 0]
        _sh = [k for k, v in _pos.items() if v < 0]
        ol.insert(0, f"**多空结构**：做多 {len(_lg)} 腿（{'、'.join(vname(x) for x in _lg) or '—'}）｜做空 {len(_sh)} 腿"
                     f"（{'、'.join(vname(x) for x in _sh) or '—'}）｜多腿合计 {sum(v for v in _pos.values() if v > 0):.3f}、"
                     f"空腿合计 {-sum(v for v in _pos.values() if v < 0):.3f}")
        elements.append(make_text_element("\n".join(ol[:10])))
        elements.append(make_note(
            "对照单元：与既有单元同信号、各自 20 万起算（本单元自首次运行日起累计），差异只来自拥挤度开关与四层风控｜"
            "名义敞口口径，不含手数与保证金账务｜仅供参考，不构成投资建议"))

    card = {
        'config': {'wide_screen_mode': True},
        'header': make_header(f"横截面多空对冲模拟盘仓单 ｜ {date_display} ｜ DoMate"
                              + (" ｜ ⚠️拥挤" if _crowd_red else ""),
                              'red' if _crowd_red else 'blue'),
        'elements': elements,
    }
    return card


if __name__ == "__main__":
    # 试跑：用最近因子日构建卡片并推送
    import pandas as pd
    from sim_trade.engine import run_daily
    from sim_trade.config import FACTORS_FILE
    factors = pd.read_csv(FACTORS_FILE, parse_dates=["date"])
    last = factors["date"].max()
    res = run_daily(last, factors, force_rebalance=("--reb" in sys.argv), reset=("--reset" in sys.argv))
    card = build_sim_card(res)
    ok = send_feishu_card(card)
    print("推送结果:", ok)
    # 落盘卡片 JSON 供复核
    out = Path(__file__).parent / "output" / f"feishu_card_{last.date()}.json"
    out.write_text(str(card) if isinstance(card, str) else __import__("json").dumps(card, ensure_ascii=False, indent=1),
                   encoding="utf-8")