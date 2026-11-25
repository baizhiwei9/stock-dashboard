#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""nowcast_daily 的离线单测：变动计算 / 触发挑选 / 指标与报告产出。

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


def _metric(spec, value, delta, date="2026-11-20"):
    return {"spec": spec, "value": value, "delta": delta, "date": date}


def test_pick_triggers_uses_per_series_threshold():
    """每条序列用自己的阈值；未达阈值不进邮件。"""
    gdpnow = next(s for s in run.SERIES if s["key"] == "nowcast.gdpnow")
    wei = next(s for s in run.SERIES if s["key"] == "nowcast.wei")
    metrics = [
        _metric(gdpnow, 2.5, -0.6),   # 阈值 0.5 → 触发
        _metric(wei, 3.0, -0.8),      # 阈值 1.0 → 不触发
    ]
    got = run.pick_triggers(metrics)
    assert [item["spec"]["key"] for item in got] == ["nowcast.gdpnow"]


def test_build_points_covers_all_registered_keys():
    """10 个水平 + 5 个变动 = 15 条，键名与 DataHub 登记表一致。"""
    metrics = []
    for spec in run.SERIES:
        metrics.append(_metric(spec, 1.0, 0.1))
    points = run.build_points(metrics)
    assert len(points) == 15
    keys = {point["key"] for point in points}
    assert "nowcast.gdpnow" in keys
    assert "nowcast.core_pce_yoy.chg_1m" in keys
    assert all(point["sec"] == "US.NOWCAST" for point in points)


def test_build_points_clamps_future_quarter_end():
    """核心 PCE 季折年的目标季末在未来，落库观测日必须夹到今天。"""
    qann = next(s for s in run.SERIES if s["key"] == "nowcast.core_pce_qann")
    metrics = [_metric(qann, 3.38, None, date="2026-12-31")]
    point = run.build_points(metrics)[0]
    assert point["obs_date"] == run.dt.date.today().isoformat()


def test_mail_subject_lists_top_triggers():
    """邮件标题带前三个触发项，方便手机通知栏直接看。"""
    gdpnow = next(s for s in run.SERIES if s["key"] == "nowcast.gdpnow")
    metrics = [_metric(gdpnow, 2.2, -0.9)]
    subject, body = run.build_mail(metrics, metrics, "2026-11-25 22:00")
    assert "GDPNow" in subject
    assert "-0.90pp" in subject
    assert "美国 Nowcast 日报" in body


def test_report_marks_no_trigger():
    """无触发时报告明确写「无」，不留空。"""
    metrics = [_metric(spec, 1.0, 0.0) for spec in run.SERIES]
    text = run.render_report(metrics, [], "2026-11-25 22:00")
    assert "本期无" in text
    assert "GDPNow" in text
    assert "核心 PCE 同比" in text
