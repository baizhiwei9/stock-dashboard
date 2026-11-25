#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""nowcast_daily —— 美国 Nowcast 日更（GDP / 通胀实时预测）

每天 22:00（Asia/Shanghai；对应美东 9–10 点，覆盖克利夫兰 9 点更新）跑一次：

1. 从 DataHub EDB 取 10 条 nowcast 序列（官方端点直采，不从别的任务读产物）；
2. 算「较一周前 / 较上一目标月」的展示用变动，写 ``out/points.json``；
3. 写 ``out/report.md`` 供人看，指标由 pipeline 的 emit 阶段落 DataHub 指标仓。

**这个任务不发预警邮件。** 预警只有一条链路：DataHub 的
``python -m data_hub.edb.nowcast_alert``，它的口径是「最新值 − 上一次取数时的值」
（上一天 / 上一交易日，即上次真正取数），命中阈值才发。这里再套一套 7 天窗口的
阈值就会变成两套口径、两封邮件，故本任务只负责取数与落库；``--force-email``
仅用于人工要一份日报时手动触发。

看板「宏观」和「流动性 → 利率」两处都读同一批 ``nowcast.*`` 指标（见
DataHub ``data_hub/tech/registry.py`` 的 nowcast 域），不各自落一份。

口径提醒（与 EDB registry 一致）
--------------------------------
- GDPNow / NY Fed 只保留**当前季度**轨迹，季度切换后序列重置；
- WEI 是四季度同比口径，不能和 GDPNow（环比折年率）直接比大小；
- 克利夫兰是目标月最后一版 nowcast，月中会修订；季折年的观测日不是目标季末
  （那是未来日期），而是**这次修订真正落地的日期**，见 :func:`_pin_obs`。

用法
----
    python3 run.py                 # 正常跑（联网刷新 + 落库产物）
    python3 run.py --no-refresh    # 只读 EDB 缓存（离线排查）
    python3 run.py --dry-run       # 不发邮件（强制日报时也不发）
    python3 run.py --force-email   # 人工要一份日报：把当前读数发一封邮件
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

#: 序列清单。
#: ``window``：7=较 7 天前（展示用），None=较上一目标月（展示用）。
#: ``chg_key``：对应的变动指标键；``pin_change_date``：观测日不取目标期末，
#: 而是取「本次修订实际落地日」（季折年这种目标期末在未来的序列用）。
SERIES: Tuple[Dict[str, Any], ...] = (
    {"sid": "US.NOWCAST.GDPNOW", "key": "nowcast.gdpnow", "name": "GDPNow",
     "unit": "%", "dec": 2, "group": "GDP", "window": 7,
     "chg_key": "nowcast.gdpnow.chg_1w"},
    {"sid": "US.NOWCAST.NYFED", "key": "nowcast.nyfed", "name": "纽约联储 Nowcast",
     "unit": "%", "dec": 2, "group": "GDP", "window": 7,
     "chg_key": "nowcast.nyfed.chg_1w"},
    {"sid": "US.NOWCAST.NYFED.80_LB", "key": "nowcast.nyfed.lb", "name": "NY Fed 80% 下限",
     "unit": "%", "dec": 2, "group": "GDP"},
    {"sid": "US.NOWCAST.NYFED.80_UB", "key": "nowcast.nyfed.ub", "name": "NY Fed 80% 上限",
     "unit": "%", "dec": 2, "group": "GDP"},
    {"sid": "US.NOWCAST.WEI", "key": "nowcast.wei", "name": "WEI",
     "unit": "%", "dec": 2, "group": "GDP", "window": 7,
     "chg_key": "nowcast.wei.chg_1w"},
    {"sid": "US.NOWCAST.CORE_PCE.YOY", "key": "nowcast.core_pce_yoy", "name": "核心 PCE 同比",
     "unit": "%", "dec": 2, "group": "通胀", "window": None,
     "chg_key": "nowcast.core_pce_yoy.chg_1m"},
    {"sid": "US.NOWCAST.CPI.YOY", "key": "nowcast.cpi_yoy", "name": "CPI 同比",
     "unit": "%", "dec": 2, "group": "通胀", "window": None,
     "chg_key": "nowcast.cpi_yoy.chg_1m"},
    {"sid": "US.NOWCAST.CORE_CPI.YOY", "key": "nowcast.core_cpi_yoy", "name": "核心 CPI 同比",
     "unit": "%", "dec": 2, "group": "通胀"},
    {"sid": "US.NOWCAST.PCE.YOY", "key": "nowcast.pce_yoy", "name": "PCE 同比",
     "unit": "%", "dec": 2, "group": "通胀"},
    {"sid": "US.NOWCAST.CORE_PCE.QANN", "key": "nowcast.core_pce_qann", "name": "核心 PCE 季折年",
     "unit": "%", "dec": 2, "group": "通胀", "pin_change_date": True},
)


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
    """算最新值、基期值和变动（仅用于展示，不参与预警判定）。

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


def _pin_obs(rows: Sequence[Tuple[str, float]], value: Optional[float],
             today: str) -> str:
    """本次修订实际落地的观测日（末尾同值连续段的第一天），不超过今天。

    季折年的目标季末在未来（例如 2026-12-31），直接落库会让 freshness 把
    未来日期当最新；夹到今天又会每天写一条同值「新观测」——Q 频序列长成日频、
    数据被伪造。取「这次数值是哪天开始出现的」两件事都解决：同一版修订反复
    跑会落到同一个 obs_date，仓储里是覆盖而不是新增。
    """
    if value is None or not rows:
        return today
    date = str(rows[-1][0])[:10]
    for row_date, row_value in reversed(list(rows[:-1])):
        try:
            same = abs(float(row_value) - float(value)) <= 1e-9
        except (TypeError, ValueError):
            break
        if not same:
            break
        date = str(row_date)[:10]
    return min(date, today)


def _obs(item: Dict[str, Any], today: str) -> str:
    """一条指标的落库观测日。"""
    text = str(item.get("date") or today)[:10]
    if item["spec"].get("pin_change_date"):
        return _pin_obs(item.get("rows") or [], item.get("value"), today)
    return min(text, today)


def build_points(metrics: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把测量结果转成 ``out/points.json`` 的标准指标（publish 简写格式）。

    观测日：普通序列取模型更新日、未来日期夹到今天；``pin_change_date`` 的
    序列（核心 PCE 季折年）取本次修订实际落地日，见 :func:`_pin_obs`。
    """
    today = dt.date.today().isoformat()

    points: List[Dict[str, Any]] = []
    for item in metrics:
        spec = item["spec"]
        value = item.get("value")
        obs = _obs(item, today)
        points.append({
            "key": spec["key"], "sec": SEC, "name": spec["name"],
            "unit": spec["unit"], "dec": spec["dec"],
            "value": value, "obs_date": obs,
            "quality": "ok" if value is not None else "missing",
        })
        if spec.get("chg_key"):
            delta = item.get("delta")
            label = "较一周前" if spec.get("window") == 7 else "较上月"
            points.append({
                "key": spec["chg_key"], "sec": SEC,
                "name": spec["name"] + "·" + label + "变动",
                "unit": "pp", "dec": 2, "value": delta,
                "obs_date": obs,
                "quality": "ok" if delta is not None else "missing",
            })
    return points


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


