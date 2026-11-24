# -*- coding: utf-8 -*-
"""tda_spd 的框架适配：策略计算 → 报告 → 指标 → 飞书分发

    fetch     跑策略（取 22 个申万行业指数 + 股债分析），结果落 data/strategy.json
    check     框架通用校验
    render    生成飞书卡片 + 本地摘要 out/report.md
    emit      仓位、市场状态、beta0/beta1、Top5 行业权重 → out/latest.json
    dispatch  推飞书卡片 + SPD 行业旋转轨迹图（任务自己的推送）

原 run_daily_22f.py 保留为手动入口；定时调度走 runner。策略本身没改一行，
只是把"算完直接发"拆成"算 → 存 → 出报告 → 出指标 → 发"。
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def fetch(ctx):
    """取数并完成策略计算（原脚本 fetch 与计算是一体的，这里一次做完存下来）。"""
    import run_daily_22f as R
    ctx.log("运行 TDA-SPD 策略（不推送）...")
    result = R.run_daily_22f(trade_date=None, send_feishu=False, select_stocks=True)
    if not result:
        raise RuntimeError("策略没有产出（非交易日或行业指数数据不足）")

    sig = result["signal"]
    market = sig.get("market") or {}
    payload = {
        "trade_date": result["trade_date"],
        "market": {k: market.get(k) for k in
                   ("state", "rotation_state", "vulnerability", "position_ratio", "position_pct")},
        "features": {k: _num(v) for k, v in (sig.get("features") or {}).items()},
        "weights": {k: _num(v) for k, v in (sig.get("weights") or {}).items()},
        "total_drift": _num(sig.get("total_drift")),
        "bond": _brief_bond(result.get("bond")),
        "stocks": {k: [s.get("name") for s in (v or [])]
                   for k, v in (sig.get("stocks") or {}).items()},
    }
    os.makedirs(ctx.data_dir, exist_ok=True)
    p = os.path.join(ctx.data_dir, "strategy.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    ctx.result = result
    ctx.payload = payload
    ctx.trade_date = str(result["trade_date"])
    ctx.log("交易日 %s，状态 %s，仓位 %.1f%%"
            % (ctx.trade_date, market.get("rotation_state") or market.get("state"),
               market.get("position_pct") or 0))


def render(ctx):
    """生成推送卡片（只构造不发送），并在 out/ 落一份可读摘要。"""
    result = getattr(ctx, "result", None)
    if not result:
        raise RuntimeError("render 阶段拿不到策略结果（fetch 未执行或失败）")
    from feishu_pusher_22f import generate_daily_report_card_22f

    card = generate_daily_report_card_22f(result["signal"], result.get("bond"),
                                          result.get("pnl"), result["trade_date"])
    ctx.card = card

    p = ctx.payload
    lines = ["# TDA-SPD 黎曼流形策略日报 %s" % ctx.trade_date, ""]
    lines.append("- 市场状态：%s" % (p["market"].get("rotation_state") or p["market"].get("state")))
    lines.append("- 脆弱性：%s%%" % p["market"].get("vulnerability"))
    lines.append("- 建议仓位：%s%%" % p["market"].get("position_pct"))
    lines.append("- β0=%s，β1=%s，曲率=%s"
                 % (p["features"].get("beta_0"), p["features"].get("beta_1"),
                    p["features"].get("curvature")))
    lines.append("")
    lines.append("## 行业权重（Top5 集中）")
    for f, w in list(p["weights"].items())[:5]:
        lines.append("- %s：%.1f%%" % (f, (w or 0) * 100))
    if p.get("bond"):
        lines.append("")
        lines.append("## 债券端")
        lines.append("- 方向：%s" % p["bond"].get("direction_str"))
        lines.append("- 价格：%s，止损：%s" % (p["bond"].get("bond_price"), p["bond"].get("stop_price")))
    os.makedirs(ctx.out_dir, exist_ok=True)
    with open(os.path.join(ctx.out_dir, "report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    ctx.log("卡片已生成，摘要写入 out/report.md")


def emit(ctx):
    """指标：仓位、状态、几何特征、Top5 行业权重。"""
    p = getattr(ctx, "payload", None)
    if not p:
        raise RuntimeError("emit 阶段拿不到策略结果（fetch 未执行或失败）")
    obs = _iso(ctx.trade_date)
    sec = "CN.STRATEGY"
    market, feats, weights = p["market"], p["features"], p["weights"]
    top = list(weights.items())[:5]
    points = [
        {"key": "tda_spd.position_pct", "sec": sec, "obs_date": obs,
         "name": "TDA-SPD 建议仓位", "value": market.get("position_pct"), "unit": "%", "dec": 1,
         "quality": "ok" if market.get("position_pct") is not None else "missing"},
        {"key": "tda_spd.market_state", "sec": sec, "obs_date": obs,
         "name": "市场旋转状态", "value_text": market.get("rotation_state") or market.get("state"),
         "quality": "ok" if (market.get("rotation_state") or market.get("state")) else "missing"},
        {"key": "tda_spd.vulnerability", "sec": sec, "obs_date": obs,
         "name": "拓扑脆弱性", "value": market.get("vulnerability"), "unit": "%", "dec": 1,
         "quality": "ok" if market.get("vulnerability") is not None else "missing"},
        {"key": "tda_spd.beta0", "sec": sec, "obs_date": obs, "name": "β0",
         "value": feats.get("beta_0"), "dec": 4,
         "quality": "ok" if feats.get("beta_0") is not None else "missing"},
        {"key": "tda_spd.beta1", "sec": sec, "obs_date": obs, "name": "β1",
         "value": feats.get("beta_1"), "dec": 4,
         "quality": "ok" if feats.get("beta_1") is not None else "missing"},
        {"key": "tda_spd.top_industry", "sec": sec, "obs_date": obs,
         "name": "第一权重行业", "value_text": top[0][0] if top else "",
         "quality": "ok" if top else "missing"},
    ]
    for f, w in top:
        points.append({"key": "tda_spd.weight.%s" % _slug(f), "sec": sec, "obs_date": obs,
                       "name": "%s 权重" % f, "value": round((w or 0) * 100, 2), "unit": "%",
                       "dec": 2, "quality": "ok" if w is not None else "missing"})
    return points


def dispatch(ctx):
    """任务自己的推送：飞书卡片；旋转轨迹图作为附带，失败不阻断。"""
    from feishu_pusher_22f import send_feishu_card
    if not getattr(ctx, "card", None):
        raise RuntimeError("dispatch 阶段没有卡片（render 未执行或失败）")
    ctx.log("推送飞书卡片 ...")
    if not send_feishu_card(ctx.card):
        raise RuntimeError("飞书卡片推送失败")

    rot = os.path.join(HERE, "push_spd_rotation_daily.py")
    if os.path.isfile(rot):
        ctx.log("生成并推送 SPD 行业旋转轨迹图（失败不阻断）...")
        ctx.sh("%s -u %s" % (_q(sys.executable), _q(rot)), timeout=900, check=False)


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _iso(d: str) -> str:
    d = str(d or "")
    if len(d) == 8 and d.isdigit():
        return "%s-%s-%s" % (d[:4], d[4:6], d[6:])
    return d


def _slug(s: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in str(s)).strip("_")[:24] or "x"


def _brief_bond(b):
    if not b:
        return None
    keep = ("direction_str", "bond_price", "stop_price", "direction")
    return {k: b.get(k) for k in keep if k in b}


def _q(s):
    return '"%s"' % str(s).replace('"', '\\"')