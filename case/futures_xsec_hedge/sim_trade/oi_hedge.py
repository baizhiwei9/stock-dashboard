# -*- coding: utf-8 -*-
"""持仓量横截面对冲 —— 板块内 OI_ROC40 横截面多空（新增栏目）
=================================================================
规格依据（XSEC_COMMODITY_SPEC-1）：
  因子 = OI_ROC40 = ln(OI+1).diff(40)（40交易日对数差分）
  权重 = 板块内横截面 z / Σ|z|，多正空负，绝对敞口和=1，T日信号→T+1收益
  频率 = 周频调仓（规格 A1 优化方向：周频+5bp 净年化显著优于日频）
  板块 = 化工 9 + 农产品 10（现有 19 品种池内部对冲，板块净敞口=0）

本模块与既有「横截面多空对冲」（机会驱动配对）完全独立：
  独立账户状态（oi_hedge/state/account.json）、独立推进引擎、日报独立栏目。
  回测口径与模拟盘一致：周 W-FRI 周期最后交易日调仓，当日以收盘价成交当日不计盈亏；
  模拟盘叠加组合回撤 8% → 全组合降至 50% 风控（与既有单元一致）。
"""
import json
from pathlib import Path
from typing import Dict, List, Optional, Set

import numpy as np
import pandas as pd

from sim_trade.config import (
    CHEM, SECTORS, OI_UNIT_CAPITAL, OI_TOTAL_CAPITAL, COST_RATE, CONTRACT_MULTIPLIER,
    SECTOR_OF, PANEL_FILE, OUT_DIR, STATE_DIR, DD_TRIGGER, vname,
)

OI_WIN = 40                      # OI 变化率窗口（交易日）
EVAL_START = pd.Timestamp("2021-02-01")   # 因子预热后评估起点


# ---------------- 因子构建 ----------------

def build_oi_factor(panel: pd.DataFrame) -> pd.DataFrame:
    """从 panel 构建 OI 因子：oi_roc40（对数差分）-> 板块内横截面 z -> w=z/Σ|z|"""
    p = panel.copy()
    p = p[p["main_oi"] > 0]
    p["sector"] = p["variety"].map(SECTOR_OF)
    p = p.sort_values(["variety", "date"]).reset_index(drop=True)
    p["oi_roc40"] = p.groupby("variety")["main_oi"].transform(
        lambda s: np.log(s + 1).diff(OI_WIN))

    def xz(s: pd.Series) -> pd.Series:
        return (s - s.mean()) / s.std(ddof=1)

    p["oi_z"] = p.groupby(["date", "sector"])["oi_roc40"].transform(xz)
    p["oi_w"] = p["oi_z"] / p.groupby(["date", "sector"])["oi_z"].transform(
        lambda s: s.abs().sum())
    return p


def snapshot_at(panel: pd.DataFrame, d: pd.Timestamp) -> pd.DataFrame:
    """某交易日全品种持仓量截面快照（含 oi_z / oi_w / 价格）"""
    p = build_oi_factor(panel)
    sub = p[p["date"] == d].copy()
    sub["sector"] = sub["variety"].map(SECTOR_OF)
    return sub


# ---------------- 周频调仓日 ----------------

def weekly_rebalance_dates(dates: List[pd.Timestamp]) -> set:
    """每周（W-FRI 周期）最后交易日集合——与回测一致"""
    s = pd.Series(pd.to_datetime(dates))
    period = s.dt.to_period("W-FRI")
    df = pd.DataFrame({"date": s, "p": period})
    return set(df.groupby("p")["date"].max().values)


# ---------------- 账户 ----------------

def _oi_state_dir() -> Path:
    d = STATE_DIR / "oi_hedge"
    d.mkdir(parents=True, exist_ok=True)
    return d


def new_account() -> dict:
    return {
        "cash": {sec: OI_UNIT_CAPITAL for sec in SECTORS},   # 每板块单元现金（独立核算）
        "positions": {},   # variety -> {dir, lots, entry_price, entry_date, sector, weight, oi_z}
        "nav": {sec: OI_UNIT_CAPITAL for sec in SECTORS},    # 每单元净值
        "total_nav": OI_TOTAL_CAPITAL,
        "peak_nav": OI_TOTAL_CAPITAL,
        "dd_cut": False,
        "trig_peak": None,
        "last_trade_date": None,
        "last_day": None,  # 当日首次真实运行的动作/盈亏缓存（同日重复运行恢复用）
        "history": [],     # 每日净值记录
    }


def load_account() -> dict:
    p = _oi_state_dir() / "account.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return new_account()