def render_report(metrics: Sequence[Dict[str, Any]], generated_at: str) -> str:
    """生成人读的 ``out/report.md``。"""
    lines = ["# 美国 Nowcast 日报 · %s" % generated_at[:10], ""]
    lines.append("数据源：亚特兰大 GDPNow / 纽约联储 Staff Nowcast / 达拉斯 WEI / "
                 "克利夫兰联储 Inflation Nowcasting（DataHub EDB `US.NOWCAST.*`）。")
    lines.append("")
    lines.append("预警：本任务不判阈值；预警由 DataHub `nowcast_alert` 以「较上次取数」口径统一发出。")
    lines.append("")

    for group in ("GDP", "通胀"):
        label = "较一周前" if group == "GDP" else "较上月"
        lines.append("## %s" % group)
        lines.append("| 指标 | 最新 | 观测日 | %s |" % label)
        lines.append("|---|---|---|---|")
        for item in metrics:
            spec = item["spec"]
            if spec["group"] != group:
                continue
            change = "—" if item.get("delta") is None else "%+.2fpp" % item["delta"]
            lines.append("| %s | %s | %s | %s |"
                         % (spec["name"], _fmt(item["value"], spec["dec"], "%"),
                            _obs(item, generated_at[:10]), change))
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
        "- 克利夫兰是目标月最后一版 nowcast，月中会随发布修订；核心 PCE 季折年的观测日"
        "取本次修订实际落地日（目标季末在未来，不直接当观测日）。",
        "- 表里的「较一周前 / 较上月」只用于展示；预警口径是「较上次取数」。",
    ]
    return "\n".join(lines) + "\n"


def build_mail(metrics: Sequence[Dict[str, Any]], generated_at: str) -> Tuple[str, str]:
    """构造人工日报邮件的标题与正文（只在 ``--force-email`` 时用）。"""
    subject = "【Nowcast】美国 GDP/通胀实时预测 %s" % generated_at[:10]
    lines = [render_report(metrics, generated_at)]
    lines.append("")
    lines.append("—— 来自 DoMate · 美国 Nowcast 日更（人工日报）")
    return subject, "\n".join(lines)


# ================================================================
# 主流程
# ================================================================

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="美国 Nowcast 日更")
    parser.add_argument("--no-refresh", action="store_true",
                        help="只读 EDB 缓存，不联网刷新")
    parser.add_argument("--dry-run", action="store_true",
                        help="不发邮件（--force-email 时也不发）")
    parser.add_argument("--force-email", action="store_true",
                        help="人工要一份日报：把当前读数发一封邮件（不做阈值判定）")
    args = parser.parse_args(argv)

    generated_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    metrics = collect(refresh=not args.no_refresh)
    points = build_points(metrics)

    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "points.json"), "w", encoding="utf-8") as handle:
        json.dump({"as_of": max((p["obs_date"] for p in points), default=generated_at[:10]),
                   "points": points}, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    report = render_report(metrics, generated_at)
    with open(os.path.join(OUT, "report.md"), "w", encoding="utf-8") as handle:
        handle.write(report)

    ok_count = sum(1 for p in points if p["quality"] == "ok")
    print("[nowcast] 指标 %d/%d ok；%d 条缺失"
          % (ok_count, len(points), len(points) - ok_count))

    if not args.force_email:
        print("[nowcast] 已落库并写 report.md；预警由 DataHub nowcast_alert 统一发出")
        return 0

    subject, body = build_mail(metrics, generated_at)
    sent, info = mail.send(subject, body, dry_run=args.dry_run)
    print("[nowcast] 日报邮件：%s（%s）" % ("成功" if sent else "失败", info))
    return 0


if __name__ == "__main__":
    sys.exit(main())