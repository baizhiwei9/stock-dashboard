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

    def test_max_delete_is_global_across_rules(self):
        """--max-delete 是整轮上限，不是每条规则各给一份（曾经 2 条规则各删 2 个）。"""
        for i in range(5):
            _touch(os.path.join(self.root, "case", "t", "data", "f%d.csv" % i), 40)
            _touch(os.path.join(self.root, "case", "t", "out", "o%d.png" % i), 400)
        self.assertEqual(self._run("--apply", "--max-delete", "3"), 0)
        left_csv = [x for x in os.listdir(os.path.join(self.root, "case", "t", "data"))
                    if x.endswith(".csv")]
        left_png = [x for x in os.listdir(os.path.join(self.root, "case", "t", "out"))
                    if x.endswith(".png")]
        self.assertEqual(len(left_csv) + len(left_png), 7, "整轮只该删 3 个")

    def test_rotated_log_matched_by_dot_segment(self):
        """logs/<key>.log.<时间戳> 的 splitext 后缀是时间戳，必须按点分段判后缀。"""
        rules = os.path.join(self.root, "log_rules.json")
        with open(rules, "w", encoding="utf-8") as f:
            json.dump({"rules": [{"name": "rotated", "glob": "logs/*.log.*",
                                  "keep_days": 30, "ext": [".log", ".txt"]}]}, f)
        old = _touch(os.path.join(self.root, "logs", "margin_flow.log.20261101-043000"), 40)
        self.assertEqual(cleanup.main(["--root", self.root, "--rules", rules,
                                       "--apply", "--quiet"]), 0)
        self.assertFalse(os.path.exists(old), "轮转日志必须能被清理规则命中")

    def test_task_scopes_root_level_rule(self):
        """--task 也要管任务根级规则（case/*/*.png），别退化成 case/<task>/ 目录。"""
        rules = os.path.join(self.root, "root_rules.json")
        with open(rules, "w", encoding="utf-8") as f:
            json.dump({"rules": [{"name": "rootpng", "glob": "case/*/*.png",
                                  "keep_days": 90, "ext": [".png"]}]}, f)
        keep = _touch(os.path.join(self.root, "case", "other", "x.png"), 400)
        drop = _touch(os.path.join(self.root, "case", "t", "img.png"), 400)
        self.assertEqual(cleanup.main(["--root", self.root, "--rules", rules, "--task", "t",
                                       "--apply", "--quiet"]), 0)
        self.assertFalse(os.path.exists(drop))
        self.assertTrue(os.path.exists(keep), "别的任务不该被 --task t 清到")

    def test_exclude_keeps_history(self):
        """短保留期的目录规则要能用 exclude 放过「不可重建」的历史数据。"""
        rules = os.path.join(self.root, "ex_rules.json")
        with open(rules, "w", encoding="utf-8") as f:
            json.dump({"rules": [
                {"name": "cache", "glob": "case/*/data/**/*", "keep_days": 30,
                 "ext": [".json"], "exclude": ["case/*/data/chains/**/*"]},
                {"name": "chains", "glob": "case/*/data/chains/**/*", "keep_days": 400,
                 "ext": [".json"]},
            ]}, f)
        keep = _touch(os.path.join(self.root, "case", "t", "data", "chains", "20260101.json"), 100)
        drop = _touch(os.path.join(self.root, "case", "t", "data", "cache.json"), 100)
        self.assertEqual(cleanup.main(["--root", self.root, "--rules", rules,
                                       "--apply", "--quiet"]), 0)
        self.assertTrue(os.path.exists(keep), "chains 历史不在 30 天规则里被删")
        self.assertFalse(os.path.exists(drop))

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