def save_account(acc: dict) -> None:
    (_oi_state_dir() / "account.json").write_text(
        json.dumps(acc, ensure_ascii=False, indent=1), encoding="utf-8")


def reset_account() -> dict:
    acc = new_account()
    save_account(acc)
    return acc


# ---------------- 目标仓位与手数 ----------------

def compute_target_weights(snap: pd.DataFrame) -> Dict[str, dict]:
    """板块内 z 权重：w = z/Σ|z|。返回 {variety: {dir, weight(占单元净值), oi_z}}。
    板块必须同时存在多头腿与空头腿，否则该板块空仓等待（禁止单边裸多裸空）。"""
    target: Dict[str, dict] = {}
    for sec in SECTORS:
        g = snap[snap["sector"] == sec].dropna(subset=["oi_z", "oi_w"]).copy()
        if len(g) < 4:
            continue
        longs = g[g["oi_z"] > 0]
        shorts = g[g["oi_z"] < 0]
        if longs.empty or shorts.empty:
            continue
        for grp, sgn in ((longs, 1.0), (shorts, -1.0)):
            for _, r in grp.iterrows():
                target[str(r["variety"])] = {
                    "dir": sgn,
                    "weight": float(r["oi_w"]),          # 板块内权重（绝对敞口和=1）
                    "oi_z": float(r["oi_z"]),
                }
    return target


def lots_from_notional(notional: float, price: float, variety: str) -> int:
    if price <= 0 or variety not in CONTRACT_MULTIPLIER:
        return 0
    mult = CONTRACT_MULTIPLIER[variety]
    lots = notional / (price * mult)
    return max(0, int(round(lots)))


# ---------------- 引擎 ----------------

