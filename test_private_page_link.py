#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""私有页「打开完整页面」链接的最小回归（不联网、不连真库）

    python3 test_private_page_link.py

覆盖 `build_private._page_html`：
  1. `out/<key>.html` 存在且有内容 → 生成链接，并抄到 assets/private/<key>/
  2. 只有通用名 `out/report.html` → 也能认
  3. 空壳 html（< PAGE_MIN_BYTES）→ 不挂链接
  4. 没有 out/ 或没有 html → 返回空串（不占位、不报错）
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


class _StubModule(types.ModuleType):
    """build.py 顶层 from data_hub.tech import 一堆名字：一律返回假函数。"""

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *a, **k: None


def _install_stub():
    saved = {k: sys.modules.get(k) for k in ("data_hub", "data_hub.tech")}
    tech = _StubModule("data_hub.tech")
    pkg = _StubModule("data_hub")
    pkg.tech = tech
    sys.modules.update({"data_hub": pkg, "data_hub.tech": tech})
    return saved


def _restore(saved):
    for k, v in saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v


def _mk_task(root, key, html=None, name=None, size=0):
    d = os.path.join(root, key)
    os.makedirs(os.path.join(d, "out"), exist_ok=True)
    if html is not None:
        with open(os.path.join(d, "out", name or ("%s.html" % key)), "w", encoding="utf-8") as f:
            f.write(html)
    if size:                                        # 纯填充，撑过 PAGE_MIN_BYTES
        with open(os.path.join(d, "out", name or ("%s.html" % key)), "w", encoding="utf-8") as f:
            f.write("<html><body>" + "x" * size + "</body></html>")
    return d


def main() -> int:
    saved = _install_stub()
    try:
        import build_private as BP
    finally:
        _restore(saved)

    tmp = tempfile.mkdtemp(prefix="pgtest-")
    assets = os.path.join(tmp, "assets", "private")
    try:
        # 1 `<key>.html` 命中
        d1 = _mk_task(tmp, "market_tech", size=BP.PAGE_MIN_BYTES + 500)
        got = BP._page_html({"key": "market_tech", "dir": d1, "title": "大盘技术拆解"}, assets, tmp)
        assert 'class="pg-link"' in got, got
        assert 'target="_blank"' in got
        assert "assets/private/market_tech/market_tech.html" in got, got
        assert os.path.isfile(os.path.join(assets, "market_tech", "market_tech.html"))
        print("ok  1 key.html → 链接 + 抄到 assets")

        # 2 通用名 report.html
        d2 = _mk_task(tmp, "other_task", name="report.html", size=BP.PAGE_MIN_BYTES + 100)
        got = BP._page_html({"key": "other_task", "dir": d2, "title": "别的任务"}, assets, tmp)
        assert "report.html" in got, got
        print("ok  2 report.html 也认")

        # 3 空壳不挂
        d3 = _mk_task(tmp, "tiny_task", html="<html></html>")
        assert BP._page_html({"key": "tiny_task", "dir": d3}, assets, tmp) == ""
        print("ok  3 空壳 html 不挂链接")

        # 4 无 out/ 不报错
        d4 = os.path.join(tmp, "no_out")
        os.makedirs(d4, exist_ok=True)
        assert BP._page_html({"key": "no_out", "dir": d4}, assets, tmp) == ""
        print("ok  4 没有 out/ 返回空串")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("4 个用例全部通过")
    return 0


def test_page_link():
    """pytest 入口：pytest 只收集 test_* 函数，main() 里的 4 条断言照样跑。"""
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
