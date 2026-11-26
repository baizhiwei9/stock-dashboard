#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""明文凭据收口的回归用例（2026-11-26）。

背景
────
``case/`` 下从老沙箱迁过来的脚本里散落着三类明文凭据：

  · Tushare token（同一串出现在 60+ 个文件里）
  · 163 SMTP 授权码（``daily_email.py`` / ``weekly_stock_monitor.py`` / 两个 config.json）
  · 飞书群机器人 webhook（``credit_spread_weekly`` 的 config.json + 两个脚本的默认模板）

它们曾经以明文进过 git 历史（见 docs/SECRETS.md），轮换前等于裸奔。现在统一走
``case/_lib/hub_root.py``：环境变量 → ``~/.config/stock-dashboard/secrets.json``。

本用例守三件事：
  1. 仓库里（除 datahub_refactor 的历史快照）不再出现这三个字面量；
  2. ``hub_root`` 的三个取值函数真能从环境变量读到；
  3. 读不到时返回空串而不是抛异常 —— 老脚本按「空=没配」降级，不能因此崩。
"""
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "case", "_lib"))

import hub_root  # noqa: E402

#: 已轮换前的明文值。仓库里出现任意一个都算回退。
#: 这里拆成片段拼接 —— 否则本文件自己就成了「仓库里有明文」的那一个。
LEAKED = [
    "a7c1ab22" + "e3162aac" + "86bf7732" + "3755b2d9" + "9e9cadfa" + "d4ae631a" + "50574265",
    "TBmy7D" + "Asj9aN" + "Jdxb",
    "6d34534a" + "-af18-48" + "ba-be77-" + "a30c5247" + "34a8",
]

#: 历史快照（datahub_refactor/e2e_*）是改造前的原样留档，不参与本轮收口。
EXCLUDE_DIRS = ("datahub_refactor",)


def _tracked_files():
    out = subprocess.run(["git", "ls-files"], cwd=HERE, capture_output=True, text=True)
    for rel in out.stdout.splitlines():
        if any(d in rel for d in EXCLUDE_DIRS):
            continue
        if rel.endswith((".png", ".jpg", ".gz", ".db", ".zip", ".xlsx")):
            continue
        yield rel


def test_no_leaked_secret_literals():
    hits = []
    for rel in _tracked_files():
        try:
            src = open(os.path.join(HERE, rel), encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        for secret in LEAKED:
            if secret in src:
                hits.append("%s ← %s…" % (rel, secret[:8]))
    assert not hits, "明文凭据回退：\n" + "\n".join(hits)


def test_tushare_token_from_env(monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "env-token-xyz")
    assert hub_root.tushare_token() == "env-token-xyz"


def test_smtp_password_from_env(monkeypatch):
    monkeypatch.setenv("SMTP_PASSWORD", "env-smtp-xyz")
    assert hub_root.smtp_password() == "env-smtp-xyz"


def test_feishu_webhook_from_env(monkeypatch):
    monkeypatch.setenv("FEISHU_WEBHOOK", "https://example.invalid/hook")
    assert hub_root.feishu_webhook() == "https://example.invalid/hook"


@pytest.mark.parametrize("fn", ["tushare_token", "smtp_password", "feishu_webhook"])
def test_missing_returns_empty_not_raises(fn, monkeypatch, tmp_path):
    """读不到凭据时必须返回空串 —— 老脚本按「空=没配」降级，不能崩。"""
    for n in ("TUSHARE_TOKEN", "COZE_SEC_TUSHARE_TOKEN", "SMTP_PASSWORD",
              "EMAIL_AUTH_CODE", "EMAIL_PASSWORD", "COZE_SEC_EMAIL_PASSWORD",
              "FEISHU_WEBHOOK", "FEISHU_WEBHOOK_URL", "COZE_SEC_FEISHU_WEBHOOK"):
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setenv("COZE_SECRETS", str(tmp_path / "nope.json"))
    monkeypatch.setenv("APP_SECRETS_FILE", str(tmp_path / "nope2.json"))
    monkeypatch.setenv("HOME", str(tmp_path))
    assert getattr(hub_root, fn)() == ""
