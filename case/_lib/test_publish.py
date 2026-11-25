#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""publish.normalize 风险标签透传的回归测试（不连库）

    python3 case/_lib/test_publish.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import publish  # noqa: E402


def _one(point, obs="2026-11-23"):
    rows, errs = publish.normalize([point], obs)
    return rows, errs


def test_passthrough():
    rows, errs = _one({"key": "x.signal", "sec": "CN.STOCK", "value_text": "卖出",
                       "quality": "ok", "risk_level": "risk",
                       "risk_note": "仓位降至 20%"})
    assert not errs, errs
    assert rows[0]["risk_level"] == "risk"
    assert rows[0]["risk_note"] == "仓位降至 20%"


def test_native_fields():
    rows, errs = _one({"metric_key": "x.v", "security_id": "000300.SH",
                       "value_num": 1.0, "quality": "ok",
                       "risk_level": "opportunity", "risk_note": "双底共振"})
    assert not errs, errs
    assert rows[0]["risk_level"] == "opportunity"
    assert rows[0]["risk_note"] == "双底共振"


def test_aliases_and_case():
    for raw, want in (("warning", "risk"), ("WARN", "risk"), ("预警", "risk"),
                      ("严重", "alert"), ("告警", "alert"), ("机会", "opportunity"),
                      ("none", "none")):
        rows, errs = _one({"key": "x", "value": 1, "quality": "ok",
                           "risk_level": raw})
        assert not errs, (raw, errs)
        assert rows[0]["risk_level"] == want, (raw, rows[0])


def test_absent_and_empty():
    rows, errs = _one({"key": "x", "value": 1, "quality": "ok"})
    assert not errs, errs
    assert "risk_level" not in rows[0] and "risk_note" not in rows[0]

    rows, errs = _one({"key": "x", "value": 1, "quality": "ok",
                       "risk_level": "none", "risk_note": ""})
    assert not errs, errs
    assert rows[0]["risk_level"] == "none"
    assert "risk_note" not in rows[0]


def test_unknown_level_fails():
    rows, errs = _one({"key": "x", "value": 1, "quality": "ok",
                       "risk_level": "高风险"})
    assert not rows
    assert errs and "risk_level" in errs[0], errs


def test_extra_dirs_task_is_publishable():
    """CASE_EXTRA_DIRS 挂进来的私有任务必须能被 publish 认出来（#76-2）。

    旧代码 task_dir 硬编码 case/<key>、all_keys 只 listdir(case/)，私有任务一律
    判「未知 case 任务」退出 1，等于永远发布不出去。
    """
    import json
    import tempfile

    tmp = tempfile.mkdtemp()
    tdir = os.path.join(tmp, "privtask")
    os.makedirs(os.path.join(tdir, "out"))
    with open(os.path.join(tdir, "task.json"), "w", encoding="utf-8") as f:
        json.dump({"key": "privtask", "title": "私有任务", "enabled": True,
                   "visibility": "private", "section_domain": "risk"}, f)
    with open(os.path.join(tdir, "out", "latest.json"), "w", encoding="utf-8") as f:
        json.dump({"as_of": "2026-11-24",
                   "points": [{"key": "x.v", "sec": "000300.SH", "value": 1.0,
                               "obs_date": "2026-11-24", "quality": "ok"}]}, f)
    old = os.environ.get("CASE_EXTRA_DIRS")
    os.environ["CASE_EXTRA_DIRS"] = tmp
    try:
        keys = publish.all_keys()
        assert "privtask" in keys, keys
        assert os.path.realpath(publish.task_dir("privtask")) == os.path.realpath(tdir)
        payload, rows, errs = publish.load_out("privtask")
        assert not errs and len(rows) == 1, errs
    finally:
        if old is None:
            os.environ.pop("CASE_EXTRA_DIRS", None)
        else:
            os.environ["CASE_EXTRA_DIRS"] = old

        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print("ok  %s" % fn.__name__)
    print("%d 个用例全部通过" % len(tests))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
