#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""商品期货横截面套利模拟盘 · 合并模拟盘入口 —— 横截面对冲单元（20 万）＋ 价差套利单元（20 万）
================================================================
替代原「商品期货价差回归·每日交易报告」定时任务入口，每晚 20:30 运行：
同一份报告覆盖两个策略单元，各自开/平仓仓单、当日盈亏、账户净值、
分化度/候选与风险提示；输出合并 Markdown + 合并 CSV + 一张飞书卡片。

流程：
  0) （可选 --no-fetch）横截面对冲行情增量抓取
  1) 重建 panel_full.csv + factors_full.csv（横截面对冲侧）
  2) 横截面对冲单元 run_daily（化工10万+农产品10万，幂等增量推进）
  3) 套利数据增量更新（新浪主连+近期活跃合约强抓，补齐到共同交易日）
  4) 套利单元 arb_sim_daily.run_daily（20 万账户化模拟，幂等增量推进）
  5) 生成 merged_report_{date}.md + merged_orders_{date}.csv + merged_nav_{date}.csv
  6) 推送一张飞书卡片（header「商品期货横截面套利模拟盘」）

用法：
  python3 run_merged_daily.py --no-push   # 不推送，先核对数字
  python3 run_merged_daily.py --send      # 完整链路 + 推送
  python3 run_merged_daily.py --reset     # 重置横截面对冲账户（套利用 --reset-arb）
