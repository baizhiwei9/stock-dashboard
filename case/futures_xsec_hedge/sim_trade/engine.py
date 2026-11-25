# -*- coding: utf-8 -*-
"""模拟盘交易引擎
==========================
每日流程：
  1) 读取最新共同交易日因子快照
  2) 双周调仓日（或建仓首日）重算目标仓位：机会驱动配对
     - σ(板块 score) >= 0.8 才有机会；|score_adj - 板块均值| >= k*σ 入选
     - 每板块多头最多 MAX_N、空头最多 MAX_N；腿内 ATR20 倒数加权
     - 板块内多空名义各 = BASE_LEV * 单元净值
  3) 非调仓日只跑风控：score 反转 1σ 减半 / 2σ 全平；
     back 转 contango 强制降至 30%；组合回撤 8% -> 全组合降至 50%
  4) diff 当前持仓生成开/平/减/维持指令（含手数、方向、价位、手数差）
  5) 当日盈亏 = Σ lots*(close - prev_close)*mult*dir（开仓当日计 0）
     + 已实现盈亏（平/减仓按当日价与加权成本差）+ 成本
"""
import json
import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from sim_trade.config import (
    CHEM, SECTORS, UNIT_CAPITAL, TOTAL_CAPITAL, MARGIN_RATE, COST_RATE,
    BASE_LEV, MAX_LEV, DD_TRIGGER, SIGMA_GATE, K, MAX_N, LAM, SIGMA_WIN,
    CONTRACT_MULTIPLIER, SECTOR_OF, UNIVERSE, STATE_DIR, OUT_DIR, FACTORS_FILE, vname,
)


# ---------------- 因子快照与信号 ----------------

def build_snapshot(factors: pd.DataFrame, d: pd.Timestamp) -> pd.DataFrame:
    """取某交易日全品种因子快照，返回含 score_adj 的 DataFrame"""
    sub = factors[factors["date"] == d].copy()
    sub = sub[sub["variety"].isin(UNIVERSE)]
    sub["sector"] = sub["variety"].map(SECTOR_OF)
    sub["score_adj"] = sub["score"] - LAM * sub["momz_20"]
    return sub


def compute_target_positions(snap: pd.DataFrame, unit_nav: float = UNIT_CAPITAL) -> Dict:
    """机会驱动配对：板块分化度门槛 + 极值腿门槛 + 腿数上限。
    板块必须同时存在多头腿与空头腿（禁止单边裸多裸空）；
    否则该板块空仓等待。
    返回 {variety: {dir, weight(占单元净值比例), score_adj, sigma}}"""
    target: Dict[str, dict] = {}
    for sec in SECTORS:
        g = snap[snap["sector"] == sec].dropna(subset=["score_adj", "atr20"]).copy()
        if len(g) < 4:
            continue
        vals = g["score_adj"].values
        mu, sig = vals.mean(), vals.std(ddof=1)
        if not np.isfinite(sig) or pd.isna(sig) or sig < SIGMA_GATE:
            continue  # 分化度不足 -> 板块空仓
        longs = g[g["score_adj"] - mu >= K * sig].sort_values("score_adj", ascending=False)
        shorts = g[mu - g["score_adj"] >= K * sig].sort_values("score_adj")
        if longs.empty or shorts.empty:
            continue  # 只有单侧腿 -> 禁止单边裸多/裸空，板块空仓
        longs, shorts = longs.head(MAX_N), shorts.head(MAX_N)
        for grp, sgn in ((longs, 1.0), (shorts, -1.0)):
            w = (1.0 / grp["atr20"])
            w = w / w.sum() * BASE_LEV  # 该侧总权重 = BASE_LEV × 单元净值
            for v, wv in zip(grp["variety"].values, w.values):
                target[str(v)] = {"dir": sgn, "weight": float(wv),
                                  "score_adj": float(grp.loc[grp["variety"] == v, "score_adj"].iloc[0]),
                                  "sigma": float(sig)}
    return target


def lots_from_notional(notional: float, price: float, variety: str) -> int:
    if price <= 0 or variety not in CONTRACT_MULTIPLIER:
        return 0
    mult = CONTRACT_MULTIPLIER[variety]
    lots = notional / (price * mult)
    return max(0, int(round(lots)))


