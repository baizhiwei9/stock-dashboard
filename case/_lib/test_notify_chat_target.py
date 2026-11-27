# -*- coding: utf-8 -*-
"""``notify_init.chat_id`` 的取值口径回归（2026-11-28）。

飞书目标群不再写死在仓库里：默认群走 DataHub 通知模块的 app profile，再退本应用
secrets 的 ``feishu.chat_id``；具名群（stocks / tugou / tags）只认自己的
``feishu.chats.<key>`` 或 ``NOTIFY_APP_STOCK_DASHBOARD_FEISHU_CHAT_ID_<KEY>``，
**不回落到默认群** —— 把土狗群、标签群的推送误发到主群，比如实报缺更难发现。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import notify_init  # noqa: E402

_ENV_BASE = "NOTIFY_APP_STOCK_DASHBOARD_FEISHU_CHAT_ID"


def _write_secrets(tmp_path, data):
    p = tmp_path / "secrets.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


def _isolate(monkeypatch, tmp_path, data):
    """把凭据来源钉到临时 secrets，并关掉部署侧 notify.json 与相关环境变量。"""
    monkeypatch.setenv("APP_SECRETS_FILE", _write_secrets(tmp_path, data))
    monkeypatch.setenv("NOTIFY_CONFIG", str(tmp_path / "no-such-notify.json"))
    for name in (_ENV_BASE, _ENV_BASE + "_TUGOU", _ENV_BASE + "_TAGS"):
        monkeypatch.delenv(name, raising=False)


def test_default_chat_from_secrets(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path, {"feishu": {"chat_id": "oc_default"}})
    assert notify_init.chat_id() == "oc_default"
    assert notify_init.chat_id("", "fallback") == "oc_default"


def test_named_chat_from_secrets(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path,
             {"feishu": {"chat_id": "oc_default", "chats": {"tugou": "oc_tugou"}}})
    assert notify_init.chat_id("tugou") == "oc_tugou"
    assert notify_init.chat_id("TUGOU") == "oc_tugou"


def test_named_chat_never_falls_back_to_default(tmp_path, monkeypatch):
    """具名群没配时如实返回空，绝不借用默认群。"""
    _isolate(monkeypatch, tmp_path, {"feishu": {"chat_id": "oc_default"}})
    assert notify_init.chat_id("tugou") == ""
    assert notify_init.chat_id("tugou", "fallback") == "fallback"


def test_env_suffix_wins_over_secrets(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path,
             {"feishu": {"chats": {"tugou": "oc_from_secrets"}}})
    monkeypatch.setenv(_ENV_BASE + "_TUGOU", "oc_from_env")
    assert notify_init.chat_id("tugou") == "oc_from_env"


def test_default_env_wins_over_secrets(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path, {"feishu": {"chat_id": "oc_from_secrets"}})
    monkeypatch.setenv(_ENV_BASE, "oc_from_env")
    assert notify_init.chat_id() == "oc_from_env"


def test_hint_names_the_field(tmp_path, monkeypatch):
    """缺配提示要能直接告诉部署侧改哪个字段。"""
    _isolate(monkeypatch, tmp_path, {"feishu": {}})
    hint = notify_init.chat_hint("tugou")
    assert "feishu.chats.tugou" in hint
    assert _ENV_BASE + "_TUGOU" in hint
    assert "apps.%s" % notify_init.APP in hint


def test_cli_chat_prints_id_and_exits_nonzero_when_missing(tmp_path, monkeypatch, capsys):
    _isolate(monkeypatch, tmp_path, {"feishu": {"chats": {"stocks": "oc_stocks"}}})
    assert notify_init._cli(["notify_init.py", "chat", "stocks"]) == 0
    assert capsys.readouterr().out.strip() == "oc_stocks"

    assert notify_init._cli(["notify_init.py", "chat", "tugou"]) == 1
    err = capsys.readouterr().err
    assert "feishu.chats.tugou" in err