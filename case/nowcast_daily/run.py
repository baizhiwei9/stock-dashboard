#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""nowcast_daily —— 美国 Nowcast 日更（GDP / 通胀实时预测）

每天 22:00（Asia/Shanghai；对应美东 9–10 点，覆盖克利夫兰 9 点更新）跑一次：

1. 从 DataHub EDB 取 10 条 nowcast 序列（官方端点直采，不从别的任务读产物）；
2. 算「较一周前 / 较上一目标月」的变动，写 ``out/points.json``；
3. 变动超过阈值就发一封邮件（dws 主通道 + 163 SMTP 回退），同一数据日幂等；
4. 写 ``out/report.md`` 供人看，指标由 pipeline 的 emit 阶段落 DataHub 指标仓。

看板「宏观」和「流动性 → 利率」两处都读同一批 ``nowcast.*`` 指标（见
DataHub ``data_hub/tech/registry.py`` 的 nowcast 域），不各自落一份。

口径提醒（与 EDB registry 一致）
--------------------------------
- GDPNow / NY Fed 只保留**当前季度**轨迹，季度切换后序列重置；
- WEI 是四季度同比口径，不能和 GDPNow（环比折年率）直接比大小；
- 克利夫兰是目标月最后一版 nowcast，月中会修订。

用法
----
    python3 run.py                 # 正常跑（联网刷新 + 按阈值发邮件）
    python3 run.py --no-refresh    # 只读 EDB 缓存（离线排查）
    python3 run.py --dry-run       # 不发邮件、不写幂等状态
    python3 run.py --force-email   # 忽略幂等，强制重发
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
for _p in (ROOT, os.path.join(HERE, "..", "_lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from datahub_path import datahub_root  # noqa: E402

DATAHUB = datahub_root(ROOT)
if DATAHUB not in sys.path:
    sys.path.insert(0, DATAHUB)

import mail  # noqa: E402

OUT = os.environ.get("CASE_OUT_DIR") or os.path.join(HERE, "out")
SEC = "US.NOWCAST"

#: 序列清单。``window``：7=较 7 天前，None=较上一目标月；``thr``=邮件触发阈值（pp）。
SERIES: Tuple[Dict[str, Any], ...] = (
    {"sid": "US.NOWCAST.GDPNOW", "key": "nowcast.gdpnow", "name": "GDPNow",
     "unit": "%", "dec": 2, "group": "GDP", "window": 7,
     "chg_key": "nowcast.gdpnow.chg_1w", "thr": 0.5, "chg_label": "周"},
    {"sid": "US.NOWCAST.NYFED", "key": "nowcast.nyfed", "name": "纽约联储 Nowcast",
     "unit": "%", "dec": 2, "group": "GDP", "window": 7,
     "chg_key": "nowcast.nyfed.chg_1w", "thr": 0.5, "chg_label": "周"},
    {"sid": "US.NOWCAST.NYFED.80_LB", "key": "nowcast.nyfed.lb", "name": "NY Fed 80% 下限",
     "unit": "%", "dec": 2, "group": "GDP"},
    {"sid": "US.NOWCAST.NYFED.80_UB", "key": "nowcast.nyfed.ub", "name": "NY Fed 80% 上限",
     "unit": "%", "dec": 2, "group": "GDP"},
    {"sid": "US.NOWCAST.WEI", "key": "nowcast.wei", "name": "WEI",
     "unit": "%", "dec": 2, "group": "GDP", "window": 7,
     "chg_key": "nowcast.wei.chg_1w", "thr": 1.0, "chg_label": "周"},
    {"sid": "US.NOWCAST.CORE_PCE.YOY", "key": "nowcast.core_pce_yoy", "name": "核心 PCE 同比",
     "unit": "%", "dec": 2, "group": "通胀", "window": None,
     "chg_key": "nowcast.core_pce_yoy.chg_1m", "thr": 0.2, "chg_label": "月"},
    {"sid": "US.NOWCAST.CPI.YOY", "key": "nowcast.cpi_yoy", "name": "CPI 同比",
     "unit": "%", "dec": 2, "group": "通胀", "window": None,
     "chg_key": "nowcast.cpi_yoy.chg_1m", "thr": 0.3, "chg_label": "月"},
    {"sid": "US.NOWCAST.CORE_CPI.YOY", "key": "nowcast.core_cpi_yoy", "name": "核心 CPI 同比",
     "unit": "%", "dec": 2, "group": "通胀"},
    {"sid": "US.NOWCAST.PCE.YOY", "key": "nowcast.pce_yoy", "name": "PCE 同比",
     "unit": "%", "dec": 2, "group": "通胀"},
    {"sid": "US.NOWCAST.CORE_PCE.QANN", "key": "nowcast.core_pce_qann", "name": "核心 PCE 季折年",
     "unit": "%", "dec": 2, "group": "通胀"},
)

STATE = os.path.join(OUT, "alert_state.json")


# ================================================================
# 纯计算（单测直接调）
# ================================================================

def _to_date(text: Any) -> Optional[dt.date]:
    """ISO 日期文本 → date；解析不了返回 None。"""
    try:
        return dt.datetime.strptime(str(text)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def measure(rows: Sequence[Tuple[str, float]], window: Optional[int]) -> Dict[str, Any]:
    """算最新值、基期值和变动。

    Args:
        rows: ``[(YYYY-MM-DD, value)]``，升序；调用方保证已去重。
        window: ``7`` 表示较 7 天前最近一个观测；``None`` 表示较上一目标月。

    Returns:
        dict: ``date`` / ``value`` / ``base_date`` / ``base_value`` / ``delta``。
        基期缺失或间隔过久时 ``delta=None``（不拿跨季度的水平差冒充单次变动）。
    """
    if not rows:
        return {"date": None, "value": None, "base_date": None,
                "base_value": None, "delta": None}
    date, value = rows[-1]
    base_date = base_value = None

    if window is None:
        if len(rows) >= 2:
            base_date, base_value = rows[-2]
            gap = (_to_date(date) - _to_date(base_date)).days if _to_date(date) and _to_date(base_date) else None
            if gap is None or gap > 70:          # 月频正常 28~31 天；>70 天视为断档
                base_date = base_value = None
    else:
        latest = _to_date(date)
        if latest is not None:
            cutoff = latest - dt.timedelta(days=int(window))
            candidates = [row for row in rows[:-1] if (_to_date(row[0]) or latest) <= cutoff]
            if candidates:
                base_date, base_value = candidates[-1]
                gap = (latest - _to_date(base_date)).days
                if gap > int(window) * 2:        # 周频允许 14 天内；再久视为断档
                    base_date = base_value = None

    delta = None
    if base_value is not None and value is not None:
        delta = float(value) - float(base_value)
    return {"date": date, "value": value, "base_date": base_date,
            "base_value": base_value, "delta": delta}


def build_points(metrics: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把测量结果转成 ``out/points.json`` 的标准指标（publish 简写格式）。

    季度折年序列的观测日是**目标季末**（可能在未来），落库前夹到今天：
    指标仓的 freshness 检查按「观测日距今」算，未来日期会让它误判为最新；
    真实目标季仍保留在 ``report.md`` 与 EDB 序列里。
    """
    today = dt.date.today().isoformat()

    def _obs(value: Optional[str]) -> str:
        text = str(value or today)[:10]
        return min(text, today)

    points: List[Dict[str, Any]] = []
    for item in metrics:
        spec = item["spec"]
        value = item.get("value")
        points.append({
            "key": spec["key"], "sec": SEC, "name": spec["name"],
            "unit": spec["unit"], "dec": spec["dec"],
            "value": value, "obs_date": _obs(item.get("date")),
            "quality": "ok" if value is not None else "missing",
        })
        if spec.get("chg_key"):
            delta = item.get("delta")
            points.append({
                "key": spec["chg_key"], "sec": SEC,
                "name": spec["name"] + "·" + spec["chg_label"] + "变动",
                "unit": "pp", "dec": 2, "value": delta,
                "obs_date": _obs(item.get("date")),
                "quality": "ok" if delta is not None else "missing",
            })
    return points


def pick_triggers(metrics: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """挑出 |变动| 达到阈值的序列（按绝对变动降序）。"""
    out = []
    for item in metrics:
        spec = item["spec"]
        delta = item.get("delta")
        if spec.get("thr") and delta is not None and abs(delta) >= spec["thr"]:
            out.append(item)
    out.sort(key=lambda item: abs(item["delta"]), reverse=True)
    return out


# ================================================================
# 取数
# ================================================================

def fetch_series(series_id: str, refresh: bool) -> Tuple[List[Tuple[str, float]], Optional[str]]:
    """取一条 EDB 序列；失败返回 ``([], 原因)``，不抛异常。"""
    try:
        from data_hub import edb
        result = edb.get_series_result(series_id, force_refresh=refresh)
    except Exception as exc:  # noqa: BLE001
        return [], "%s: %s" % (type(exc).__name__, exc)

    if not result.get("ok"):
        return [], str(result.get("error") or "取数失败")[:160]
    frame = result.get("data")
    if frame is None or len(frame) == 0:
        return [], "无数据"

    rows: List[Tuple[str, float]] = []
    for record in frame.to_dict("records"):
        try:
            rows.append((str(record["date"])[:10], float(record["value"])))
        except (KeyError, TypeError, ValueError):
            continue
    rows.sort()
    return rows, None


def collect(refresh: bool = True) -> List[Dict[str, Any]]:
    """按 :data:`SERIES` 逐条取数并测量。"""
    metrics: List[Dict[str, Any]] = []
    for spec in SERIES:
        rows, error = fetch_series(spec["sid"], refresh)
        measured = measure(rows, spec.get("window"))
        measured.update({"spec": spec, "error": error, "rows": rows})
        metrics.append(measured)
    return metrics


# ================================================================
# 输出
# ================================================================

def _fmt(value: Optional[float], digits: int = 2, suffix: str = "") -> str:
    return "—" if value is None else ("%." + str(digits) + "f%s") % (value, suffix)


def render_report(metrics: Sequence[Dict[str, Any]], triggers: Sequence[Dict[str, Any]],
                  generated_at: str) -> str:
    """生成人读的 ``out/report.md``。"""
    lines = ["# 美国 Nowcast 日报 · %s" % generated_at[:10], ""]
    lines.append("数据源：亚特兰大 GDPNow / 纽约联储 Staff Nowcast / 达拉斯 WEI / "
                 "克利夫兰联储 Inflation Nowcasting（DataHub EDB `US.NOWCAST.*`）。")
    lines.append("")

    if triggers:
        lines.append("## 触发（|变动| 达阈值）")
        for item in triggers:
            spec = item["spec"]
            lines.append("- **%s** %s（基期 %s）→ 变动 %spp"
                         % (spec["name"],
                            _fmt(item["value"], spec["dec"], "%"),
                            item.get("base_date") or "—",
                            _fmt(item["delta"], 2, "")))
        lines.append("")
    else:
        lines.append("## 触发")
        lines.append("本期无 |变动| 达阈值的序列。")
        lines.append("")

    for group in ("GDP", "通胀"):
        lines.append("## %s" % group)
        lines.append("| 指标 | 最新 | 观测日 | 较%s |" % ("一周前" if group == "GDP" else "上月"))
        lines.append("|---|---|---|---|")
        for item in metrics:
            spec = item["spec"]
            if spec["group"] != group:
                continue
            change = "—" if item.get("delta") is None else "%+.2fpp" % item["delta"]
            lines.append("| %s | %s | %s | %s |"
                         % (spec["name"], _fmt(item["value"], spec["dec"], "%"),
                            item.get("date") or "—", change))
        lines.append("")

    errors = [item for item in metrics if item.get("error")]
    if errors:
        lines.append("## 缺失")
        for item in errors:
            lines.append("- %s：%s" % (item["spec"]["name"], item["error"]))
        lines.append("")

    lines += [
        "## 口径",
        "- 观测日=官方模型更新日；GDPNow / NY Fed 只保留当前季度轨迹，季度切换会重置，"
        "跨季度不可直接连成增长曲线。",
        "- WEI 是四季度同比口径，不能和 GDPNow（环比折年率）直接比大小。",
        "- 克利夫兰是目标月最后一版 nowcast，月中会随发布修订。",
    ]
    return "\n".join(lines) + "\n"


def build_mail(metrics: Sequence[Dict[str, Any]], triggers: Sequence[Dict[str, Any]],
               generated_at: str) -> Tuple[str, str]:
    """构造邮件标题与正文。"""
    if triggers:
        brief = "、".join("%s %+.2fpp" % (item["spec"]["name"], item["delta"])
                         for item in triggers[:3])
        subject = "【Nowcast】美国 GDP/通胀实时预测：%s" % brief
    else:
        subject = "【Nowcast】美国 GDP/通胀实时预测 %s" % generated_at[:10]

    lines = [render_report(metrics, triggers, generated_at)]
    lines.append("")
    lines.append("—— 来自 DoMate · 美国 Nowcast 日更")
    return subject, "\n".join(lines)


def load_state() -> Dict[str, str]:
    try:
        with open(STATE, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state: Dict[str, str]) -> None:
    os.makedirs(OUT, exist_ok=True)
    trimmed = dict(sorted(state.items(), key=lambda kv: kv[1])[-200:])
    temp = STATE + ".tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(trimmed, handle, ensure_ascii=False, indent=2)
    os.replace(temp, STATE)


def _state_key(item: Dict[str, Any]) -> str:
    return "%s|%s" % (item["spec"]["sid"], item.get("date") or "")


# ================================================================
# 主流程
# ================================================================

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="美国 Nowcast 日更")
    parser.add_argument("--no-refresh", action="store_true",
                        help="只读 EDB 缓存，不联网刷新")
    parser.add_argument("--dry-run", action="store_true",
                        help="不发邮件、不写幂等状态")
    parser.add_argument("--force-email", action="store_true",
                        help="忽略幂等，强制重发")
    args = parser.parse_args(argv)

    generated_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    metrics = collect(refresh=not args.no_refresh)
    triggers = pick_triggers(metrics)
    points = build_points(metrics)

    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "points.json"), "w", encoding="utf-8") as handle:
        json.dump({"as_of": max((p["obs_date"] for p in points), default=generated_at[:10]),
                   "points": points}, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    report = render_report(metrics, triggers, generated_at)
    with open(os.path.join(OUT, "report.md"), "w", encoding="utf-8") as handle:
        handle.write(report)

    ok_count = sum(1 for p in points if p["quality"] == "ok")
    print("[nowcast] 指标 %d/%d ok；触发 %d 项" % (ok_count, len(points), len(triggers)))
    for item in triggers:
        print("  [触发] %s %+.2fpp（基期 %s）"
              % (item["spec"]["name"], item["delta"], item.get("base_date") or "—"))

    if not triggers and not args.force_email:
        print("[nowcast] 无触发，不发邮件")
        return 0

    state = load_state()
    if args.force_email:
        pending = list(triggers)
    else:
        pending = [item for item in triggers if _state_key(item) not in state]
        if not pending:
            print("[nowcast] 触发项已发过（幂等），不重复发送")
            return 0

    subject, body = build_mail(metrics, pending, generated_at)
    sent, info = mail.send(subject, body, dry_run=args.dry_run)
    print("[nowcast] 邮件：%s（%s）" % ("成功" if sent else "失败", info))

    if sent and not args.dry_run:
        for item in pending:
            state[_state_key(item)] = generated_at
        save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