def run_oi_daily(d: pd.Timestamp, panel: pd.DataFrame, force_rebalance: bool = False,
                 reset: bool = False) -> dict:
    """单日推进持仓量横截面对冲单元。
    周频调仓：每周最后交易日重算目标权重；当日开仓不计盈亏；非调仓日纯持有。
    幂等：同一天重复运行不重复开仓。"""
    if reset:
        acc = reset_account()
    else:
        acc = load_account()

    snap = snapshot_at(panel, d).set_index("variety", drop=False)
    orders = {"open": [], "close": [], "reduce": [], "hold": []}
    prices: Dict[str, float] = {}
    prev_close: Dict[str, float] = {}
    for v in snap.index:
        prices[v] = float(snap.loc[v, "main_close"])
        pr = panel[(panel["variety"] == v) & (panel["date"] < d)]
        prev_close[v] = float(pr["main_close"].iloc[-1]) if len(pr) else np.nan

    for pos in acc.get("positions", {}).values():
        pos.pop("new_lots_today", None)

    already_ran_today = (acc.get("last_trade_date") == str(d.date())
                         and not force_rebalance and not reset)
    # 同日重复运行：恢复当日首次真实运行的仓单/盈亏，避免净值被重复计入持仓盈亏
    if already_ran_today and acc.get("last_day") and acc["last_day"].get("date") == str(d.date()):
        ld = acc["last_day"]
        return {
            "date": d, "snap": snap, "orders": ld["orders"], "acc": acc,
            "pnl_day": ld["pnl_day"], "pnl_detail": ld["pnl_detail"],
            "is_reb": ld["is_reb"], "is_first": False, "cost": ld["cost"],
            "realized": ld["realized"],
        }
    is_first = (acc.get("last_trade_date") is None)
    reb_dates = weekly_rebalance_dates(sorted(panel["date"].unique()))
    is_reb = (d in reb_dates) or force_rebalance or (is_first and not already_ran_today)

    # 1) 调仓/首日：重算目标仓位
    if is_reb and not already_ran_today:
        target = compute_target_weights(snap)
        tradable = {}
        for v, t in target.items():
            price = prices.get(v)
            if price is None or price <= 0:
                continue
            lt = lots_from_notional(abs(t["weight"]) * OI_UNIT_CAPITAL, price, v)
            if lt > 0:
                tradable[v] = (t, lt)
        sector_ok = {}
        for sec in SECTORS:
            legs = [t for v, (t, _) in tradable.items() if SECTOR_OF[v] == sec]
            sector_ok[sec] = any(t["dir"] > 0 for t in legs) and any(t["dir"] < 0 for t in legs)
        old = dict(acc["positions"])
        for v, pos in old.items():
            sec = SECTOR_OF[v]
            keep = (sector_ok.get(sec, False) and v in tradable and pos["dir"] == tradable[v][0]["dir"])
            if not keep:
                orders["close"].append({**pos, "variety": v,
                                        "reason": "周频调仓换仓平仓" if sector_ok.get(sec, False) else "板块缺少对冲腿，平仓等待",
                                        "date": str(d.date()), "price": prices.get(v)})
                acc["positions"].pop(v, None)
        for v, (t, lots) in tradable.items():
            if not sector_ok.get(SECTOR_OF[v], False):
                continue
            price = prices.get(v)
            pos = {"variety": v, "dir": t["dir"], "lots": lots, "entry_price": price,
                   "entry_date": str(d.date()), "sector": SECTOR_OF[v],
                   "weight": t["weight"], "oi_z": t["oi_z"]}
            oldp = old.get(v)
            if oldp is not None and oldp["dir"] == t["dir"]:
                delta = lots - oldp["lots"]
                if delta > 0:
                    new_cost = oldp["entry_price"] * oldp["lots"] + price * delta
                    oldp["lots"] += delta
                    oldp["entry_price"] = new_cost / oldp["lots"]
                    oldp["weight"] = t["weight"]
                    oldp["oi_z"] = t["oi_z"]
                    oldp["new_lots_today"] = delta
                    orders["open"].append({**pos, "lots": delta, "reason": "周频调仓加仓",
                                           "date": str(d.date()), "price": price})
                elif delta < 0:
                    red = -delta
                    orders["reduce"].append({**oldp, "_reduced_lots": red, "reason": "周频调仓减仓",
                                             "date": str(d.date()), "price": price})
                    oldp["lots"] -= red
                else:
                    orders["hold"].append({**pos, "reason": "维持", "date": str(d.date()), "price": price})
                acc["positions"][v] = oldp
            else:
                acc["positions"][v] = pos
                orders["open"].append(pos)
    elif is_reb and already_ran_today:
        for v, pos in acc.get("positions", {}).items():
            orders["hold"].append({**pos, "reason": "同日重复运行-维持", "date": str(d.date()),
                                   "price": prices.get(v)})
        is_reb = False
    else:
        # 非调仓日：纯持有（因子为慢变量，不做日频开平）
        if already_ran_today:
            for v, pos in acc.get("positions", {}).items():
                orders["hold"].append({**pos, "reason": "同日重复运行-维持", "date": str(d.date()),
                                       "price": prices.get(v)})
        else:
            for v, pos in acc.get("positions", {}).items():
                orders["hold"].append({**pos, "reason": "周内持有", "date": str(d.date()),
                                       "price": prices.get(v)})

    # 2) 当日盈亏（开仓当日 0；已实现盈亏计入平/减）
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
        new_today = pos.get("new_lots_today", 0)
        if pos["entry_date"] == str(d.date()):
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
            pnl_detail.append({"variety": v, "dir": pos["dir"], "lots": pnl_lots,
                               "pnl": 0.0, "note": "无前收盘"})
            continue
        p = pnl_lots * (close - pc) * CONTRACT_MULTIPLIER[v] * pos["dir"]
        pnl_day += p
        pnl_detail.append({"variety": v, "dir": pos["dir"], "lots": pnl_lots,
                           "pnl": round(p, 2), "close": close, "prev_close": pc})

    # 3) 交易成本（单边 0.025%）
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

    # 4) 净值更新
    for sec in SECTORS:
        sec_pnl = sum(pd["pnl"] for pd in pnl_detail if SECTOR_OF[pd["variety"]] == sec)
        acc["nav"][sec] = acc["nav"].get(sec, OI_UNIT_CAPITAL) + sec_pnl - cost_by_sector[sec]
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

    # 5) 回撤风控：组合回撤 8% -> 全组合降至 50%
    if acc["peak_nav"] > 0 and (acc["total_nav"] / acc["peak_nav"] - 1) < -DD_TRIGGER and not acc["dd_cut"]:
        acc["dd_cut"] = True
        acc["trig_peak"] = acc["peak_nav"]
        for v, pos in list(acc["positions"].items()):
            red = max(0, int(pos["lots"] * 0.5))
            if red > 0:
                orders.setdefault("reduce", []).append(
                    {**pos, "_reduced_lots": red, "date": str(d.date()), "price": prices.get(v),
                     "reason": f"组合回撤>{DD_TRIGGER:.0%}降仓50%"})
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


# ---------------- 回测（核验/报告用） ----------------

def _pivot_wide(p: pd.DataFrame, col: str) -> pd.DataFrame:
    w = p.pivot_table(index="date", columns="variety", values=col)
    return w.sort_index()


