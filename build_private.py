#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_private.py — 私有任务展示页（precious-task）

公开页（`build.py` → `index.html`）与私有页（本脚本 → `private.html`）是**两套页面**：
各有各的入口，互不混排。样式、卡片渲染、取数口径全部复用 `build.py`，不复制框架、
不另算一份数据。

私有仓通过 `CASE_EXTRA_DIRS` 挂进来，任务在 `task.json` 里写 `visibility=private`
（历史写法 `internal` 等价）。私有页只读这些任务；公开页不认识它们。

用法：
    export CASE_EXTRA_DIRS=/srv/private-tasks/case
    export PYTHONPATH=/srv/monitor/datahub-multi-tier:$PWD
    python3 build_private.py                     # → private.html
    python3 build_private.py --out /tmp/p.html   # 换输出路径
"""
from __future__ import annotations

import argparse
import html
import os
import sys
from datetime import datetime
from typing import Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import build as B                                            # noqa: E402  复用 CSS / 卡片渲染 / 取数

SECTION_ORDER = ("stock", "futures", "realestate", "macro",
                 "liquidity", "bond", "option", "risk", "modules")


def _workers() -> List[Dict]:
    """只取 visibility=private 的任务（含旧别名 internal）。"""
    lib = os.path.join(HERE, "case", "_lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import case_registry
    return case_registry.as_workers(os.path.join(HERE, "case"), visibility="private")


def _latest_for(ws: List[Dict]) -> Dict:
    """按 (metric_key, sec) 批量取最新值；库里没有就不显示，不补零、不拿旧值顶替。"""
    need: Dict[str, List[str]] = {}
    for w in ws:
        for it in (w.get("metrics") or []):
            if isinstance(it, str):
                it = {"key": it}
            k = it.get("key")
            s = it.get("sec") or w.get("sec") or ""
            if k and s:
                need.setdefault(s, []).append(k)
    latest: Dict = {}
    for s, ks in need.items():
        try:
            latest.update(B.read_latest(ks, [s]))
        except Exception:                                     # noqa: BLE001
            pass
    return latest


def build_private(out_path: str) -> str:
    ws = _workers()
    if not ws:
        print("[build_private] 没有私有任务：确认 CASE_EXTRA_DIRS 指到私有仓的 case/ 这一层",
              file=sys.stderr)
    latest = _latest_for(ws)

    by_sec: Dict[str, List[Dict]] = {}
    for w in ws:
        by_sec.setdefault(w.get("tab") or w.get("sec") or "modules", []).append(w)

    cfg = B.load_sections(os.path.join(HERE, "sections.json"))
    meta = {x["key"]: x for x in (cfg.get("sections") or [])}

    nav, blocks = [], []
    for sec in SECTION_ORDER:
        items = by_sec.get(sec) or []
        if not items:
            continue
        m = meta.get(sec) or {}
        nav.append('<a href="#p_%s">%s</a>' % (sec, html.escape(m.get("title") or sec)))
        cards = "".join(B.render_registered_model(w, latest, {})[0] for w in items)
        blocks.append(
            '<div class="sec-wrap" data-sec="p_%s" id="p_%s">'
            '<div class="sec-head"><span class="sec-num">%s</span>'
            '<div><h2>%s</h2><p>%s</p></div>'
            '<span class="sec-obs">%d 个私有任务</span></div>'
            '<div class="sec-grid">%s</div></div>'
            % (sec, sec, html.escape(str(m.get("num") or "")),
               html.escape(m.get("title") or sec), html.escape(m.get("sub") or ""),
               len(items), cards))

    n_with = sum(1 for w in ws if w.get("metrics"))
    dates = [r.get("obs_date") for r in latest.values() if r.get("obs_date")]
    asof = max(dates) if dates else "—"
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    doc = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Private Tasks · 私有任务展示区</title>
<style>%s</style>
</head>
<body>
<div class="wrap">

<header class="top">
  <h1>Private Tasks<small>私有任务展示区 · precious-task（与公开看板分开两套页面）</small></h1>
  <div class="top-meta">
    <div><b>%s</b><span>最新数据日</span></div>
    <div><b>%d</b><span>私有任务</span></div>
    <div><b>%d</b><span>带指标</span></div>
    <div><b><a href="index.html">公开看板 →</a></b><span>另一套页面</span></div>
  </div>
</header>

<nav class="jump">%s</nav>

%s

<footer>
  <p>只列 <code>visibility=private</code> 的任务（含旧别名 <code>internal</code>）。
  公有任务在另一套页面 <a href="index.html">index.html</a>，两页互不混排。</p>
  <p>指标来自 <code>data_hub.tech</code>，与公开页同一口径；库里没有的指标不显示，
  不补零、不拿旧值顶替。构建时间 %s。</p>
</footer>

</div>
</body>
</html>
""" % (B.CSS, html.escape(str(asof)), len(ws), n_with,
       "".join(nav) or "<span class=\"sm\">（没有可显示的板块）</span>",
       "".join(blocks) or '<div class="sec-wrap"><div class="sec-head">'
       '<span class="sec-num">—</span><div><h2>暂无私有任务</h2>'
       '<p>确认 CASE_EXTRA_DIRS 是否挂上私有仓</p></div></div></div>',
       now)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(doc)
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "private.html"))
    a = ap.parse_args()
    p = build_private(a.out)
    print("已生成 %s  %.1f KB" % (p, os.path.getsize(p) / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())