#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""nowcast_daily 的离线单测：变动计算 / 指标与报告产出。

不联网、不读 EDB：只测 ``run.py`` 里的纯函数。跑法：
    cd <stock-dashboard 根>
    python3 -m pytest case/nowcast_daily/test_run.py -q
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import run  # noqa: E402


def test_measure_weekly_picks_last_observation_before_cutoff():
    """周变动取「7 天前最近一个观测」，不是简单取上一行。"""
    rows = [("2026-11-01", 3.2), ("2026-11-08", 3.3), ("2026-11-15", 3.4)]
    got = run.measure(rows, 7)
    assert got["date"] == "2026-11-15"
    assert got["base_date"] == "2026-11-08"
    assert round(got["delta"], 6) == 0.1


def test_measure_monthly_uses_previous_target():
    """月频没有「7 天前」的点，退回上一目标月。"""
    rows = [("2026-10-01", 3.10), ("2026-11-01", 3.32)]
    got = run.measure(rows, None)
    assert got["base_date"] == "2026-10-01"
    assert round(got["delta"], 6) == 0.22


def test_measure_long_gap_does_not_fake_change():
    """断档超过两倍窗口时不给变动，避免把跨季度水平差当单次下修。"""
    rows = [("2026-09-01", 1.0), ("2026-11-20", 3.0)]
    got = run.measure(rows, 7)
    assert got["base_date"] is None
    assert got["delta"] is None


def test_measure_empty_and_single_row():
    """空序列/单点不报错，变动为 None。"""
    assert run.measure([], 7)["delta"] is None
    assert run.measure([("2026-11-20", 3.0)], 7)["delta"] is None


def _metric(spec, value, delta, date="2026-11-20", rows=None):
    return {"spec": spec, "value": value, "delta": delta, "date": date,
            "rows": rows if rows is not None else [(date, value)]}


def test_build_points_covers_all_registered_keys():
    """10 个水平 + 5 个变动 = 15 条，键名与 DataHub 登记表一致。"""
    metrics = [_metric(spec, 1.0, 0.1) for spec in run.SERIES]
    points = run.build_points(metrics)
    assert len(points) == 15
    keys = {point["key"] for point in points}
    assert "nowcast.gdpnow" in keys
    assert "nowcast.core_pce_yoy.chg_1m" in keys
    assert all(point["sec"] == "US.NOWCAST" for point in points)


def test_build_points_pins_quarter_ann_to_revision_day():
    """季折年不能用未来目标季末，也不能每天写一条同值新观测。"""
    qann = next(s for s in run.SERIES if s["key"] == "nowcast.core_pce_qann")
    rows = [("2026-11-19", 3.38), ("2026-11-20", 3.38), ("2026-11-21", 3.38)]
    point = run.build_points([_metric(qann, 3.38, None, date="2026-12-31", rows=rows)])[0]
    # 这一版 3.38 是从 11-19 开始的 → 观测日固定在那一天，反复跑不新增行
    assert point["obs_date"] == "2026-11-19"


def test_build_points_pins_to_first_day_of_same_value_run_only():
    """值一旦变了，观测日跟着换到新版第一天。"""
    qann = next(s for s in run.SERIES if s["key"] == "nowcast.core_pce_qann")
    rows = [("2026-11-19", 3.30), ("2026-11-20", 3.30), ("2026-11-21", 3.38)]
    point = run.build_points([_metric(qann, 3.38, None, date="2026-12-31", rows=rows)])[0]
    assert point["obs_date"] == "2026-11-21"


def test_report_has_no_threshold_section():
    """报告只展示读数与变动；阈值判定归 DataHub nowcast_alert。"""
    metrics = [_metric(spec, 1.0, 0.0) for spec in run.SERIES]
    text = run.render_report(metrics, "2026-11-25 22:00")
    assert "GDPNow" in text
    assert "核心 PCE 同比" in text
    assert "较上次取数" in text
    assert "触发" not in text


def test_mail_is_manual_digest_only():
    """邮件是人工日报：标题带日期，不含触发项列表。"""
    metrics = [_metric(spec, 1.0, 0.0) for spec in run.SERIES]
    subject, body = run.build_mail(metrics, "2026-11-25 22:00")
    assert "2026-11-25" in subject
    assert "美国 Nowcast 日报" in body


def test_dws_cli_absent_is_not_fatal(monkeypatch):
    """dws 找不到时返回明确错误，不抛异常（由 SMTP 回退接管）。"""
    monkeypatch.setattr(run.mail, "DWS_CLI", "")
    ok, info = run.mail._dws("s", "b", "a@b.c", ["c@d.e"])
    assert ok is False
    assert "dws" in info