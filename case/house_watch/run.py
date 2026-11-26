# -*- coding: utf-8 -*-
"""京沪楼市监控：取数 + 告警判定（只做数据，不出图不发信）。

用户口径（2026-11-25）
--------------------
- 只盯**北京、上海**；
- 指标：成交面积、成交价（用**挂牌均价**替代）、挂牌数量；
- 任一指标变化 **>10%** 告警；KDJ/MACD 沿用旧口径；**VWAP 突破 ±1σ/±2σ** 也告警；
- 图与邮件在私有任务 `house_watch_chart`（本任务只写 `data/market.json` 快照）。

数据来源与口径
-------------
- 成交面积/套数：网签（`data_hub.house.fetch_all`，北京住建委 + 上海开源归档），**无成交金额**；
- 挂牌均价/挂牌数量：中国房价行情（`creprice_source.py`，移动端才有挂牌套数）；
- 价格序列：70 城房价指数的环比连乘还原形状，按最新挂牌均价锚定 → **近似单价**，
  实测月单独标出（`real_months`），不把回溯段说成逐月实测。

产物
----
- ``data/market.json``   图表任务消费的快照（价格/通道/KDJ/MACD/周度/挂牌/告警）
- ``data/creprice.json`` 挂牌价与挂牌量的月度累积（每跑一次追加当期）
- ``out/latest.json``   本期摘要；``out/alerts.json`` 告警与信号；``out/report.md`` 人读版
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
OUT = os.path.join(HERE, "out")

sys.path.insert(0, HERE)
import creprice_source  # noqa: E402
import indicators as ind  # noqa: E402

try:
    from data_hub import house
except ImportError as exc:                      # 部署机没装 datahub 时给明确提示
    print("[FATAL] 无法 import data_hub.house：%s" % exc, file=sys.stderr)
    print("        需先装 datahub-multi-tier（pip install -e . 或设 PYTHONPATH）", file=sys.stderr)
    raise SystemExit(1)

#: city code → (中文名, 成交口径说明)
CITY_MAP = {"BJ": ("北京", "北京存量房网签（不含商业）"),
            "SH": ("上海", "上海二手网签（含商业车位）")}
WATCH = tuple(CITY_MAP)
VWAP_WINDOW = 12
OVERBOUGHT = (80.0, 100.0)          # K ≥80 或 J ≥100 视为超买
OVERSOLD = (20.0, 0.0)


def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=False)
    os.replace(tmp, path)
    return path


# ────────────────────────────── 取数
def fetch_market(cities=("北京", "上海")):
    """网签（京沪二手）→ 返回 data_hub 的原始结果，失败不影响后续。"""
    try:
        return house.fetch_all(sync_sh=True)
    except Exception as exc:
        return {"cities": {}, "error": "%s: %s" % (type(exc).__name__, exc)}


def fetch_index(verbose=False):
    """70 城房价指数（月度）。它是指数不是价格，这里只用它的环比形状。"""
    try:
        return house.fetch_price(verbose=verbose)
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}


def fetch_creprice(cities=("北京", "上海")):
    """挂牌均价 + 挂牌数量，并与历史合并成月度累积表。"""
    got = creprice_source.fetch(cities=cities)
    hist = _load_json(os.path.join(DATA, "creprice.json"), {})
    for city, rec in (got.get("cities") or {}).items():
        slot = hist.setdefault(city, {})
        slot[rec["month"]] = {**rec, "fetched": got.get("fetched_at")}
    if got.get("cities"):
        _save_json(os.path.join(DATA, "creprice.json"), hist)
    return got, hist


# ────────────────────────────── 判定
def change_items(city, label, basis, threshold, weekly, creprice_hist):
    """三类变化告警：周度面积/套数、月度挂牌价、月度挂牌量。"""
    out = []
    full = [w for w in weekly if not w.get("partial") and (w.get("days") or 0) >= 6]
    if len(full) >= 2:
        cur, prev = full[-1], full[-2]
        for metric, key, unit in (("成交面积", "area", "㎡"), ("成交套数", "deals", "套")):
            ch = ind.pct(cur.get(key), prev.get(key))
            if ch is not None and abs(ch) > threshold:
                out.append({"kind": "change", "code": key, "city": city, "label": label,
                            "metric": metric, "unit": unit, "latest": cur.get(key),
                            "prev": prev.get(key), "change_pct": ch,
                            "period": "周 %s" % cur.get("label"),
                            "basis": basis, "basis_detail": "上周 %s" % prev.get("label")})

    tbl = creprice_hist.get(label) or {}
    months = sorted(tbl)
    if months:
        last = tbl[months[-1]] or {}
        ch, detail = None, ""
        if len(months) >= 2:
            ch = ind.pct(last.get("price_yuan"), (tbl[months[-2]] or {}).get("price_yuan"))
            detail = "上期 %s" % months[-2]
        if ch is None:
            ch, detail = last.get("mom_pct"), "源披露环比"
        if ch is not None and abs(ch) > threshold:
            out.append({"kind": "change", "code": "price", "city": city, "label": label,
                        "metric": "挂牌均价", "unit": "元/㎡",
                        "latest": last.get("price_yuan"), "prev": None, "change_pct": ch,
                        "period": "月 %s（挂牌口径）" % months[-1],
                        "basis": "中国房价行情 挂牌均价", "basis_detail": detail})
        lst = [(m, (tbl[m] or {}).get("listings")) for m in months
               if (tbl[m] or {}).get("listings")]
        if len(lst) >= 2:
            ch = ind.pct(lst[-1][1], lst[-2][1])
            if ch is not None and abs(ch) > threshold:
                out.append({"kind": "change", "code": "listings", "city": city,
                            "label": label, "metric": "挂牌数量", "unit": "套",
                            "latest": lst[-1][1], "prev": lst[-2][1], "change_pct": ch,
                            "period": "月 %s" % lst[-1][0],
                            "basis": "中国房价行情 月度挂牌套数",
                            "basis_detail": "上期 %s" % lst[-2][0]})
    return out


def signal_items(city, label, months, level, bands, k, d, j, dif, dea, hist):
    """技术信号：KDJ 超买超卖/中轴、MACD 零轴与金死叉、VWAP ±1σ/±2σ 新穿越。"""
    out = []
    if len(level) < 2:
        return out
    p, i = -2, -1
    if k[p] < 50 <= k[i]:
        out.append({"kind": "tech", "code": "kdj_mid_cross_up", "city": city,
                    "label": label, "text": "KDJ 中轴(50)上穿", "period": "月 %s" % months[i]})
    if k[p] > 50 >= k[i]:
        out.append({"kind": "tech", "code": "kdj_mid_cross_down", "city": city,
                    "label": label, "text": "KDJ 中轴(50)下破", "period": "月 %s" % months[i]})
    prev_ob = k[p] >= OVERBOUGHT[0] or j[p] >= OVERBOUGHT[1]
    cur_ob = k[i] >= OVERBOUGHT[0] or j[i] >= OVERBOUGHT[1]
    prev_os = k[p] <= OVERSOLD[0] or j[p] <= OVERSOLD[1]
    cur_os = k[i] <= OVERSOLD[0] or j[i] <= OVERSOLD[1]
    if cur_ob and not prev_ob:
        out.append({"kind": "tech", "code": "kdj_overbought", "city": city, "label": label,
                    "text": "KDJ 进入超买区（K=%.1f J=%.1f）" % (k[i], j[i]),
                    "period": "月 %s" % months[i]})
    if cur_os and not prev_os:
        out.append({"kind": "tech", "code": "kdj_oversold", "city": city, "label": label,
                    "text": "KDJ 进入超卖区（K=%.1f J=%.1f）" % (k[i], j[i]),
                    "period": "月 %s" % months[i]})
    if dif[p] <= 0 < dif[i]:
        out.append({"kind": "tech", "code": "macd_zero_up", "city": city, "label": label,
                    "text": "MACD 零轴上穿", "period": "月 %s" % months[i]})
    if dif[p] >= 0 > dif[i]:
        out.append({"kind": "tech", "code": "macd_zero_down", "city": city, "label": label,
                    "text": "MACD 零轴下破", "period": "月 %s" % months[i]})
    if dif[p] <= dea[p] and dif[i] > dea[i]:
        out.append({"kind": "tech", "code": "macd_golden", "city": city, "label": label,
                    "text": "MACD 金叉", "period": "月 %s" % months[i]})
    if dif[p] >= dea[p] and dif[i] < dea[i]:
        out.append({"kind": "tech", "code": "macd_death", "city": city, "label": label,
                    "text": "MACD 死叉", "period": "月 %s" % months[i]})

    z = [ind.zscore(v, w, s) for v, w, s in zip(level, bands["vwap"], bands["sigma"])]
    if z[-1] is not None and z[-2] is not None:
        for kk, tag in ((1, "1σ"), (2, "2σ")):
            if z[-2] < kk <= z[-1]:
                out.append({"kind": "vwap", "code": "vwap_up_%d" % kk, "city": city,
                            "label": label, "text": "价格上穿 +%s（VWAP 通道）" % tag,
                            "period": "月 %s" % months[-1]})
            if z[-2] > -kk >= z[-1]:
                out.append({"kind": "vwap", "code": "vwap_down_%d" % kk, "city": city,
                            "label": label, "text": "价格下穿 -%s（VWAP 通道）" % tag,
                            "period": "月 %s" % months[-1]})
    return out


# ────────────────────────────── 快照
def build_snapshot(threshold=10.0, with_index=True, with_creprice=True, verbose=False):
    asof = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    steps = {}
    if with_index:
        steps["index"] = fetch_index(verbose=verbose)
    if with_creprice:
        got, hist = fetch_creprice()
        steps["creprice"] = got
    else:
        hist = _load_json(os.path.join(DATA, "creprice.json"), {})

    snap = {"asof": asof, "threshold": threshold, "window": VWAP_WINDOW,
            "cities": {}, "alerts": [], "signals": [], "notes": [], "steps": {}}

    for code in WATCH:
        label, basis = CITY_MAP[code]
        series = house.price(label, months=None)
        weekly = house.weekly(code, weeks=13)
        if len(series) < 24:
            snap["notes"].append("%s：70 城指数不足 24 期，未出图" % label)
            continue
        months = [s["month"] for s in series]
        level = ind.level_from_mom(series)
        cream = ((hist.get(label) or {}) if isinstance(hist, dict) else {})
        anchor_month = sorted(cream)[-1] if cream else None
        anchor_yuan = (cream.get(anchor_month) or {}).get("price_yuan") if anchor_month else None
        scale = (anchor_yuan / level[-1]) if anchor_yuan else 1.0

        deals = {m["month"]: m.get("deals") for m in house.monthly(code, months=240)}
        weights = [deals.get(m) or 0 for m in months]
        bands = ind.vwap_bands(level, weights, window=VWAP_WINDOW)
        k, d, j = ind.kdj(level)
        dif, dea, hist_h = ind.macd(level)
        listing = [{"month": m, "listings": (cream[m] or {}).get("listings"),
                    "price_yuan": (cream[m] or {}).get("price_yuan")}
                   for m in sorted(cream) if (cream[m] or {}).get("listings")]

        snap["cities"][code] = {
            "label": label, "basis": basis,
            "price": {
                "months": months,
                "values": [None if v is None else round(v * scale) for v in level],
                "anchor": {"month": anchor_month, "price_yuan": anchor_yuan},
                "real_months": [m for m in cream if (cream[m] or {}).get("price_yuan")],
                "window": VWAP_WINDOW, "weighted": bands["weighted"],
                "bands": {kk: [None if v is None else round(v * scale, 1)
                               for v in bands[kk]]
                          for kk in ("vwap", "upper1", "lower1", "upper2", "lower2", "sigma")},
            },
            "kdj": {"k": k, "d": d, "j": j,
                    "last": {"k": k[-1], "d": d[-1], "j": j[-1]}},
            "macd": {"dif": dif, "dea": dea, "hist": hist_h},
            "weekly": [{"label": w.get("label"), "start": w.get("start"),
                        "end": w.get("end"), "days": w.get("days"),
                        "deals": w.get("deals"), "area": w.get("area"),
                        "partial": w.get("partial")} for w in weekly],
            "listing": listing,
        }
        snap["alerts"].extend(change_items(code, label, basis, threshold, weekly, hist))
        snap["signals"].extend(signal_items(code, label, months, level, bands,
                                            k, d, j, dif, dea, hist_h))
    snap["steps"] = {k: (v if not isinstance(v, dict) else
                         {"ok": v.get("ok"), "error": v.get("error")})
                     for k, v in steps.items()}
    return snap


# ────────────────────────────── 输出
def render_report(snap):
    lines = ["# 京沪楼市监控（%s）" % snap["asof"], "",
             "阈值：变化 >%g%%；VWAP ±1σ/±2σ 新穿越；KDJ 80/20、MACD 零轴与金死叉"
             % snap["threshold"], ""]
    for code, c in (snap.get("cities") or {}).items():
        p = c["price"]
        last = p["values"][-1] if p["values"] else None
        lines.append("## %s（%s）" % (c["label"], c["basis"]))
        lines.append("- 挂牌均价（锚 %s）：%s 元/㎡%s"
                     % (p.get("anchor", {}).get("month") or "-",
                        "-" if last is None else format(int(last), ","),
                        "（实测月 %s）" % "、".join(p.get("real_months") or []) or ""))
        wk = [w for w in c["weekly"] if not w.get("partial")]
        if wk:
            w = wk[-1]
            lines.append("- 周成交：%s　%s ㎡ / %s 套" %
                         (w.get("label"), format(round(w.get("area") or 0, 2), ","),
                          format(w.get("deals") or 0, ",")))
        if c.get("listing"):
            l = c["listing"][-1]
            lines.append("- 挂牌数量：%s 套（%s）" % (format(l["listings"], ","), l["month"]))
        lines.append("")
    chg = [a for a in snap["alerts"]]
    lines.append("## 变化告警（>%g%%）" % snap["threshold"])
    lines += ["- %s %s：%s → %+.2f%%（%s）"
              % (a["label"], a["metric"],
                 "n/a" if a.get("latest") is None else format(a["latest"], ","),
                 a["change_pct"], a["period"]) for a in chg] or ["- 无"]
    lines.append("")
    lines.append("## 技术信号")
    lines += ["- %s %s：%s" % (s["label"], s["period"], s["text"])
              for s in snap["signals"]] or ["- 无"]
    lines.append("")
    lines.append("## 口径")
    lines.append("- 成交=网签（北京不含商业、上海含商业车位，京沪不可横比）；网签无成交金额，推不出成交单价。")
    lines.append("- 价格=挂牌均价（要价口径）；红点为实测月，更早为 70 城指数形态回溯，非逐月实测。")
    lines.append("- 挂牌数量=中国房价行情移动端月度挂牌套数。")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="京沪楼市监控取数与判定")
    ap.add_argument("--threshold", type=float, default=10.0)
    ap.add_argument("--skip-net", action="store_true", help="不抓网签（用库内已有）")
    ap.add_argument("--skip-index", action="store_true", help="不抓 70 城指数")
    ap.add_argument("--skip-creprice", action="store_true", help="不抓挂牌价/量（用本地累积）")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args(argv)

    steps = {}
    if not a.skip_net:
        res = fetch_market()
        steps["market"] = {c: v for c, v in (res.get("cities") or {}).items()
                           if isinstance(v, dict)}
        if res.get("error"):
            steps["market_error"] = res["error"]

    snap = build_snapshot(threshold=a.threshold, with_index=not a.skip_index,
                          with_creprice=not a.skip_creprice, verbose=a.verbose)
    snap["steps"].update({k: v for k, v in steps.items() if k != "market_error"})
    if steps.get("market_error"):
        snap["notes"].append("网签取数失败：%s" % steps["market_error"])

    os.makedirs(DATA, exist_ok=True)
    os.makedirs(OUT, exist_ok=True)
    _save_json(os.path.join(DATA, "market.json"), snap)
    _save_json(os.path.join(OUT, "latest.json"),
               {"asof": snap["asof"], "cities": list(snap["cities"]),
                "alerts": len(snap["alerts"]), "signals": len(snap["signals"]),
                "threshold": snap["threshold"], "steps": snap["steps"],
                "notes": snap["notes"]})
    _save_json(os.path.join(OUT, "alerts.json"),
               {"asof": snap["asof"], "alerts": snap["alerts"], "signals": snap["signals"]})
    with open(os.path.join(OUT, "report.md"), "w", encoding="utf-8") as f:
        f.write(render_report(snap))
    print(render_report(snap))
    return 0 if snap["cities"] else 1


if __name__ == "__main__":
    raise SystemExit(main())