"""
import argparse
import datetime
import json
import subprocess
import sys
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# 跨品种套利单元依赖外部仓库 commodity_spread_scanner。
# 原写死的 ses_gffe5f1ccd.../commodity_spread_scanner 在现环境下不存在，
# 之前是静默 sys.path 加一个空路径 → import 时才炸，报错信息还指不到根因。
# 改成：候选位置逐个探测，都没有就明确告警并跳过套利单元（横截面对冲不受影响）。
_ARB_CANDS = [
    os.environ.get("ARB_ROOT", ""),
    str(Path(__file__).resolve().parent / "commodity_spread_scanner"),
    str(Path(__file__).resolve().parent.parent.parent / "commodity_spread_scanner"),
]
ARB_ROOT = next((Path(p) for p in _ARB_CANDS
                 if p and (Path(p) / "commodity_spread_scanner").is_dir()
                 or p and Path(p).is_dir() and (Path(p) / "__init__.py").exists()
                 or p and Path(p).is_dir() and any(Path(p).glob("*.py"))), None)
if ARB_ROOT is not None:
    sys.path.insert(0, str(ARB_ROOT))
else:
    print("[warn] 未找到 commodity_spread_scanner，跨品种套利单元将跳过"
          "（横截面对冲不受影响）。需要时用 ARB_ROOT 环境变量指定。")


# --------------------------------------------------------------------------
# 1) 横截面对冲单元（复用 sim_trade 全链路）
# --------------------------------------------------------------------------
def run_cross_section(no_fetch: bool, reset: bool) -> dict:
    from data_update import incremental_fetch, apply_kline_merge

    if not no_fetch:
        print("==", "横截面对冲：增量抓取行情 ...")
        kline = incremental_fetch()
        apply_kline_merge(kline)

    t0 = time.time()
    print("==", "横截面对冲：重建 panel_full.csv ...")
    subprocess.run([sys.executable, "panel_build.py"], cwd=ROOT, check=True)
    print("==", "横截面对冲：重建 factors_full.csv ...")
    subprocess.run([sys.executable, "factors_build_2022.py"], cwd=ROOT, check=True)
    print(f"== 重建完成，耗时 {time.time()-t0:.0f}s")

    import pandas as pd
    from sim_trade.engine import run_daily
    from sim_trade.config import FACTORS_FILE

    factors = pd.read_csv(FACTORS_FILE, parse_dates=["date"])
    last = factors["date"].max()
    print("==", "横截面对冲最新共同交易日:", last.date())

    res = run_daily(last, factors, force_rebalance=reset, reset=reset)
    return {"res": res, "last": last, "factors": factors}


# --------------------------------------------------------------------------
# 2) 套利单元数据更新（强抓近期活跃合约，补齐到目标日）
# --------------------------------------------------------------------------
def update_arb_data(target_date: str) -> str:
    """增量更新套利分月合约缓存；返回套利侧最新共同交易日（str）。"""
    from scanner.data_update import update_all
    from scanner.pair_table import load_pair_table
    from scanner.load_data import load_pair_data

    print("==", "套利单元：增量更新分月合约缓存 ...")
    try:
        update_all(verbose=False)
    except Exception as e:
        print("[arb-update] update_all 异常:", repr(e)[:120])

    # 强抓近期活跃合约：以主连/合约缓存尾日 < 目标日为准，覆盖 17 对全部腿
    from scanner.data_update import PLAN, _fetch_contract, _cache_contract_file
    pairs = load_pair_table()
    legs = set()
    for p in pairs:
        legs.add(p.leg_a)
        legs.add(p.leg_b)
    now = datetime.date.today()
    cands = []
    for prod in sorted(legs):
        for shift in (0, 1, 2, 3):
            mm = now.month + shift
            y = now.year + (mm - 1) // 12
            m = (mm - 1) % 12 + 1
            if m in PLAN.get(prod, []):
                sym = f"{prod}{y % 100:02d}{m:02d}"
                if sym not in cands:
                    cands.append(sym)
    n_fetched = 0
    for sym in cands:
        f = _cache_contract_file(sym)
        tail = None
        if f.exists() and f.stat().st_size >= 100:
            for line in reversed(f.read_text(encoding="utf-8-sig").splitlines()[1:]):
                line = line.strip()
                if line:
                    tail = line.split(",")[0]
                    break
        if tail is None or tail < target_date:
            try:
                if _fetch_contract(sym, retries=1, wait=1.0):
                    n_fetched += 1
            except Exception:
                pass
            time.sleep(0.4)
    print(f"[arb-update] 强抓近期活跃合约 {n_fetched} 个（目标日 {target_date}）")

    # 套利侧共同最新日
    latest = []
    for p in pairs:
        try:
            d = load_pair_data(p.leg_a, p.leg_b, start="2026-09-20")
            latest.append(str(d.index.max().date()))
        except Exception:
            pass
    common = min(latest) if latest else target_date
    print("==", "套利单元共同最新交易日:", common)
    return common


# --------------------------------------------------------------------------
# 3) 套利单元报告段落（Markdown）
# --------------------------------------------------------------------------
_DIR_CN = {1: "多A空B", -1: "空A多B", 0: "—"}


def arb_markdown(res: dict) -> str:
    L = []
    nav = res["nav"]
    cap = res.get("total_pnl", nav - 200_000.0)
    L.append("## 单元二 · 价差套利（20 万本金·保证金率 10%）")
    L.append(f"> 账户净值 **{nav:,.2f}** 元 ｜ 当日盈亏 **{res['pnl_day']:+,.2f}** 元 ｜ "
             f"累计 **{cap:+,.2f}** 元（含成本 {res['cost_total']:,.0f}）\n")

    # 当日动作
    L.append("### 当日仓单")
    if res["orders"]["open"]:
        for o in res["orders"]["open"]:
            L.append(f"- **开仓** {o['pair']}({_DIR_CN[o['dir']]}) "
                     f"{o['sa']}/{o['sb']}手 腿价 {o['open_a']:,.0f}/{o['open_b']:,.0f} "
                     f"z开={o['z_open']:+.2f}（触发z={o['z_trigger'] and round(o['z_trigger'],2)}） "
                     f"保证金 {o['margin']:,.0f}")
    if res["orders"]["close"]:
        for c in res["orders"]["close"]:
            L.append(f"- **平仓** {c['pair']}({_DIR_CN[c['dir']]}) "
                     f"净盈亏 {c['net']:+,.0f}（毛 {c['gross']:+,.0f} - 费 {c['cost']:,.0f}）｜{c['reason']}")
    if not res["orders"]["open"] and not res["orders"]["close"]:
        L.append("- 今日无开平动作（持仓维持）")
    if res["pending"]:
        L.append("### 确认中候选（N=3 动量反转确认）")
        for pd_ in res["pending"]:
            L.append(f"- {pd_['pair']} {_DIR_CN[pd_['dir']]} 已确认 {pd_['streak']}/{pd_['need']} 日")
    if res["skipped"]:
        L.append("### 资金约束（保证金占用 > 净值，未开）")
        for sk in res["skipped"]:
            L.append(f"- {sk['pair']} 需保证金 {sk['margin_need']:,.0f}（已用 {sk['used']:,.0f}）")
    L.append("")

    # 当前持仓
    L.append("### 当前持仓")
    if not res["positions"]:
        L.append("_空仓_")
    else:
        L.append("| 配对 | 方向 | 手数(腿A/腿B) | 当前z | 触发z | 保证金 |")
        L.append("|---|---|---|---|---|---|")
        for pos in res["positions"]:
            zt = pos.get("z_trigger")
            L.append(f"| {pos['pair']} | {_DIR_CN[pos['dir']]} | {pos['sa']}/{pos['sb']} | "
                     f"{pos['z']:+} | {zt and round(zt,2)} | {pos['margin_used']:,.0f} |")
    L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------
# 4) 合并报告（Markdown + CSV）
# --------------------------------------------------------------------------
def build_merged_text(cross: dict, arb: dict) -> str:
    from sim_trade.report import build_daily_report
    cross_md = build_daily_report(cross["res"])   # 横截面对冲完整 Markdown
    d = cross["last"].date()
    L = []
    L.append(f"# 商品期货横截面套利模拟盘 · 每日仓单（横截面对冲 20 万 ＋ 价差套利 20 万）")
    L.append(f"## 报告期 {d} ｜ 来自 DoMate\n")
    # 合并账户概况
    cross_acc = cross["res"]["acc"]
    total = cross_acc["total_nav"] + arb["nav"]
    chem = cross_acc["nav"]["化工"] - 100_000.0
    agri = cross_acc["nav"]["农产品"] - 100_000.0
    arb_cap = arb["nav"] - 200_000.0
    L.append(f"> 合并净值 **{total:,.2f}** 元＝横截面对冲 {cross_acc['total_nav']:,.2f}"
             f"（化工 {chem:+,.0f} / 农产品 {agri:+,.0f}）＋ 套利 {arb['nav']:,.2f}（{arb_cap:+,.0f}）\n")
    L.append("---")
    L.append("")
    # 横截面对冲部分（去掉其自带的一级标题，保留二级起）
    for line in cross_md.splitlines():
        if line.startswith("# 商品期货横截面"):
            continue
        if line.startswith("## 报告期"):
            continue
        L.append(line)
    L.append("")
    L.append("---")
    L.append("")
    L.append(arb_markdown(arb))
    return "\n".join(L)


def save_merged_csv(cross: dict, arb: dict, date_: str):
    import csv as _csv
    out_dir = ROOT / "output" / "merged"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 订单合并 CSV
    rows = []
    for otype, unit in (("open", "横截面对冲"), ("close", "横截面对冲"),
                        ("reduce", "横截面对冲"), ("hold", "横截面对冲")):
        for od in cross["res"]["orders"].get(otype, []):
            rows.append({
                "unit": unit, "date": date_, "action": otype,
                "variety": od.get("variety"), "pair": od.get("pair", ""),
                "dir": od.get("dir"), "lots": od.get("lots"),
                "price": od.get("price") or od.get("entry_price"),
                "reason": od.get("reason", ""),
            })
    for otype in ("open", "close", "hold"):
        for od in arb["orders"][otype]:
            rows.append({
                "unit": "价差套利", "date": date_, "action": otype,
                "variety": "", "pair": od.get("pair"),
                "dir": od.get("dir"), "lots": f"{od.get('sa')}/{od.get('sb')}",
                "price": f"{od.get('open_a', 0):,.0f}/{od.get('open_b', 0):,.0f}",
                "reason": od.get("reason", ""),
            })
    if rows:
        cols = ["unit", "date", "action", "variety", "pair", "dir", "lots", "price", "reason"]
        with open(out_dir / f"merged_orders_{date_}.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = _csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)

    # 净值历史合并 CSV
    hist = []
    for h in cross["res"]["acc"]["history"]:
        hist.append({"unit": "横截面对冲", "date": h["date"],
                     "nav": h.get("nav_total"), "pnl_day": h.get("pnl_day")})
    for h in arb["history"]:
        hist.append({"unit": "价差套利", "date": h["date"],
                     "nav": h.get("nav"), "pnl_day": h.get("pnl_day")})
    if hist:
        with open(out_dir / f"merged_nav_{date_}.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = _csv.DictWriter(f, fieldnames=["unit", "date", "nav", "pnl_day"])
            w.writeheader()
            w.writerows(hist)
    print("== 合并 CSV 已落盘:", out_dir)


# --------------------------------------------------------------------------
# 5) 合并飞书卡片（一张卡片两个 section）
# --------------------------------------------------------------------------
def build_merged_card(cross: dict, arb: dict) -> dict:
    from sim_trade.feishu_pusher import (
        make_header, make_text_element, make_column_set, make_divider, make_note,
    )
    from sim_trade.feishu_push import _fmt_order_items
    from sim_trade.config import SECTOR_OF

    acc = cross["res"]["acc"]
    date_display = str(cross["last"].date())
    snap = cross["res"]["snap"]

    elements = []
    elements.append(make_text_element(
        f"**商品期货横截面套利模拟盘** ｜ 横截面对冲20万＋价差套利20万 ｜ {date_display} ｜ **DoMate**"
    ))
    elements.append(make_divider())

    # ==== 单元一：横截面对冲 ====
    cross_total = acc["total_nav"]
    cross_pnl = cross["res"]["pnl_day"]
    elements.append(make_column_set([
        (f"■横截面对冲净值\n**{cross_total:,.0f}**", 'weighted', 1),
        (f"当日盈亏\n**{cross_pnl:+,.0f}**", 'weighted', 1),
        (f"累计\n**{cross_total - 200_000:+,.0f}**", 'weighted', 1),
    ]))
    s = {}
    for sec in ("化工", "农产品"):
        g = snap[snap["sector"] == sec].dropna(subset=["score"])
        s[sec] = round(float(g["score"].std(ddof=1)), 2) if len(g) >= 4 else None
    chem_flag = "✅" if (s["化工"] is not None and s["化工"] >= 0.8) else "⚠️"
    agri_flag = "✅" if (s["农产品"] is not None and s["农产品"] >= 0.8) else "⚠️"
    elements.append(make_text_element(
        f"**横截面对冲｜分化度**（σ≥0.8 有机会）：化工 {s['化工']} {chem_flag} ｜ 农产品 {s['农产品']} {agri_flag}"
    ))
    lines = _fmt_order_items(cross["res"]["orders"], None)
    elements.append(make_text_element("**▸ 今日仓单（横截面对冲）**\n" + "\n".join(lines)))
    if acc["positions"]:
        pos_lines = []
        for v, pos in sorted(acc["positions"].items()):
            nv = pos["lots"] * pos["entry_price"] * (SECTOR_OF.get(v) and 1 or 1)
            from sim_trade.config import CONTRACT_MULTIPLIER, vname
            nv = pos["lots"] * pos["entry_price"] * CONTRACT_MULTIPLIER.get(v, 1)
            pct = nv / acc["total_nav"] * 100
            dircn = "多" if pos["dir"] == 1 else "空"
            pos_lines.append(f"[{pos['sector']}] {vname(v)} {dircn} {pos['lots']}手 @{pos['entry_price']:,.0f} 占{pct:.1f}%")
        elements.append(make_text_element("**▸ 当前持仓（横截面对冲）**\n" + "\n".join(pos_lines)))
    elements.append(make_divider())

    # ==== 单元二：价差套利 ====
    elements.append(make_column_set([
        (f"■价差套利净值\n**{arb['nav']:,.0f}**", 'weighted', 1),
        (f"当日盈亏\n**{arb['pnl_day']:+,.0f}**", 'weighted', 1),
        (f"累计\n**{arb['nav'] - 200_000:+,.0f}**", 'weighted', 1),
    ]))
    arb_lines = []
    for o in arb["orders"]["open"]:
        arb_lines.append(f"◆开仓｜{o['pair']} {_DIR_CN[o['dir']]} {o['sa']}/{o['sb']}手 "
                         f"z开{o['z_open']:+.2f}｜保证金{o['margin']:,.0f}")
    for c in arb["orders"]["close"]:
        arb_lines.append(f"✕平仓｜{c['pair']} {_DIR_CN[c['dir']]} 净{c['net']:+,.0f}｜{c['reason']}")
    if not arb_lines:
        arb_lines.append("— 今日无开平动作 —")
    elements.append(make_text_element("**▸ 今日仓单（价差套利）**\n" + "\n".join(arb_lines)))
    if arb["positions"]:
        pl = []
        for pos in arb["positions"]:
            zt = pos.get("z_trigger")
            pl.append(f"{pos['pair']} {_DIR_CN[pos['dir']]} {pos['sa']}/{pos['sb']}手 "
                      f"z={pos['z']:+}（触发{zt and round(zt,2)}）")
        elements.append(make_text_element("**▸ 当前持仓（价差套利）**\n" + "\n".join(pl)))
    if arb["pending"]:
        pl = [f"{p['pair']} {_DIR_CN[p['dir']]} {p['streak']}/{p['need']}日" for p in arb["pending"]]
        elements.append(make_text_element("**⏳ 确认中候选**\n" + "\n".join(pl)))
    if arb["skipped"]:
        skl = [f"{s['pair']} 保证金{s['margin_need']:,.0f}/{s['used']:,.0f}" for s in arb["skipped"]]
        elements.append(make_text_element("**⚠️ 资金约束未开**\n" + "\n".join(skl)))

    elements.append(make_note(
        "横截面对冲：10万/板块单元·保证金≥10%·ATR20倒数加权·杠杆≤1.5倍·双周调仓｜"
        "价差套利：20万·保证金率10%·每腿目标名义30万·N=3动量确认·|z|≥2开·|z|≤0.5平｜"
        "收盘估算，仅供参考，不构成投资建议"
    ))
    card = {
        'config': {'wide_screen_mode': True},
        'header': make_header(f"商品期货横截面套利模拟盘 ｜ {date_display} ｜ DoMate", 'blue'),
        'elements': elements,
    }
    return card


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true", help="跳过横截面对冲行情抓取")
    ap.add_argument("--no-arb-update", action="store_true", help="跳过套利数据更新")
    ap.add_argument("--no-push", action="store_true", help="不推送飞书")
    ap.add_argument("--reset", action="store_true", help="重置横截面对冲账户")
    ap.add_argument("--reset-arb", action="store_true", help="重置套利账户")
    ap.add_argument("--send", action="store_true", help="推送飞书")
    args = ap.parse_args()
    push = args.send and not args.no_push

    # 横截面对冲单元
    cross = run_cross_section(args.no_fetch, args.reset)

    # 套利单元
    if args.reset_arb:
        import arb_sim_daily
        arb_sim_daily.save_account(arb_sim_daily.new_account())
        print("[arb-sim] 套利账户已重置为 20 万")
    target = str(cross["last"].date())
    if not args.no_arb_update:
        update_arb_data(target)
    import arb_sim_daily
    arb = arb_sim_daily.run_daily(latest=target)
    print(f"== 套利推进完成：净值 {arb['nav']:,.2f}，当日盈亏 {arb['pnl_day']:+,.2f}，"
          f"持仓 {len(arb['positions'])} 对，开 {len(arb['orders']['open'])} 平 {len(arb['orders']['close'])}")

    # 合并报告
    date_ = str(cross["last"].date())
    text = build_merged_text(cross, arb)
    out_dir = ROOT / "output" / "merged"
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"merged_report_{date_}.md"
    md_path.write_text(text, encoding="utf-8")
    print("== 合并报告已落盘:", md_path)
    save_merged_csv(cross, arb, date_)

    # 飞书卡片
    if push:
        from sim_trade.feishu_pusher import send_feishu_card
        card = build_merged_card(cross, arb)
        (out_dir / f"feishu_card_{date_}.json").write_text(
            json.dumps(card, ensure_ascii=False, indent=1), encoding="utf-8")
        ok = send_feishu_card(card)
        print("== 飞书推送:", "成功" if ok else "失败")
    else:
        print("== 跳过飞书推送（--no-push / 未 --send）")

    # 运行摘要（供定时任务日志）
    summary = {
        "date": date_,
        "cross_nav": cross["res"]["acc"]["total_nav"], "cross_pnl": cross["res"]["pnl_day"],
        "arb_nav": arb["nav"], "arb_pnl": arb["pnl_day"],
        "arb_n_pos": len(arb["positions"]),
        "cross_open": len(cross["res"]["orders"]["open"]),
        "cross_close": len(cross["res"]["orders"]["close"]),
        "arb_open": len(arb["orders"]["open"]), "arb_close": len(arb["orders"]["close"]),
        "pushed": push,
    }
    (ROOT / "output" / "merged" / "last_run.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print("== 完成：", json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()