#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""extract.py — 从任务产物里自动抽取可上面板的指标

不是每个任务都愿意改代码去 ctx.emit(...)。这个模块给它们一条零配置的路：
把 out/ 下的 json 产物扫一遍，取出标量与短文本字段（深度 ≤ 2），变成指标行。

    from extract import auto_extract
    def emit(ctx):
        return auto_extract(ctx, sec="CN.MACRO")

命名规则：<任务key>.<文件名去扩展>.<字段路径>，例如
    tda_spd.strategy.market.position_pct
    cn_us_inventory.inventory.stage

过滤：
  · 只取 int/float/bool/str（str 长度 ≤ 40，且不是 URL/路径）
  · 跳过 latest.json / check.json / status.json / alerts.json（框架自己的文件）
  · 单任务最多 40 条，避免面板被几百个字段淹没

as_of：只认 json 里自带的 as_of / date / trade_date / obs_date 等字段（数据日）。
       **取不到就跳过该文件**，不拿运行日兜底 —— 否则历史回填的旧数据会被盖上
       今天的日期，看板上「今天全都有数」全是假的，而且能躲过 freshness 校验。
       任务确实拿不到日期时，可以在 pipeline.py 里显式传 as_of="2026-11-24"。
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys
from typing import Any, Dict, List

SKIP = {"latest.json", "check.json", "status.json", "alerts.json", "latest.sample.json"}
MAX_POINTS = 40
DATE_KEYS = ("as_of", "trade_date", "date", "obs_date", "data_date", "update_date")
PATH_RE = re.compile(r"^(/|[A-Za-z]:\\|https?://)")


def _scalar(v) -> bool:
    if isinstance(v, bool):
        return True
    if isinstance(v, (int, float)):
        return True
    return isinstance(v, str) and 0 < len(v) <= 40 and not PATH_RE.match(v)


def _walk(obj, prefix: str, out: List, depth: int = 0):
    if depth > 2 or len(out) >= MAX_POINTS:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if _scalar(v):
                out.append(("%s.%s" % (prefix, k) if prefix else k, v))
            elif isinstance(v, (dict, list)):
                _walk(v, "%s.%s" % (prefix, k) if prefix else k, out, depth + 1)
    elif isinstance(obj, list) and obj and _scalar(obj[0]):
        for i, v in enumerate(obj[:5]):
            out.append(("%s[%d]" % (prefix, i), v))


def _as_of(obj) -> str | None:
    """从产物里取数据日；取不到返回 None（调用方跳过，不兜底成运行日）。"""
    if isinstance(obj, dict):
        for k in DATE_KEYS:
            v = obj.get(k)
            if isinstance(v, str) and re.match(r"^\d{4}-\d{2}-\d{2}", v):
                return v[:10]
            if isinstance(v, str) and re.fullmatch(r"\d{8}", v):
                return "%s-%s-%s" % (v[:4], v[4:6], v[6:])
    return None


def auto_extract(ctx, sec: str = "", max_points: int = MAX_POINTS,
                 only: List[str] | None = None, as_of: str = "") -> List[Dict]:
    """扫描 ctx.out_dir 下的 json，返回指标行（key/sec/value/obs_date/quality）。

    as_of 显式给出时（任务自己知道数据日），整批统一用它；否则逐文件读日期字段，
    读不到的文件整份跳过并在 stderr 留痕。
    """
    out_dir = ctx.out_dir
    points: List[Dict] = []
    skipped: List[str] = []
    seen = set()
    for p in sorted(glob.glob(os.path.join(out_dir, "*.json"))):
        name = os.path.basename(p)
        if name in SKIP or (only and name not in only):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                obj = json.load(f)
        except (OSError, ValueError):
            continue
        stem = os.path.splitext(name)[0]
        asof = (as_of or "").strip() or _as_of(obj)
        if not asof:
            skipped.append(name)
            continue
        flat: List = []
        _walk(obj, "", flat)
        for path, v in flat:
            key = "%s.%s" % (ctx.key, "%s.%s" % (stem, path) if path else stem)
            if key in seen:
                continue
            seen.add(key)
            row = {"key": key, "sec": sec or "CN.GENERIC", "obs_date": asof,
                   "name": key, "quality": "ok"}
            if isinstance(v, bool):
                row["value"] = 1.0 if v else 0.0
                row["dec"] = 0
            elif isinstance(v, (int, float)):
                row["value"] = float(v)
                row["dec"] = 2
            else:
                row["value_text"] = v
            points.append(row)
            if len(points) >= max_points:
                break
        if len(points) >= max_points:
            break
    if skipped:
        sys.stderr.write(
            "[extract] %s: %d 个 json 没有数据日期字段，已跳过（不拿运行日兜底）：%s\n"
            % (getattr(ctx, "key", "?"), len(skipped), ", ".join(skipped[:5])))
    return points