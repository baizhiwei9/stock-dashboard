#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tugou_archive.ima_api_call 的离线单测：skill 版本提示不能被当成业务失败。

背景（2026-11-26）
──────────────────
`ima_api.cjs` 每天第一次调用会先做 skill 版本检查。发现有新版时它把
「更新提示」JSON 打到 stdout 就退出 —— 那个对象**没有 `code` 字段**：

    {"current_version":"1.1.9","latest_version":"1.1.10", ...}

旧代码 `resp.get("code") == 0` 不成立 → 落到「有明确业务错误码，重试」分支 →
日志里天天出现 `[RETRY] IMA API 第1次失败（code=None msg=None）`，靠第二次重试
蒙对。这里把 subprocess 换掉，直接验证「识别提示 → 设 IMA_SKILL_VERSION →
立刻重跑 → 返回真实结果」，不联网。

跑法：
    cd <stock-dashboard 根>
    python3 -m pytest case/tugou_archive/test_ima_version_retry.py -q
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import tugou_archive as T  # noqa: E402


class _FakeCompleted:
    def __init__(self, payload):
        self.stdout = json.dumps(payload, ensure_ascii=False)
        self.stderr = ""
        self.returncode = 0


def _patch_run(monkeypatch, payloads, seen_env=None):
    """按顺序吐出 payloads；每次调用记下当时的 IMA_SKILL_VERSION。"""
    calls = {"n": 0}

    def fake_run(cmd, **kw):
        i = calls["n"]
        calls["n"] += 1
        if seen_env is not None:
            seen_env.append(os.environ.get("IMA_SKILL_VERSION"))
        return _FakeCompleted(payloads[min(i, len(payloads) - 1)])

    monkeypatch.setattr(T.subprocess, "run", fake_run)
    return calls


def test_version_banner_is_not_reported_as_failure(monkeypatch):
    """第一次返回版本提示（无 code），第二次返回真实成功 → 整体算成功、无重试日志。"""
    monkeypatch.delenv("IMA_SKILL_VERSION", raising=False)
    seen = []
    calls = _patch_run(
        monkeypatch,
        [
            {"current_version": "1.1.9", "latest_version": "1.1.10",
             "release_desc": "支持获取知识库文件和网页链接", "checked_at": "x"},
            {"code": 0, "msg": "success", "data": {"info_list": []}},
        ],
        seen_env=seen,
    )

    got = T.ima_api_call("openapi/wiki/v1/search_knowledge_base", {"query": "x"})

    assert got["code"] == 0
    assert calls["n"] == 2, "版本提示后应当立刻重跑一次"
    assert seen[0] is None and seen[1] == "1.1.10", (
        "重跑前必须把提示里的 latest_version 设进 IMA_SKILL_VERSION，"
        f"否则还会再收到一次提示。实际: {seen}"
    )


def test_business_error_still_retries(monkeypatch):
    """有 code 且非 0 的业务错误仍走原来的重试路径，不能被版本逻辑吞掉。"""
    monkeypatch.delenv("IMA_SKILL_VERSION", raising=False)
    monkeypatch.setattr(T.time, "sleep", lambda *_: None)   # 别真等 3s+6s
    calls = _patch_run(monkeypatch, [{"code": 51, "msg": "invalid limit"}])

    got = T.ima_api_call("openapi/wiki/v1/search_knowledge_base", {"query": "x"})

    assert got["code"] == -1
    assert "51" in str(got["msg"])
    assert calls["n"] == 3, "业务错误应重试满 3 次"


def test_existing_env_version_does_not_loop(monkeypatch):
    """环境里已经是提示的版本时，不重复重跑（避免同版本死循环）。"""
    monkeypatch.setenv("IMA_SKILL_VERSION", "1.1.10")
    monkeypatch.setattr(T.time, "sleep", lambda *_: None)
    calls = _patch_run(monkeypatch, [{"latest_version": "1.1.10"}])

    got = T.ima_api_call("openapi/wiki/v1/search_knowledge_base", {"query": "x"})

    assert got["code"] == -1
    assert calls["n"] == 3
