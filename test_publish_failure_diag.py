#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""publish.sh 失败可见性回归测试（不联网、不真发布）

    python3 test_publish_failure_diag.py

背景
────
2026-11-25 22:43 build 整链 exit=1：build.py 成功，publish.sh 挂了。
health_run 只留一句「退出码 1」，daily.log 里虽有 AUTH_REQUIRED 原文，
但没人会主动去翻 —— 等于静默失败。

所以失败分支必须做两件事：
  1. daily.log 里写「诊断」段：HOME / config.json 状态 / COZE_API_TOKEN /
     auth status / deploy 输出末 15 行
  2. stderr 打一行摘要，让 scheduler 的 logs/build.log 直接能看见

覆盖 3 条：
  1 AUTH_REQUIRED 失败 → rc=1、stderr 有摘要且带错误码、daily.log 有诊断段
  2 成功路径 → rc=0、stderr 干净、daily.log 不出现诊断段
  3 发布链路凭证不落日志：诊断段只写「已设置/未设置」，绝不打印 token 值
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PUBLISH = os.path.join(HERE, "publish.sh")

FAKE_AUTH_REQUIRED = """#!/bin/bash
echo '{"error": {"code": "AUTH_REQUIRED", "message": "No shared login found."}}'
exit 1
"""

FAKE_OK = """#!/bin/bash
case "$*" in
  *"deploy --format json --source-root"*) echo '{"deploy_history_id":"777001"}' ;;
  *"status --app-id"*) echo '{"status":"Succeeded","domains":["https://example.page.coze.site"]}' ;;
  *) echo '{"logged_in": true}' ;;
esac
exit 0
"""


def _run(cli_body: str, env_extra: dict | None = None):
    """在临时目录里跑一份被改过 CLI 路径的 publish.sh。"""
    tmp = tempfile.mkdtemp(prefix="pubtest_")
    try:
        shutil.copy(PUBLISH, os.path.join(tmp, "publish.sh"))
        with open(os.path.join(tmp, "index.html"), "w") as f:
            f.write("<html>x</html>")
        cli = os.path.join(tmp, "fakecli")
        with open(cli, "w") as f:
            f.write(cli_body)
        os.chmod(cli, stat.S_IRWXU)

        path = os.path.join(tmp, "publish.sh")
        with open(path) as f:
            src = f.read()
        src = src.replace("CLI=/usr/bin/coze-dev", "CLI=" + cli)
        with open(path, "w") as f:
            f.write(src)

        env = dict(os.environ)
        env.update(env_extra or {})
        p = subprocess.run(["bash", path], cwd=tmp, env=env,
                           capture_output=True, text=True, timeout=180)
        log_path = os.path.join(tmp, "daily.log")
        log = open(log_path).read() if os.path.exists(log_path) else ""
        return p, log
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_auth_failure_is_visible():
    p, log = _run(FAKE_AUTH_REQUIRED)
    assert p.returncode == 1, p.returncode
    # stderr 一行摘要：scheduler 的 logs/build.log 靠这一行定位
    assert "[publish] 发布失败" in p.stderr, p.stderr
    assert "AUTH_REQUIRED" in p.stderr, p.stderr
    # daily.log 诊断段
    assert "发布失败" in log, log
    assert "--- 诊断 ---" in log, log
    assert "config.json:" in log, log
    assert "auth status:" in log, log
    assert "--- deploy 输出" in log, log


def test_success_path_stays_quiet():
    p, log = _run(FAKE_OK)
    assert p.returncode == 0, (p.returncode, p.stdout, p.stderr)
    assert p.stderr.strip() == "", p.stderr
    assert "发布成功" in log, log
    assert "--- 诊断 ---" not in log, log


def test_token_value_never_logged():
    p, log = _run(FAKE_AUTH_REQUIRED, {"COZE_API_TOKEN": "sat_SHOULD_NOT_APPEAR"})
    assert p.returncode == 1
    assert "sat_SHOULD_NOT_APPEAR" not in log, log
    assert "sat_SHOULD_NOT_APPEAR" not in p.stderr, p.stderr
    assert re.search(r"COZE_API_TOKEN: (已设置|未设置)", log), log


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("  ✅ %s" % name)
            except AssertionError as e:
                failed += 1
                print("  ❌ %s\n     %s" % (name, e))
    print("\n%d failed" % failed if failed else "\n全部通过")
    sys.exit(1 if failed else 0)