def notional_of(pos: dict) -> float:
    v = pos.get("variety")
    if not v or v not in CONTRACT_MULTIPLIER:
        return 0.0
    return pos.get("lots", 0) * pos.get("entry_price", 0) * CONTRACT_MULTIPLIER[v]


# ---------------- 账户/持仓结构 ----------------

def new_account() -> dict:
    return {
        "cash": {sec: UNIT_CAPITAL for sec in SECTORS},   # 每板块单元现金（独立核算）
        "positions": {},   # variety -> {..}
        "nav": {sec: UNIT_CAPITAL for sec in SECTORS},    # 每单元净值（独立核算）
        "total_nav": TOTAL_CAPITAL,
        "peak_nav": TOTAL_CAPITAL,
        "dd_cut": False,
        "trig_peak": None,
        "last_trade_date": None,
        "last_reb_date": None,
        "last_day": None,  # 当日首次真实运行的动作/盈亏缓存（同日重复运行恢复用）
        "history": [],     # 每日净值记录
    }


def load_account() -> dict:
    p = STATE_DIR / "account.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return new_account()


def save_account(acc: dict) -> None:
    (STATE_DIR / "account.json").write_text(
        json.dumps(acc, ensure_ascii=False, indent=1), encoding="utf-8")


def reset_account() -> dict:
    acc = new_account()
    save_account(acc)
    return acc


# ---------------- 双周调仓日 ----------------

def biweek_rebalance_dates(dates: List[pd.Timestamp]) -> set:
    """与回测一致：W-FRI 纬度，按 '2W-FRI' 分组取每组最后交易日"""
    s = pd.Series(pd.to_datetime(dates))
    period = s.dt.to_period("2W-FRI")
    df = pd.DataFrame({"date": s, "p": period})
    return set(df.groupby("p")["date"].max().values)


# ---------------- 风控（日频，非调仓日） ----------------

def apply_risk(acc: dict, snap: pd.DataFrame, day: pd.Timestamp, orders: Dict, prices: Dict) -> Dict:
    """对每持仓：
       - score 反转超过 1σ -> 减仓 50%；超过 2σ -> 全平
       - back 转 contango（entry_roll>0 且今 roll_annual<0）-> 强制降至原仓位 30%
    直接修改持仓 lots，并把动作记入 orders"""
    for v, pos in list(acc["positions"].items()):
        row = snap[snap["variety"] == v]
        if row.empty:
            continue
        r = row.iloc[0]
        cur = r["score_adj"] if pd.notna(r["score_adj"]) else float("nan")
        if pd.isna(cur):
            continue
        dsc = cur - pos["score_adj"]
        sigma = pos.get("sigma", 1.0) or 1.0
        if pos["dir"] > 0:
            if dsc < -2 * sigma:
                orders.setdefault("close", []).append({**pos, "reason": f"score反转{dsc:.2f}σ<−2σ全平", "date": str(day.date()), "price": prices.get(v)})
                acc["positions"].pop(v, None); continue
            if dsc < -sigma:
                red = max(0, int(pos["lots"] * 0.5))
                if red > 0:
                    orders.setdefault("reduce", []).append({**pos, "_reduced_lots": red, "reason": f"score反转{dsc:.2f}σ减半", "date": str(day.date()), "price": prices.get(v)})
                    pos["lots"] -= red
        else:
            if dsc > 2 * sigma:
                orders.setdefault("close", []).append({**pos, "reason": f"score反转{dsc:.2f}σ>+2σ全平", "date": str(day.date()), "price": prices.get(v)})
                acc["positions"].pop(v, None); continue
            if dsc > sigma:
                red = max(0, int(pos["lots"] * 0.5))
                if red > 0:
                    orders.setdefault("reduce", []).append({**pos, "_reduced_lots": red, "reason": f"score反转{dsc:.2f}σ减半", "date": str(day.date()), "price": prices.get(v)})
                    pos["lots"] -= red
        # back -> contango
        if pos.get("entry_roll", 0) > 0 and pd.notna(r["roll_annual"]) and r["roll_annual"] < 0 and pos.get("lots", 0) > 0:
            new_lots = max(1, int(round(pos["lots"] * 0.30)))
            red = pos["lots"] - new_lots
            if red > 0:
                orders.setdefault("reduce", []).append({**pos, "_reduced_lots": red, "reason": "back转contango降至30%", "date": str(day.date()), "price": prices.get(v)})
            pos["lots"] = new_lots
    return orders


# ---------------- 主引擎 ----------------

