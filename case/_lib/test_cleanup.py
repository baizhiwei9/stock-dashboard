#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cleanup.py 的安全边界测试（issue #80）。

只测「删不删」的判定，不测调度：

    python3 case/_lib/test_cleanup.py
    python3 -m pytest case/_lib/test_cleanup.py -q
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import cleanup  # noqa: E402


def _touch(path: str, age_days: float, content: str = "x") -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    old = time.time() - age_days * 86400
    os.utime(path, (old, old))
    return path


class CleanupCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="cleanup_test_")
        self.rules = os.path.join(self.root, "rules.json")
        with open(self.rules, "w", encoding="utf-8") as f:
            json.dump({"rules": [
                {"name": "cache", "glob": "case/*/data/**/*", "keep_days": 30,
                 "ext": [".csv", ".json"]},
                {"name": "out", "glob": "case/*/out/**/*", "keep_days": 90,
                 "ext": [".png", ".md", ".json"]},
                {"name": "imgs", "glob": "assets/private/**/*", "keep_days": 90,
                 "ext": [".png"], "respect_html": "private.html"},
            ]}, f)
        self.logs = os.path.join(self.root, "logs")
        os.makedirs(self.logs, exist_ok=True)
        self._old_logdir = cleanup.LOG_DIR
        cleanup.LOG_DIR = self.logs

    def tearDown(self):
        cleanup.LOG_DIR = self._old_logdir
        shutil.rmtree(self.root, ignore_errors=True)

    def _run(self, *extra):
        argv = ["--root", self.root, "--rules", self.rules, "--quiet"] + list(extra)
        return cleanup.main(argv)

    def test_dry_run_does_not_delete(self):
        p = _touch(os.path.join(self.root, "case", "t", "data", "old.csv"), 40)
        self.assertEqual(self._run(), 0)
        self.assertTrue(os.path.exists(p))

    def test_apply_deletes_expired_cache(self):
        old = _touch(os.path.join(self.root, "case", "t", "data", "old.csv"), 40)
        new = _touch(os.path.join(self.root, "case", "t", "data", "new.csv"), 1)
        self.assertEqual(self._run("--apply"), 0)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(new))

    def test_latest_json_protected(self):
        p = _touch(os.path.join(self.root, "case", "t", "out", "latest.json"), 400)
        _touch(os.path.join(self.root, "case", "t", "out", "old.png"), 400)
        self.assertEqual(self._run("--apply"), 0)
        self.assertTrue(os.path.exists(p))

    def test_non_whitelisted_suffix_untouched(self):
        # 规则里没写 .py，且 .py 也不在 ALLOWED_EXT 里
        p = _touch(os.path.join(self.root, "case", "t", "data", "old.py"), 40)
        self.assertEqual(self._run("--apply"), 0)
        self.assertTrue(os.path.exists(p))

    def test_recent_file_kept(self):
        p = _touch(os.path.join(self.root, "case", "t", "out", "old.png"), 10)
        self.assertEqual(self._run("--apply"), 0)
        self.assertTrue(os.path.exists(p))

    def test_referenced_image_kept(self):
        keep = _touch(os.path.join(self.root, "assets", "private", "a.png"), 400)
        drop = _touch(os.path.join(self.root, "assets", "private", "b.png"), 400)
        with open(os.path.join(self.root, "private.html"), "w", encoding="utf-8") as f:
            f.write('<img src="assets/private/a.png">')
        self.assertEqual(self._run("--apply"), 0)
        self.assertTrue(os.path.exists(keep))
        self.assertFalse(os.path.exists(drop))

    def test_max_delete_budget(self):
        for i in range(5):
            _touch(os.path.join(self.root, "case", "t", "data", "f%d.csv" % i), 40)
        self.assertEqual(self._run("--apply", "--max-delete", "2"), 0)
        left = [x for x in os.listdir(os.path.join(self.root, "case", "t", "data"))
                if x.endswith(".csv")]
        self.assertEqual(len(left), 3)

    def test_tracked_file_never_deleted(self):
        try:
            subprocess.run(["git", "init", "-q", self.root], check=True,
                           capture_output=True, timeout=30)
        except Exception:                                    # noqa: BLE001
            self.skipTest("git 不可用")
        p = _touch(os.path.join(self.root, "case", "t", "data", "tracked.csv"), 400)
        subprocess.run(["git", "-C", self.root, "add", "-f", "case/t/data/tracked.csv"],
                       check=True, capture_output=True, timeout=30)
        self.assertEqual(self._run("--apply"), 0)
        self.assertTrue(os.path.exists(p), "git 跟踪的文件必须保留（模型资产在 data/ 里）")

    def test_manifest_written(self):
        _touch(os.path.join(self.root, "case", "t", "data", "old.csv"), 40)
        argv = ["--root", self.root, "--rules", self.rules, "--quiet"]
        self.assertEqual(cleanup.main(argv), 0)
        man = [x for x in os.listdir(self.logs) if x.startswith("cleanup-")]
        self.assertTrue(man, "哪怕 dry-run 也要落一份清单")
        with open(os.path.join(self.logs, man[0]), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("WOULD", text)
        self.assertIn("case/t/data/old.csv", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
