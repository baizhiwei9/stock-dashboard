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

本用例守五件事：
  1. 仓库里（含 datahub_refactor 历史快照）不再出现已泄漏的字面量；
  2. 任意 56 位十六进制 token 字面量都算回退（通用兜底，防止换了个 token 又写死）；
  3. 扫描覆盖必须真实存在 —— git 不可用/非 git 树时 fail-closed，不许「零文件全绿」；
  4. ``hub_root`` 的三个取值函数真能从环境变量读到；
  5. 读不到时返回空串而不是抛异常 —— 老脚本按「空=没配」降级，不能因此崩。
"""
import os
import re
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
    "1f7d5d32" + "5804a92b" + "86996303" + "6fab5e5c" + "b71e7820" + "e7d60903" + "ee9952b1",
    "TBmy7D" + "Asj9aN" + "Jdxb",
    "6d34534a" + "-af18-48" + "ba-be77-" + "a30c5247" + "34a8",
]

#: Tushare token 是 56 位十六进制；出现任意一段连续 56 位都按明文回退处理。
_HEX56 = re.compile(r"\b[0-9a-f]{56}\b")

#: 二进制/压缩包不读文本。
_SKIP_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".gz", ".db", ".zip",
                  ".xlsx", ".xls", ".parquet", ".pdf", ".woff", ".woff2")


def _tracked_files():
    """返回受版本控制的文本文件清单。

    fail-closed：git 不可用、非 git 树、或清单为空时直接报错，绝不返回空列表让
    两个扫描用例「零文件扫描 + 全绿」。历史上正因如此，datahub_refactor 快照里
    26 个写死 token 的文件一直没被守住。
    """
    try:
        out = subprocess.run(["git", "ls-files"], cwd=HERE, capture_output=True, text=True)
    except OSError as exc:
        raise RuntimeError(
            "无法执行 git ls-files，明文凭据守卫拒绝判定通过：%s" % exc)
    if out.returncode != 0:
        raise RuntimeError(
            "git ls-files 失败（rc=%d）：%s\n"
            "非 git 树会漏扫全部文件，本用例按 fail-closed 报错，不当作通过。"
            % (out.returncode, (out.stderr or "").strip()))
    files = [rel for rel in out.stdout.splitlines()
             if rel and not rel.endswith(_SKIP_SUFFIXES)]
    if not files:
        raise RuntimeError(
            "git ls-files 未返回任何受控文件（cwd=%s），扫描覆盖为空，"
            "拒绝按通过处理。" % HERE)
    return files


def _read(rel):
    try:
        return open(os.path.join(HERE, rel), encoding="utf-8", errors="ignore").read()
    except OSError:
        return None


def test_tracked_files_cover_repo():
    """扫描清单必须真覆盖仓库，防止 cwd/路径漂移后变成空扫。"""
    files = _tracked_files()
    assert len(files) > 100, "扫描文件数异常偏少：%d" % len(files)
    for must in ("test_no_plaintext_creds.py", "case/_lib/hub_root.py"):
        assert must in files, "扫描清单缺少 %s（cwd=%s）" % (must, HERE)


def test_tracked_files_fail_closed_without_git(monkeypatch):
    """git 返回空清单时不得静默通过，必须报错。"""
    class _Done:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _Done())
    with pytest.raises(RuntimeError):
        _tracked_files()

    class _Fail:
        returncode = 128
        stdout = ""
        stderr = "fatal: not a git repository"

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _Fail())
    with pytest.raises(RuntimeError):
        _tracked_files()


def test_no_leaked_secret_literals():
    hits = []
    for rel in _tracked_files():
        src = _read(rel)
        if src is None:
            continue
        for secret in LEAKED:
            if secret in src:
                hits.append("%s ← %s…" % (rel, secret[:8]))
    assert not hits, "明文凭据回退：\n" + "\n".join(hits)


def test_no_hex56_token_literals():
    """通用兜底：任何连续 56 位十六进制字面量都按 Tushare token 明文回退。"""
    hits = []
    for rel in _tracked_files():
        src = _read(rel)
        if src is None:
            continue
        for m in _HEX56.finditer(src):
            line = src.count("\n", 0, m.start()) + 1
            hits.append("%s:%d ← %s…" % (rel, line, m.group()[:8]))
    assert not hits, "疑似 56 位 token 明文：\n" + "\n".join(hits)


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