def run_daily(d: pd.Timestamp, factors: pd.DataFrame, force_rebalance: bool = False,
              reset: bool = False) -> dict:
    """单日推进。返回当日仓单报告所需的全部数据。
    幂等：同一天重复运行不重复开仓（force_rebalance=False 时）。"""
    if reset:
        acc = reset_account()
    else:
        acc = load_account()
    snap = build_snapshot(factors, d).set_index("variety", drop=False)
    orders = {"open": [], "close": [], "reduce": [], "hold": []}
    prices = {}
    prev_close = {}   # 前一交易日收盘价（用于当日盈亏）
    for v in snap.index:
        prices[v] = float(snap.loc[v, "main_close"])
        pr = factors[(factors["variety"] == v) & (factors["date"] < d)]
        if len(pr):
            prev_close[v] = float(pr["main_close"].iloc[-1])
        else:
            prev_close[v] = np.nan

    # 清除昨日遗留的加仓标记（减掉后才算当日盈亏的“已有手数”）
    for pos in acc.get("positions", {}).values():
        pos.pop("new_lots_today", None)

    already_ran_today = (acc.get("last_trade_date") == str(d.date()) and not force_rebalance and not reset)
    # 同日重复运行：恢复当日首次真实运行的仓单/盈亏，避免被覆盖成"0动作"
    if already_ran_today and acc.get("last_day") and acc["last_day"].get("date") == str(d.date()):
        ld = acc["last_day"]
        return {
            "date": d, "snap": snap, "orders": ld["orders"], "acc": acc,
            "pnl_day": ld["pnl_day"], "pnl_detail": ld["pnl_detail"],
            "is_reb": ld["is_reb"], "is_first": False, "cost": ld["cost"],
            "realized": ld["realized"],
        }
    is_first = (acc.get("last_trade_date") is None)
    reb_dates = biweek_rebalance_dates(sorted(factors["date"].unique()))
    # 仅在真实双周调仓日或首日建仓；空仓后静默等待下一调仓日，不逐日重建
    is_reb = (d in reb_dates) or force_rebalance or (is_first and not already_ran_today)

    # 1) 调仓/首日：重算目标仓位，diff 手数生成开/平/减/维持
    if is_reb and not already_ran_today:
        target = compute_target_positions(snap, UNIT_CAPITAL)
        # 手数取整：预算不足 1 手的腿剔除（0 手不可交易）
        tradable = {}
        for v, t in target.items():
            price = prices.get(v)
            if price is None or price <= 0:
                continue
            lt = lots_from_notional(t["weight"] * UNIT_CAPITAL, price, v)
            if lt > 0:
                tradable[v] = (t, lt)
        # 板块成对校验：板块必须同时存在可交易多腿与空腿，否则整板块不开仓
        sector_ok = {}
        for sec in SECTORS:
            legs = [t for v, (t, _) in tradable.items() if SECTOR_OF[v] == sec]
            sector_ok[sec] = any(t["dir"] > 0 for t in legs) and any(t["dir"] < 0 for t in legs)
        old = dict(acc["positions"])
        # 平掉：不在可交易目标中 / 方向改变 / 板块不成对
        for v, pos in old.items():
            sec = SECTOR_OF[v]
            keep = (sector_ok.get(sec, False) and v in tradable and pos["dir"] == tradable[v][0]["dir"])
            if not keep:
                orders["close"].append({**pos, "variety": v, "reason": "调仓换仓平仓" if sector_ok.get(sec, False) else "板块缺少对冲腿，平仓等待",
                                        "date": str(d.date()), "price": prices.get(v)})
                acc["positions"].pop(v, None)
        # 开/加目标腿
        for v, (t, lots) in tradable.items():
            if not sector_ok.get(SECTOR_OF[v], False):
                continue
            price = prices.get(v)
            row = snap.loc[v]
            entry_roll = float(row["roll_annual"]) if pd.notna(row["roll_annual"]) else 0.0
            ser = factors[factors["variety"] == v]["score"].dropna()
            sigma_est = float(np.nanstd(ser.tail(SIGMA_WIN).values)) if len(ser) >= SIGMA_WIN else 1.0
            sigma_est = sigma_est if (np.isfinite(sigma_est) and sigma_est > 0) else 1.0
            pos = {"variety": v, "dir": t["dir"], "lots": lots, "entry_price": price,
                   "entry_date": str(d.date()), "sector": SECTOR_OF[v],
                   "weight": t["weight"], "score_adj": t["score_adj"], "sigma": sigma_est,
                   "entry_roll": entry_roll}
            oldp = old.get(v)
            if oldp is not None and oldp["dir"] == t["dir"]:
                # 同方向：手数差决定动作
                delta = lots - oldp["lots"]
                if delta > 0:
                    # 加仓：加权成本，当日新加手数不计入当日涨跌盈亏
                    new_cost = oldp["entry_price"] * oldp["lots"] + price * delta
                    oldp["lots"] += delta
                    oldp["entry_price"] = new_cost / oldp["lots"]
                    oldp["weight"] = t["weight"]
                    oldp["score_adj"] = t["score_adj"]
                    oldp["new_lots_today"] = delta
                    orders["open"].append({**pos, "lots": delta, "reason": "调仓加仓", "date": str(d.date()), "price": price, "entry_price": price})
                elif delta < 0:
                    red = -delta
                    orders["reduce"].append({**oldp, "_reduced_lots": red, "reason": "调仓减仓", "date": str(d.date()), "price": price})
                    oldp["lots"] -= red
                else:
                    orders["hold"].append({**pos, "reason": "维持", "date": str(d.date()), "price": price})
                acc["positions"][v] = oldp
            else:
                acc["positions"][v] = pos
                orders["open"].append(pos)
        acc["last_reb_date"] = str(d.date())
    elif is_reb and already_ran_today:
        # 同日重复运行：只补维持标记，不重复交易
        for v, pos in acc.get("positions", {}).items():
            orders["hold"].append({**pos, "reason": "同日重复运行-维持", "date": str(d.date()), "price": prices.get(v)})
        is_reb = False
    else:
        # 非调仓日：先跑风控（直接改 lots）
        if not already_ran_today:
            orders = apply_risk(acc, snap, d, orders, prices)
        else:
            for v, pos in acc.get("positions", {}).items():
                orders["hold"].append({**pos, "reason": "同日重复运行-维持", "date": str(d.date()), "price": prices.get(v)})

    # 2) 当日盈亏：持仓 (close - prev_close)*mult*lots*dir + 已实现（平/减仓按当日价）
    realized = 0.0
    for otype in ("close", "reduce"):
        for od in orders.get(otype, []):
            v = od.get("variety")
            if not v or v not in CONTRACT_MULTIPLIER:
                continue
            red_lots = od.get("_reduced_lots", od.get("lots", 0))
            price = od.get("price") or od.get("entry_price") or 0
            r = red_lots * (price - od.get("entry_price", 0)) * CONTRACT_MULTIPLIER[v] * od.get("dir", 0)
            realized += r

    pnl_day = realized
    pnl_detail = []
    for v, pos in acc["positions"].items():
        row = snap[snap["variety"] == v]
        if row.empty:
            continue
        new_today = pos.get("new_lots_today", 0)
        if pos["entry_date"] == str(d.date()):
            # 当日新建仓位（首日建仓/调仓换仓开仓）不计当日盈亏
            pnl_detail.append({"variety": v, "dir": pos["dir"], "lots": pos["lots"],
                               "pnl": 0.0, "note": "当日开仓"})
            continue
        pnl_lots = pos["lots"] - new_today
        if pnl_lots <= 0:
            pnl_detail.append({"variety": v, "dir": pos["dir"], "lots": pos["lots"],
                               "pnl": 0.0, "note": "当日加仓" if new_today else "当日开仓"})
            continue
        pc = prev_close.get(v, np.nan)
        close = prices.get(v, np.nan)
        if pd.isna(pc) or pd.isna(close) or pc <= 0:
            pnl_detail.append({"variety": v, "dir": pos["dir"], "lots": pnl_lots, "pnl": 0.0, "note": "无前收盘"})
            continue
        p = pnl_lots * (close - pc) * CONTRACT_MULTIPLIER[v] * pos["dir"]
        pnl_day += p
        pnl_detail.append({"variety": v, "dir": pos["dir"], "lots": pnl_lots,
                           "pnl": round(p, 2), "close": close, "prev_close": pc})

    # 3) 交易成本：开/平/减按成交名义单边 0.025%，板块归属
    cost = 0.0
    cost_by_sector = {sec: 0.0 for sec in SECTORS}
    for otype in ("open", "close", "reduce"):
        for od in orders.get(otype, []):
            v = od.get("variety")
            if not v or v not in CONTRACT_MULTIPLIER:
                continue
            price = od.get("price") or od.get("entry_price") or 0
            sec = SECTOR_OF[v]
            lots = od.get("_reduced_lots", od.get("lots", 0))
            c = lots * price * CONTRACT_MULTIPLIER[v] * COST_RATE
            cost += c
            cost_by_sector[sec] += c

    # 4) 净值更新（板块独立核算）：未实现盈亏(pnl_detail) + 已实现盈亏(平/减仓)
    for sec in SECTORS:
        sec_pnl = sum(pd["pnl"] for pd in pnl_detail if SECTOR_OF[pd["variety"]] == sec)
        acc["nav"][sec] = acc["nav"].get(sec, UNIT_CAPITAL) + sec_pnl - cost_by_sector[sec]
    for otype in ("close", "reduce"):
        for od in orders.get(otype, []):
            v = od.get("variety")
            if v and v in SECTOR_OF and CONTRACT_MULTIPLIER.get(v):
                red_lots = od.get("_reduced_lots", od.get("lots", 0))
                price = od.get("price") or od.get("entry_price") or 0
                r = red_lots * (price - od.get("entry_price", 0)) * CONTRACT_MULTIPLIER[v] * od.get("dir", 0)
                acc["nav"][SECTOR_OF[v]] += r
    acc["total_nav"] = acc["nav"]["化工"] + acc["nav"]["农产品"]
    acc["peak_nav"] = max(acc["peak_nav"], acc["total_nav"])

    # 5) 回撤风控
    if acc["peak_nav"] > 0 and (acc["total_nav"] / acc["peak_nav"] - 1) < -DD_TRIGGER and not acc["dd_cut"]:
        acc["dd_cut"] = True
        acc["trig_peak"] = acc["peak_nav"]
        for v, pos in list(acc["positions"].items()):
            red = max(0, int(pos["lots"] * 0.5))
            if red > 0:
                orders.setdefault("reduce", []).append({**pos, "_reduced_lots": red, "date": str(d.date()), "price": prices.get(v), "reason": f"组合回撤>{DD_TRIGGER:.0%}降仓50%"})
                pos["lots"] -= red
    elif acc["dd_cut"] and acc["total_nav"] >= (acc["trig_peak"] or 0):
        acc["dd_cut"] = False
        acc["trig_peak"] = None

    acc["last_trade_date"] = str(d.date())
    if not already_ran_today:
        acc["last_day"] = {
            "date": str(d.date()),
            "orders": orders, "pnl_day": pnl_day - cost,
            "pnl_detail": pnl_detail, "is_reb": bool(is_reb),
            "cost": cost, "realized": realized,
        }
    hist_entry = {
        "date": str(d.date()), "nav_total": round(acc["total_nav"], 2),
        "nav_chem": round(acc["nav"]["化工"], 2), "nav_agri": round(acc["nav"]["农产品"], 2),
        "pnl_day": round(pnl_day - cost, 2), "is_reb": bool(is_reb),
    }
    if not already_ran_today:
        # 真实新交易日才写历史；同日重复运行不追加也不覆盖（保留当日首条真实记录）
        if acc["history"] and acc["history"][-1]["date"] == str(d.date()):
            acc["history"][-1] = hist_entry
        else:
            acc["history"].append(hist_entry)
    save_account(acc)

    return {
        "date": d, "snap": snap, "orders": orders, "acc": acc,
        "pnl_day": pnl_day - cost, "pnl_detail": pnl_detail,
        "is_reb": bool(is_reb), "is_first": bool(is_first) or reset, "cost": cost,
        "realized": realized,
    }


if __name__ == "__main__":
    factors = pd.read_csv(FACTORS_FILE, parse_dates=["date"])
    last = factors["date"].max()
    print("最新共同交易日:", last.date())
    res = run_daily(last, factors, force_rebalance=True, reset=True)
    acc = res["acc"]
    print("总净值:", round(acc["total_nav"], 2))
    print("开仓:", len(res["orders"]["open"]), "平仓:", len(res["orders"]["close"]),
          "减仓:", len(res["orders"]["reduce"]), "维持:", len(res["orders"]["hold"]))
    for od in res["orders"]["open"]:
        print(" 开", vname(od["variety"]), od["dir"], od["lots"], "手 @", od["entry_price"])