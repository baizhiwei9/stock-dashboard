#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rate_board —— 中美利率与政策预期（看板「流动性 → 利率」子页的数据源）

一次跑完两件事：
  1) 美国侧：CME FedWatch 加息/降息概率（DataHub EDB `US.FEDWATCH.NEXT.*`，
     采集器 data_hub/edb/fetchers/cme_fedwatch.py）+ EFFR / 目标区间上下限；
  2) 中国侧：降准降息预期利差（国开/存单 1Y-3M、3M 国开-逆回购政策利率）、
     LPR、存款准备金率，以及土狗地产日报的框架读数（中性利率 / 贴现率 / 状态机）。

数据全部经 DataHub / 上游任务产物，不直连行情源；取不到的项写 missing，
不补零、不用旧值顶替。

产出：
    out/latest.json    标准指标（publish.py 落 DataHub 指标仓）
    out/fedwatch.json  FedWatch 目标区间概率矩阵（看板「利率」子页直接读）
    out/report.md      文字摘要
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
OUT = os.path.join(HERE, "out")
SEC = "RATE.BOARD"

DH = os.environ.get("DATAHUB_ROOT") or os.path.join(WS, "projects", "datahub-multi-tier")
if DH not in sys.path:
    sys.path.insert(0, DH)
for _p in (os.path.join(WS, "pylibs"), os.path.join(WS, "py_deps")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

FEEDWATCH_JSON = os.path.join(DH, "data_hub", "edb", "data", "fedwatch", "latest.json")
TUGOU_LATEST = os.path.join(HERE, "..", "realestate_daily", "out", "latest.json")

POINTS: list = []


def _read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def add(key, name, value=None, text=None, unit="", dec=2, obs="", note="", quality=None):
    """登记一条指标（value 与 text 二选一）。"""
    p = {"key": key, "sec": SEC, "name": name, "obs_date": obs}
    if unit:
        p["unit"] = unit
    if text is not None:
        p["value_text"] = str(text)
        p["quality"] = quality or "ok"
    elif value is None:
        p["quality"] = quality or "missing"
    else:
        p["value"] = float(value)
        p["dec"] = dec
        p["quality"] = quality or "ok"
    if note:
        p["note"] = note
    POINTS.append(p)
    return p


def edb_latest(sid):
    """取 EDB 序列最新值 → (date, value, source)；失败返回 (None, None, err)。"""
    try:
        from data_hub import edb
        r = edb.get_series_result(sid)
    except Exception as exc:                                    # noqa: BLE001
        return None, None, "edb异常: %s" % exc
    if not r.get("ok"):
        return None, None, str(r.get("error") or "取数失败")[:120]
    return r.get("latest_date"), r.get("latest_value"), r.get("source")


def us_block() -> dict:
    """美国：FedWatch 概率 + 有效利率 + 目标区间。返回矩阵 dict（供看板）。"""
    matrix = _read_json(FEEDWATCH_JSON, {}) or {}
    obs = matrix.get("fetch_date") or ""

    for sid, key, name in (
        ("US.FEDWATCH.NEXT.HIKE_PROB", "rate.us.fedwatch.hike_next", "FedWatch下次会议加息概率"),
        ("US.FEDWATCH.NEXT.HOLD_PROB", "rate.us.fedwatch.hold_next", "FedWatch下次会议维持不变概率"),
        ("US.FEDWATCH.NEXT.EASE_PROB", "rate.us.fedwatch.ease_next", "FedWatch下次会议降息概率"),
        ("US.FEDWATCH.NEXT.TARGET_MID", "rate.us.fedwatch.target_mid", "FedWatch隐含目标利率中值"),
        ("US.FEDWATCH.NEXT.DAYS", "rate.us.fedwatch.days", "距下次FOMC会议天数"),
    ):
        d, v, src = edb_latest(sid)
        add(key, name, value=v, unit="%" if "DAYS" not in sid else "天",
            dec=1 if "PROB" in sid else (2 if "MID" in sid else 0),
            obs=d or obs, note="" if v is not None else str(src))

    if matrix.get("meeting_date"):
        add("rate.us.fedwatch.meeting", "下次FOMC会议日",
            text=matrix["meeting_date"], obs=obs,
            note="CME FedWatch（30天联邦基金期货定价），数据时点 %s CT" % (matrix.get("asof_ct") or "?"))

    # 1 天/1 周/1 月前的加息概率（用矩阵行里同口径列求和，和页面顶部 Ease/Hold/Hike 同源）
    rows = matrix.get("rows") or []
    cur = next((r for r in rows if r.get("current")), None)
    if rows and cur:
        lo_cur = int(cur["range"].split("-")[0])
        for field, key, nm in (("d1", "rate.us.fedwatch.hike_prev_1d", "加息概率·1天前"),
                               ("w1", "rate.us.fedwatch.hike_prev_1w", "加息概率·1周前"),
                               ("m1", "rate.us.fedwatch.hike_prev_1m", "加息概率·1月前")):
            v = sum(r[field] for r in rows if int(r["range"].split("-")[0]) > lo_cur)
            add(key, nm, value=v, unit="%", dec=1, obs=obs)

    for sid, key, name, dec in (
        ("LIQ.US.EFFR", "rate.us.effr", "联邦基金有效利率EFFR", 3),
        ("US.FED.TARGET.UPPER", "rate.us.target_upper", "联邦基金目标区间上限", 2),
        ("US.FED.TARGET.LOWER", "rate.us.target_lower", "联邦基金目标区间下限", 2),
        ("RATE.US.2Y", "rate.us.2y", "美债2年（近24个月利率预期锚）", 3),
    ):
        d, v, src = edb_latest(sid)
        add(key, name, value=v, unit="%", dec=dec, obs=d,
            note="" if v is not None else str(src))

    return matrix


def cn_block():
    """中国：降准降息预期（1Y-3M 利差）+ 政策利率 + 土狗框架。"""
    for sid, key, name, unit, dec in (
        ("BOND.SPREAD.CN.CDB.1Y_3M", "rate.cn.cdb_1y3m", "国开债1Y-3M利差（降准降息预期核心）", "bp", 1),
        ("BOND.SPREAD.CN.NCD.1Y_3M", "rate.cn.ncd_1y3m", "同业存单1Y-3M利差（同上印证）", "bp", 1),
        ("BOND.SPREAD.CN.CDB3M_POLICY", "rate.cn.cdb3m_policy", "3M国开-隔夜逆回购政策利率", "bp", 1),
        ("LIQ.CN.OMO.POLICY", "rate.cn.omo_policy", "隔夜逆回购操作利率", "%", 2),
        ("CN.RRR", "rate.cn.rrr", "存款准备金率（大型机构）", "%", 2),
        ("LIQ.CN.LPR.5Y", "rate.cn.lpr5y", "LPR 5年期", "%", 2),
        ("LIQ.CN.LPR.1Y", "rate.cn.lpr1y", "LPR 1年期", "%", 2),
    ):
        d, v, src = edb_latest(sid)
        add(key, name, value=v, unit=unit, dec=dec, obs=d,
            note="" if v is not None else str(src))

    # 土狗地产日报框架（中性利率 5.5% / 贴现率 / 状态机）——只读上游任务产物
    tg = _read_json(os.path.normpath(TUGOU_LATEST), {}) or {}
    pts = {p.get("key"): p for p in (tg.get("points") or [])}
    for src_key, key, name, unit, dec in (
        ("re.neutral_rate", "rate.cn.tugou_neutral", "土狗中性利率", "%", 2),
        ("re.discount_rate", "rate.cn.tugou_discount", "风险资产贴现率（FFTR+1Y掉期年化）", "%", 3),
        ("re.kill_spread", "rate.cn.tugou_kill_spread", "按揭-租金收益率利差", "pp", 2),
    ):
        p = pts.get(src_key) or {}
        add(key, name, value=p.get("value"), unit=unit, dec=dec,
            obs=p.get("obs_date") or "", note="" if p.get("value") is not None else "土狗任务无该指标")
    p = pts.get("re.tugou_state") or {}
    add("rate.cn.tugou_state", "土狗状态机", text=p.get("value_text") or "—",
        obs=p.get("obs_date") or "", quality="ok" if p.get("value_text") else "missing")
def write_report(matrix, asof):
    os.makedirs(OUT, exist_ok=True)
    L = ["# 中美利率与政策预期（%s）" % (asof or "—"), ""]
    if matrix:
        L.append("## 美国 · CME FedWatch")
        L.append("- 下次会议 **%s**（距今 %.0f 天），数据时点 %s CT" % (
            matrix.get("meeting_date"), matrix.get("days") or 0, matrix.get("asof_ct") or "?"))
        L.append("- 加息 **%.1f%%** / 不变 %.1f%% / 降息 %.1f%%；隐含目标中值 %.3f%%" % (
            matrix.get("hike") or 0, matrix.get("hold") or 0,
            matrix.get("ease") or 0, matrix.get("target_mid") or 0))
        cur = next((r for r in (matrix.get("rows") or []) if r.get("current")), None)
        if cur:
            L.append("- 当前区间 %s%%（概率 %.1f%%）" % (cur["range"], cur["now"]))
        L.append("")
        L.append("| 目标区间(bp) | 现在 | 1天前 | 1周前 | 1月前 |")
        L.append("|---|---|---|---|---|")
        for r in matrix.get("rows") or []:
            if max(r["now"], r["d1"], r["w1"], r["m1"]) <= 0:
                continue
            L.append("| %s%s | %.1f%% | %.1f%% | %.1f%% | %.1f%% |" % (
                r["range"], "（当前）" if r["current"] else "",
                r["now"], r["d1"], r["w1"], r["m1"]))
        L.append("")
    L.append("## 中国 · 降准降息预期（1Y-3M 利差口径）")
    for p in POINTS:
        if p["key"].startswith("rate.cn.") and p.get("quality") == "ok":
            if p.get("value_text") is not None:
                L.append("- %s：%s" % (p["name"], p["value_text"]))
            else:
                L.append("- %s：%s%s" % (p["name"], round(p["value"], p.get("dec", 2)), p.get("unit", "")))
    L.append("")
    with open(os.path.join(OUT, "report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    matrix = us_block()
    cn_block()
    asof = matrix.get("fetch_date") or max((p.get("obs_date") or "" for p in POINTS), default="")
    with open(os.path.join(OUT, "fedwatch.json"), "w", encoding="utf-8") as f:
        json.dump(matrix, f, ensure_ascii=False, indent=1)
    # 指标同时落 points.json，供 pipeline.py 的 emit 钩子原样取回（避免重复取数）
    with open(os.path.join(OUT, "points.json"), "w", encoding="utf-8") as f:
        json.dump({"as_of": asof, "points": POINTS}, f, ensure_ascii=False, indent=1)
    write_report(matrix, asof)
    ok = sum(1 for p in POINTS if p.get("quality") == "ok")
    print("rate_board: %d 条指标（ok %d）as_of=%s" % (len(POINTS), ok, asof))
    for p in POINTS:
        print("  %-34s %s" % (p["key"], p.get("value", p.get("value_text"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())