def _nw_t(x: pd.Series, lag: int = 20) -> float:
    x = x.dropna()
    n = len(x)
    if n < 10:
        return float("nan")
    mu = x.mean()
    e = x - mu

    def acv(k: int) -> float:
        if k == 0:
            return float((e * e).mean())
        if k >= n:
            return 0.0
        return float((e.iloc[k:] * e.iloc[:-k].values).mean())

    v0 = acv(0)
    se2 = v0 + 2 * sum((1 - k / (lag + 1)) * acv(k) for k in range(1, lag + 1))
    if se2 <= 0 or n <= 1:
        return float("nan")
    return float(mu / np.sqrt(se2 / n))


def oi_metrics(port: pd.Series) -> dict:
    port = port.dropna()
    if port.empty:
        return {}
    n = len(port)
    years = n / 244.0
    ann = (1 + port).prod() ** (1 / years) - 1
    vol = port.std() * np.sqrt(244)
    sharpe = ann / vol if vol > 0 else float("nan")
    cum = (1 + port).cumprod()
    mdd = (cum / cum.cummax() - 1).min()
    return {"ann": ann, "vol": vol, "sharpe": sharpe, "mdd": mdd,
            "n": n, "t_nw": _nw_t(port)}


def backtest_oi(panel: pd.DataFrame, freq: str = "weekly", cost_bps: float = 5.0,
                dd_ctrl: bool = False,
                start: pd.Timestamp = EVAL_START) -> Dict:
    """持仓量横截面对冲历史回测（板块内 z 权重多空）。
    freq: daily | weekly；cost_bps: 单边成本；dd_ctrl: 是否叠加回撤8%降仓50%。"""
    p = build_oi_factor(panel)
    w_piv = _pivot_wide(p, "oi_w")
    r_piv = _pivot_wide(p, "ret_hold")
    dts = w_piv.index

    if freq == "weekly":
        period = pd.Series(dts).dt.to_period("W-FRI")
        rebs = set(pd.Series(dts).groupby(period.values).max().values)
        hold_w = w_piv.shift(1)
        eff_w = hold_w.copy()
        active = None
        for d in hold_w.index:
            if d in rebs:
                active = hold_w.loc[d].copy()
            if active is not None:
                eff_w.loc[d] = active
            else:
                eff_w.loc[d] = np.nan
        combo = (eff_w * r_piv).sum(axis=1, min_count=1)
        combo = combo[(combo.index >= start)]
        turnover = pd.Series(0.0, index=combo.index)
        prev_eff = None
        for d in combo.index:
            if d in rebs:
                cur = eff_w.loc[d].fillna(0.0)
                base = prev_eff if prev_eff is not None else cur * 0.0
                turnover.loc[d] = (cur - base).abs().sum()
                prev_eff = cur
        combo = combo - turnover * (cost_bps / 1e4)
    else:
        hold_w = w_piv.shift(1)
        combo = (hold_w * r_piv).sum(axis=1, min_count=1)
        combo = combo[(combo.index >= start)]
        w_eff = hold_w.fillna(0.0)
        turn = w_eff.diff().abs().sum(axis=1, min_count=1).fillna(0.0)
        turn = turn[turn.index >= start]
        combo = combo - turn * (cost_bps / 1e4)

    if dd_ctrl:
        cum = (1 + combo).cumprod()
        peak = cum.cummax()
        dd_cut = False
        dd_peak = None
        adj = combo.copy()
        scale = 1.0
        for i in range(len(combo)):
            if dd_cut:
                # 降仓 50% 后收益减半，直到净值回到触发时峰值再恢复
                nav_now = float(cum.iloc[i] if i == 0 else (cum.iloc[i - 1] * (1 + combo.iloc[i])))
                nav_now = float(combo.iloc[:i + 1].add(1).prod())
            dd_now = float(nav_now / (dd_peak or nav_now) - 1)
            if dd_cut:
                adj.iloc[i] = combo.iloc[i] * 0.5
                if nav_now >= (dd_peak or 0):
                    dd_cut = False
                    dd_peak = None
            else:
                if dd_now < -0.08:
                    dd_cut = True
                    dd_peak = (dd_peak or 1.0) * (1 + combo.iloc[:i].sum())  # 触发时净值水平
                    adj.iloc[i] = combo.iloc[i] * 0.5
        combo = adj

    combo.name = "port"
    return {"port": combo, "perf": oi_metrics(combo)}


