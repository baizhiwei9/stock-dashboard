#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""feishu_creds.require / runner 飞书前置校验的回归测试（不连网）

#35 把 app_secrets.require 改成只认点号 key 后，feishu_creds.require 仍按
分段形式传 ("feishu", "webhook_url")，导致恒报缺凭据、runner 的
_preflight_feishu 把所有飞书任务在启动阶段跳过。这里用临时凭据文件锁住。

    python3 case/_lib/test_feishu_creds.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile

LIB = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(LIB))
if LIB not in sys.path:
    sys.path.insert(0, LIB)

# 本机可能已有真实凭据，测试全程用临时文件，先把会干扰的环境变量清掉
_ENV_LEAKS = (
    "COZE_SECRETS", "APP_SECRETS_FILE",
    "FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_WEBHOOK",
    "FEISHU_WEBHOOK_URL", "FEISHU_CHAT_ID",
    "COZE_SEC_FEISHU_APP_ID", "COZE_SEC_FEISHU_APP_SECRET",
    "COZE_SEC_FEISHU_WEBHOOK", "COZE_SEC_FEISHU_WEBHOOK_URL",
)
for _var in _ENV_LEAKS:
    os.environ.pop(_var, None)

_TMP = tempfile.mkdtemp(prefix="feishu-creds-test-")
FULL = os.path.join(_TMP, "full.json")
ONLY_HOOK = os.path.join(_TMP, "only-webhook.json")
NO_HOOK = os.path.join(_TMP, "no-webhook.json")
EMPTY = os.path.join(_TMP, "empty.json")
HOOK = "https://example.invalid/hook"


def _write(path, feishu):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"feishu": feishu}, f)
    os.chmod(path, 0o600)


def _use(path):
    os.environ["COZE_SECRETS"] = path


_write(FULL, {"app_id": "cli_test", "app_secret": "sec_test",
              "webhook_url": HOOK})
_write(ONLY_HOOK, {"webhook_url": HOOK})
_write(NO_HOOK, {"app_id": "cli_test", "app_secret": "sec_test"})
_write(EMPTY, {})
_use(FULL)

import feishu_creds  # noqa: E402
import runner  # noqa: E402


def _task(with_feishu):
    d = tempfile.mkdtemp(prefix="task-", dir=_TMP)
    body = ("from feishu_creds import WEBHOOK_URL  # 飞书推送\n"
            if with_feishu else "print('no push')\n")
    with open(os.path.join(d, "run.py"), "w", encoding="utf-8") as f:
        f.write(body)
    return {"key": "fake", "_dir": d}


def test_require_roundtrip():
    got = feishu_creds.require("webhook_url")
    assert got == {"webhook_url": HOOK}, got


def test_require_defaults_to_all_fields():
    got = feishu_creds.require()
    assert set(got) == {"app_id", "app_secret", "webhook_url"}, got
    assert got["app_id"] == "cli_test" and got["webhook_url"] == HOOK


def test_require_partial_field():
    """只要 webhook 时，不因缺 app_id/app_secret 报错；点缺的字段要报。"""
    _use(ONLY_HOOK)
    try:
        assert feishu_creds.require("webhook_url") == {"webhook_url": HOOK}
        try:
            feishu_creds.require("app_secret")
        except feishu_creds.FeishuCredentialMissing as e:
            assert "feishu.app_secret" in str(e), e
        else:
            raise AssertionError("缺 app_secret 时没有报错")
    finally:
        _use(FULL)


def test_require_reports_missing_webhook():
    _use(NO_HOOK)
    try:
        feishu_creds.require("webhook_url")
    except feishu_creds.FeishuCredentialMissing as e:
        assert "feishu.webhook_url" in str(e), e
    else:
        raise AssertionError("缺 webhook_url 时没有报错")
    finally:
        _use(FULL)


def test_preflight_ready():
    assert runner._preflight_feishu(_task(True)) is None


def test_preflight_blocks_missing_webhook():
    _use(NO_HOOK)
    try:
        msg = runner._preflight_feishu(_task(True))
    finally:
        _use(FULL)
    assert msg and "飞书凭据未就绪" in msg, msg
    assert "feishu.webhook_url" in msg, msg


def test_preflight_ignores_non_feishu_task():
    assert runner._preflight_feishu(_task(False)) is None


def test_manning_config_import_without_secrets():
    """缺配置时 manning_flow 两个 config 的 import 不能崩。"""
    _use(EMPTY)
    try:
        for rel, attr in (("feishu_config.py", "WEBHOOK_URL"),
                          ("ts_config.py", "TS_TOKEN")):
            path = os.path.join(REPO, "case", "manning_flow", rel)
            spec = importlib.util.spec_from_file_location("manning_" + rel, path)
            mod = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(mod)
            except ImportError as e:  # 本机没装 tushare 这类依赖时跳过
                print("skip %s: %s" % (rel, e))
                continue
            assert getattr(mod, attr) == "", (rel, getattr(mod, attr))
    finally:
        _use(FULL)


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print("ok  %s" % fn.__name__)
    print("%d 个用例全部通过" % len(tests))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
