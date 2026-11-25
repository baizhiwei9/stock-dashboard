# -*- coding: utf-8 -*-
"""带风控的横截面对冲单元（对照单元 · 名义敞口口径）
=================================================
与既有横截面对冲单元使用同一套信号（score_adj 机会驱动配对），但在其之上叠加：

  ① 拥挤度开关（来自 sim_trade/crowding）：绿=100%、黄=敞口×0.50、红=敞口×0.25（拥挤期只留基准仓位）
  ② 层1 波动率目标：单品种 w=25%目标波动/实际波动（EWMA θ=0.94），组合级目标 15%
  ③ 层2 杠杆与下注：总敞口≤2.0 倍；分数 Kelly λ=0.40（不足 12 个月历史时不缩放）；近零时保底 25%
  ④ 层3 换手：无交易带 b=0.02；成本单边 0.025%
  ⑤ 层4 状态与回撤：相关性降杠杆（板块内 60 日均相关 > 滚动中位数×1.2 时 ×0.6）；
     200 日均线慢熊规则与 20 日波动快崩规则各 ×0.5

口径说明：本单元只做名义敞口权重与净值推进，不做手数取整与保证金账务，
用于与既有单元并列对照；独立账户、独立落盘（state/risk_account.json）。
参数全部为模块常量，可按用户要求调整。
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_ROOTT = Path(__file__).resolve().parent.parent
if str(_ROOTT) not in sys.path:
    sys.path.insert(0, str(_ROOTT))

from sim_trade.config import (FACTORS_FILE, SECTOR_OF, SECTORS, BASE_LEV, COST_RATE, STATE_DIR, NAME_OF)  # noqa: E402
from sim_trade.engine import build_snapshot, compute_target_positions, biweek_rebalance_dates  # noqa: E402
from sim_trade.crowding import evaluate as crowding_eval  # noqa: E402

UNIT = "带风控横截面对冲"
CAPITAL = 200_000.0
TGT_VOL_NAME, TGT_VOL_PORT, THETA = 0.25, 0.15, 0.94
LEV_CAP, KELLY_LAMBDA, KELLY_FLOOR = 2.0, 0.40, 0.25
BAND, CORR_WIN, CORR_DELEV = 0.02, 60, 0.6
DD_MA, VOL_CRASH = 200, 1.5
NAME_CAP = 0.6
CROWD_SCALE = {"绿": 1.0, "黄": 0.5, "红": 0.25}
STATE = STATE_DIR / "risk_account.json"
HIST = STATE_DIR / "risk_nav_history.csv"
TRADES = STATE_DIR / "risk_trades.csv"      # 逐笔成交流水（开/平/调整）


def _new_acc():
    return {"nav": CAPITAL, "peak": CAPITAL, "weights": {}, "last_reb": None,
            "last_scale": {}, "history": []}


def _load():
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return _new_acc()


def _save(acc):
    STATE.write_text(json.dumps(acc, ensure_ascii=False, indent=1), encoding="utf-8")
    if acc["history"]:
        pd.DataFrame(acc["history"]).to_csv(HIST, index=False, encoding="utf-8-sig")


def crowd_gate(crowd_res):
    """拥挤档位＝横截面四因子中最差者（红 > 黄 > 绿）；样本不足的因子不参与判定"""
    lights = [v["light"] for k, v in crowd_res["factors"].items()
              if k != "动量惩罚项" and not v.get("note")]
    if not lights:
        return "黄"
    return "红" if "红" in lights else ("黄" if "黄" in lights else "绿")


def _append_trades(rows):
    if not rows:
        return
    df = pd.DataFrame(rows)
    df.to_csv(TRADES, mode="a", header=not TRADES.exists(), index=False, encoding="utf-8-sig")


def _monthly_returns(acc, win=24):
    h = pd.DataFrame(acc["history"])
    if len(h) < 40:
        return None
    h["date"] = pd.to_datetime(h["date"])
    m = h.set_index("date")["pnl"].resample("ME").sum().dropna().tail(win)
    return m if len(m) >= 12 else None


def run_daily_risk(d, factors, crowd_res=None, persist=True):
    d = pd.Timestamp(d)
    acc = _load()
    f = factors.copy()
    R = f.pivot_table(index="date", columns="variety", values="ret_hold").sort_index()
    R = R.reindex(columns=[v for v in R.columns if v in SECTOR_OF])
    vol = (R.pow(2).ewm(alpha=1 - THETA, adjust=False).mean() * 252).pow(0.5)
    dates = list(R.index)
    d = d if d in R.index else dates[-1]
    # 幂等：同日重复运行不重复推进（定时任务重跑安全）
    if acc["history"] and acc["history"][-1]["date"] == str(d.date()):
        w0 = pd.Series(acc.get("weights", {}), dtype=float).reindex(R.columns).fillna(0.0)
        return {"unit": UNIT, "date": d, "nav": acc["nav"], "pnl_day": 0.0, "cost": 0.0,
                "orders": acc.get("last_orders", {"open": [], "close": [], "adjust": []}),
                "is_reb": False,
                "switches": acc.get("last_scale", {}), "gross": float(w0.abs().sum()),
                "positions": acc["weights"], "cum": acc["nav"] - CAPITAL, "pnl_yuan": 0.0,
                "start_date": (acc["history"][0]["date"] if acc["history"] else str(d.date())),
                "note": "同日重复运行，未重复推进"}

    w = pd.Series(acc.get("weights", {}), dtype=float).reindex(R.columns).fillna(0.0)
    row = R.loc[d].reindex(R.columns).fillna(0.0)
    nav_before = float(acc["nav"])
    pnl = float((w * row).sum())
    acc["nav"] = float(acc["nav"]) * (1 + pnl)
    acc["peak"] = max(acc["peak"], acc["nav"])

    is_reb = d in biweek_rebalance_dates(dates)
    switches, orders = {}, {"open": [], "close": [], "adjust": []}
    turn, cost = 0.0, 0.0
    if is_reb:
        snap = build_snapshot(f, d)
        target = compute_target_positions(snap, CAPITAL)
        if crowd_res is None:
            crowd_res = crowding_eval(d, f, save=False)
        gate = crowd_gate(crowd_res)
        switches["拥挤度档位"] = gate
        w_new = pd.Series(0.0, index=R.columns)
        if target:
            base = pd.Series({v: t["dir"] * t["weight"] for v, t in target.items()})   # 带方向：多正空负
            s_i = vol.loc[:d].iloc[-1]
            w1 = base * (TGT_VOL_NAME / s_i.reindex(base.index)).clip(upper=5.0)
            for sec in SECTORS:
                idx = [v for v in w1.index if SECTOR_OF.get(v) == sec]
                tot = w1[idx].abs().sum()
                if tot > 0:
                    w1[idx] = w1[idx] * (BASE_LEV / tot)
            w_new[w1.index] = w1
            switches["单品种波动率缩放"] = "已应用(目标25%)"
            # 层1 组合级波动率目标
            S = R.loc[:d].tail(CORR_WIN).cov() * 252
            v = w_new.reindex(R.columns).fillna(0.0).values
            sp = float(np.sqrt(max(v @ S.values @ v, 0.0)))
            scale_p = TGT_VOL_PORT / sp if sp > 1e-8 else 1.0
            # 层2 分数 Kelly
            m = _monthly_returns(acc)
            if m is not None:
                mu, sg = float(m.mean()) * 12, float(m.std()) * np.sqrt(12)
                fk = mu / (sg ** 2) if sg > 1e-8 else 0.0
                scale_k = float(np.clip(KELLY_LAMBDA * fk, KELLY_FLOOR, LEV_CAP))
            else:
                scale_k = 1.0
            # 层4 相关性降杠杆
            cm = R.loc[:d].tail(CORR_WIN).corr().values
            avg_corr = float(cm[np.triu_indices_from(cm, 1)].mean())
            hist_corr_path = STATE_DIR / "risk_corr_history.json"
            hc = json.loads(hist_corr_path.read_text(encoding="utf-8")) if hist_corr_path.exists() else []
            med = float(np.median(hc)) if len(hc) > 12 else np.nan
            scale_c = CORR_DELEV if (med == med and avg_corr > 1.2 * med) else 1.0
            hc.append(avg_corr)
            hist_corr_path.write_text(json.dumps(hc[-2000:]), encoding="utf-8")
            # 层4 回撤规则
            scale_dd = 1.0
            h = pd.DataFrame(acc["history"])
            if len(h) > DD_MA:
                navs = pd.to_numeric(h["nav"], errors="coerce")
                if acc["nav"] < navs.tail(DD_MA).mean():
                    scale_dd *= 0.5
                    switches["200日均线慢熊"] = "触发×0.5"
            rv20 = float((pd.Series([x["pnl"] for x in acc["history"][-20:]]).std() or 0) * np.sqrt(252))
            if rv20 > VOL_CRASH * TGT_VOL_PORT:
                scale_dd *= 0.5
                switches["波动率快崩"] = "触发×0.5"
            scale_cw = CROWD_SCALE[gate]
            w_new = w_new * scale_p * scale_k * scale_c * scale_dd * scale_cw
            switches.update({"组合波动率目标": f"×{scale_p:.2f}", "分数Kelly": f"×{scale_k:.2f}",
                             "相关性降杠杆": f"×{scale_c:.2f}", "回撤规则": f"×{scale_dd:.2f}",
                             "拥挤度缩放": f"×{scale_cw:.2f}"})
            w_new = w_new.clip(-NAME_CAP, NAME_CAP)
            gr = w_new.abs().sum()
            if gr > LEV_CAP:
                w_new = w_new * (LEV_CAP / gr)
                switches["杠杆上限"] = "总敞口受 2.0 倍约束"
            # 层3 无交易带
            keep = (w_new - w).abs() < BAND
            w_new[keep] = w[keep]
        turn = float((w_new - w).abs().sum())
        cost = turn * COST_RATE
        acc["nav"] = float(acc["nav"]) * (1.0 - cost)     # 换手成本按名义占比扣减净值
        pnl = pnl - cost
        for v in R.columns:
            a, b = float(w.get(v, 0.0)), float(w_new.get(v, 0.0))
            if abs(a - b) < 1e-9:
                continue
            rec = {"variety": v, "sector": SECTOR_OF.get(v), "from": round(a, 4), "to": round(b, 4),
                   "notional": round(b * acc["nav"], 0)}
            if a == 0 and b != 0:
                orders["open"].append(rec)
            elif b == 0 and a != 0:
                orders["close"].append(rec)
            else:
                orders["adjust"].append(rec)
        acc["weights"] = {k: round(float(v2), 4) for k, v2 in w_new.items() if abs(v2) > 1e-9}
        acc["last_reb"] = str(d.date())
        acc["last_scale"] = switches
        acc["last_orders"] = orders
        rows = []
        for otype, label in (("open", "开仓"), ("close", "平仓"), ("adjust", "调整")):
            for od in orders.get(otype, []):
                pos_w = od["to"] if od["to"] != 0 else od["from"]
                rows.append({"日期": str(d.date()), "板块": od.get("sector"), "品种": od["variety"],
                             "品种名称": NAME_OF.get(od["variety"], od["variety"]),
                             "动作": label, "方向": "多" if pos_w > 0 else "空",
                             "上期权重": od["from"], "本期权重": od["to"],
                             "权重变动": round(od["to"] - od["from"], 4),
                             "名义(元)": od["notional"], "拥挤档位": gate,
                             "组合波动目标": switches.get("组合波动率目标", ""),
                             "分数Kelly": switches.get("分数Kelly", ""),
                             "拥挤度缩放": switches.get("拥挤度缩放", "")})
        if persist:
            _append_trades(rows)
        w = w_new
    else:
        switches = acc.get("last_scale", {})
        cost = 0.0

    acc["history"].append({"date": str(d.date()), "nav": round(acc["nav"], 2), "pnl": round(pnl, 6),
                           "turnover": round(turn, 4), "cost": round(cost, 5),
                           "gross": round(float(w.abs().sum()), 4), "is_reb": bool(is_reb),
                           "crowd": switches.get("拥挤度档位", "")})
    if persist:
        _save(acc)
    return {"unit": UNIT, "date": d, "nav": acc["nav"], "pnl_day": pnl, "cost": cost,
            "orders": orders, "is_reb": bool(is_reb), "switches": switches,
            "gross": float(w.abs().sum()), "positions": acc["weights"],
            "cum": acc["nav"] - CAPITAL, "pnl_yuan": nav_before * pnl,
            "start_date": (acc["history"][0]["date"] if acc["history"] else str(d.date()))}


def reset():
    """清零本单元账务：从下一次运行起重新起算（本金 20 万），不动既有单元"""
    for p in (STATE, HIST, TRADES, STATE_DIR / "risk_corr_history.json"):
        if p.exists():
            p.unlink()
    print("已重置：带风控横截面对冲单元账面清零，本金 200,000，盈亏自下次运行起算")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="清零本单元账务（从下次运行起算）")
    a = ap.parse_args()
    if a.reset:
        reset()
        return
    f = pd.read_csv(FACTORS_FILE, parse_dates=["date"])
    d = f["date"].max()
    res = run_daily_risk(d, f)
    out = {k: v for k, v in res.items() if k != "orders"}
    out["date"] = str(pd.Timestamp(res["date"]).date())
    out["成交明细"] = res["orders"]
    print(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"开仓 {len(res['orders']['open'])} 平仓 {len(res['orders']['close'])} "
          f"调整 {len(res['orders']['adjust'])}｜自起算累计 {res['cum']:+,.2f}")


if __name__ == "__main__":
    main()