def oi_rank_ic(panel: pd.DataFrame, start: pd.Timestamp = EVAL_START) -> dict:
    """Rank IC：oi_roc40 与 T+1 收益的横截面秩相关（全池，核验用）"""
    p = build_oi_factor(panel)
    f = _pivot_wide(p, "oi_roc40")
    r = _pivot_wide(p, "ret_hold").shift(-1)
    ic = []
    for d in f.index:
        if d < start:
            continue
        a = f.loc[d].dropna()
        b = r.loc[d].reindex(a.index).dropna()
        idx = a.index.intersection(b.index)
        if len(idx) >= 5 and b[idx].std() > 0 and a[idx].std() > 0:
            ic.append(float(a[idx].rank().corr(b[idx].rank())))
    s = pd.Series(ic)
    return {"ic_mean": float(s.mean()), "ic_t": _nw_t(s, 20) if len(s) > 5 else float("nan"),
            "ic_n": len(s)}


# ---------------- 日报栏目 ----------------

def build_oi_report_section(res: dict) -> str:
    """持仓量横截面对冲栏目（Markdown），供日报合并展示"""
    d = res["date"]
    acc = res["acc"]
    orders = res["orders"]
    snap = res["snap"].reset_index(drop=True)
    lines = []
    lines.append(f"## 持仓量横截面对冲 · 当日仓单")
    lines.append(f"> 独立单元：化工 {OI_UNIT_CAPITAL/1e4:.0f}万 + 农产品 {OI_UNIT_CAPITAL/1e4:.0f}万 = "
                 f"**{acc['total_nav']:,.2f}** 元 ｜ 当日盈亏 **{res['pnl_day']:+,.2f}** 元（成本 {res['cost']:,.2f}）"
                 f" ｜ 周频调仓（每周最后交易日）\n")
    # 信号表
    t = snap.dropna(subset=["oi_z"]).copy()
    t = t.sort_values("oi_z", ascending=False)
    lines.append("**本周信号**（OI_ROC40 板块内 z，权重 = z/Σ|z|）：")
    lines.append("| 品种 | 板块 | oi_z | 权重 | 方向 |")
    lines.append("|---|---|---|---|---|")
    for _, r in t.iterrows():
        d_ = "多" if r["oi_z"] > 0 else ("空" if r["oi_z"] < 0 else "—")
        lines.append(f"| {vname(r['variety'])} | {r['sector']} | {float(r['oi_z']):+.2f} | "
                     f"{float(r['oi_w'])*100:.1f}% | {d_} |")
    # 仓单
    lines.append("\n**今日交易动作**：")
    act = []
    for otype, label in (("open", "开仓"), ("reduce", "减仓"), ("close", "平仓"), ("hold", "维持")):
        for od in orders.get(otype, []):
            v = od.get("variety") or "—"
            dirm = "多" if od.get("dir") == 1 else ("空" if od.get("dir") == -1 else "—")
            lots = od.get("_reduced_lots", od.get("lots", 0))
            price = od.get("price") or od.get("entry_price") or 0
            act.append(f"- [{SECTOR_OF.get(v, '—')}] {vname(v)} {dirm} {lots}手 @ {price} ｜ {od.get('reason', '—')}")
    if not act:
        act.append("— 无交易动作 —")
    lines.extend(act)
    # 持仓
    lines.append("\n**当前持仓**：")
    if not acc["positions"]:
        lines.append("_空仓_")
    else:
        lines.append("| 品种 | 板块 | 方向 | 手数 | 开仓价 | 名义/总净值 |")
        lines.append("|---|---|---|---|---|---|")
        for v, pos in sorted(acc["positions"].items()):
            nv = pos["lots"] * pos["entry_price"] * CONTRACT_MULTIPLIER[v]
            lines.append(f"| {vname(v)} | {SECTOR_OF[v]} | {'多' if pos['dir'] > 0 else '空'} | {pos['lots']} | "
                         f"{pos['entry_price']} | {nv / acc['total_nav'] * 100:.1f}% |")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    panel = pd.read_csv(PANEL_FILE, parse_dates=["date"])
    d = pd.Timestamp(sys.argv[1]) if len(sys.argv) > 1 else panel["date"].max()
    res = run_oi_daily(d, panel, force_rebalance=("--reb" in sys.argv),
                       reset=("--reset" in sys.argv))
    print("最新交易日:", d.date(), "| 净值:", round(res["acc"]["total_nav"], 2),
          "| 当日盈亏:", round(res["pnl_day"], 2),
          "| 开:", len(res["orders"]["open"]), "平:", len(res["orders"]["close"]),
          "减:", len(res["orders"]["reduce"]), "维持:", len(res["orders"]["hold"]))