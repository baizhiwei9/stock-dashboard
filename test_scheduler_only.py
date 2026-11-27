#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SD_ONLY / SD_NO_API：运行期开关的单元测试。

不起进程、不写真实配置：
  · only_keys 解析（逗号/空格/中文逗号/去重）
  · _apply_only 过滤
  · _only_unknown 拼错 key
  · cmd_start 对未知 key fail-fast
  · api_enabled 的两种关闭来源
"""
from __future__ import annotations

import contextlib
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import scheduler as S  # noqa: E402


@contextlib.contextmanager
def _stubbed(**attrs):
    saved = {k: getattr(S, k) for k in attrs}
    for k, v in attrs.items():
        setattr(S, k, v)
    try:
        yield
    finally:
        for k, v in saved.items():
            setattr(S, k, v)


@contextlib.contextmanager
def _env(**vals):
    saved = {k: os.environ.get(k) for k in vals}
    try:
        for k, v in vals.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_only_keys_parsing():
    with _env(SD_ONLY=" a, b ，a\tc "):
        assert S.only_keys() == ["a", "b", "c"]
    with _env(SD_ONLY=""):
        assert S.only_keys() == []
    with _env(SD_ONLY=None):
        assert S.only_keys() == []


def test_apply_only_filters_and_keeps_order():
    mods = {"a": {"key": "a"}, "b": {"key": "b"}, "c": {"key": "c"}}
    with _env(SD_ONLY="c,a"):
        assert list(S._apply_only(mods)) == ["a", "c"]
    with _env(SD_ONLY=None):
        assert S._apply_only(mods) == mods
        assert S._apply_only(mods) is not mods      # 副本，调用方可改


def test_only_unknown_lists_typos():
    with _env(SD_ONLY="a,typo"):
        with _stubbed(all_modules=lambda force=False: [{"key": "a"}, {"key": "b"}]):
            assert S._only_unknown() == ["typo"]


def test_cmd_start_rejects_unknown_only():
    """拼错 key 不能静默变成「一个都不跑」：启动直接失败并列出未知 key。"""
    out = io.StringIO()
    with _env(SD_ONLY="nope"):
        with _stubbed(_preflight_secrets=lambda: True,
                      all_modules=lambda force=False: [{"key": "a"}]):
            with contextlib.redirect_stdout(out):
                rc = S.cmd_start()
    assert rc == 1
    assert "nope" in out.getvalue() and "不存在" in out.getvalue()


def test_api_enabled_sources():
    with _env(SD_NO_API="1"):
        with _stubbed(load_cfg=lambda force=False: {}):
            assert S.api_enabled() is False
    with _env(SD_NO_API=None):
        with _stubbed(load_cfg=lambda force=False: {"api": {"enabled": False}}):
            assert S.api_enabled() is False
        with _stubbed(load_cfg=lambda force=False: {"api": {}}):
            assert S.api_enabled() is True
