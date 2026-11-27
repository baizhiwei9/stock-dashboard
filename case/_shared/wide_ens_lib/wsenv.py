#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wsenv —— 宽基三个任务（wide_t1 / wide_t5 / wide_ens_weekly）的公共环境层

为什么要这一层
==============
这三套脚本原来长在宿主机的 ``/root/.openclaw/workspace`` 里，模块里到处是

    WS = '/root/.openclaw/workspace'
    CACHE = os.path.join(WS, '.cache/wide_ens')

搬进 ``case/`` 之后：**仓库里不许再出现宿主机绝对路径**，但五个宽基任务又要
共用同一份模型缓存（today_dual 的 panel、unified_select 的 ms/enh/train csv、
wide_daily 的 h*.csv），不能各存一份。

所以把「WS 是什么」收成一处：

    WIDE_WS（env）    → 显式指定缓存根（CI / 换机器时用）
    否则              → <本库目录>/cache       （库内默认位置，gitignore）

改完之后各模块只写 ``from wsenv import WS``，其余 ``os.path.join(WS, '.cache/...')``
一行都不用动 —— 迁移改动面最小，且口径唯一。

DataHub 入口
============
``hub()`` 负责把 DataHub 挂进 ``sys.path`` 并 import（走 ``case/_lib/paths.py``
的 ``ensure_hub_on_path()``，与框架其它任务同一口径，不另立规则）。
取数**只走 DataHub**，任务侧不开线程池、不直连 akshare / tushare / CFTC。

落盘口径
========
    cache_root()  模型缓存（.cache/wide_ens、.cache/chan_zs_t1、.cache/weekly_forecast/raw）
    data_dir()    任务自己的 data/（CASE_DATA_DIR → <case>/data）
    out_dir()     任务自己的 out/ （CASE_OUT_DIR  → <case>/out）

⚠️ 跨任务共享：wide_t5 / wide_ens_weekly / wide_t1 读的是**同一个** cache_root，
   所以三个任务不要并行跑（原 crontab 也是错开 15:30 / 20:00 / 次日 08:30）。
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CASE_DIR = os.path.dirname(os.path.dirname(HERE))          # …/case

# 子目录都是「模块根」：wide_ens/、chan_zs_t1/、wide_daily/、bin/、research_wf/
MODULE_DIRS = (
    HERE,
    os.path.join(HERE, "wide_ens"),
    os.path.join(HERE, "chan_zs_t1"),
    os.path.join(HERE, "wide_daily"),
    os.path.join(HERE, "bin"),
    os.path.join(HERE, "research_wf"),
)

for _d in MODULE_DIRS:
    if _d not in sys.path:
        sys.path.insert(0, _d)


def _env(*names):
    for n in names:
        v = (os.environ.get(n) or "").strip()
        if v:
            return v
    return ""


def cache_root():
    """模型缓存根（等价于原来的宿主机 workspace）。"""
    env = _env("WIDE_WS", "WIDE_CACHE")
    if env:
        return env
    return os.path.join(HERE, "cache")


WS = cache_root()          # ← 各模块 `from wsenv import WS` 用的就是这个


def data_dir(default_case: str = "") -> str:
    """任务 data/：框架注入 CASE_DATA_DIR；手工跑退到 <case>/data。"""
    env = _env("CASE_DATA_DIR")
    if env:
        return env
    if default_case:
        return os.path.join(CASE_DIR, default_case, "data")
    return os.path.join(HERE, "data")


def out_dir(default_case: str = "") -> str:
    env = _env("CASE_OUT_DIR")
    if env:
        return env
    if default_case:
        return os.path.join(CASE_DIR, default_case, "out")
    return os.path.join(HERE, "out")


def ensure_dirs(*paths: str) -> None:
    for p in paths:
        if p:
            os.makedirs(p, exist_ok=True)


# ── DataHub ────────────────────────────────────────────────────────────────
def hub():
    """import data_hub（唯一取数入口）。

    先走 case/_lib/paths.py 的 ensure_hub_on_path()（与框架同口径）；
    该模块不在（case 被单独拷出去跑）时退回 DATA_HUB_PATH 环境变量。
    """
    lib = os.path.join(CASE_DIR, "_lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    try:
        import paths  # noqa: PLC0415  case/_lib/paths.py
        paths.ensure_hub_on_path()
    except Exception:                                            # noqa: BLE001
        root = _env("DATA_HUB_PATH", "DATAHUB_DIR", "EDB_REPO")
        if root and root not in sys.path:
            sys.path.insert(0, root)
    import data_hub                                              # noqa: PLC0415
    return data_hub


# 五只宽基（与 task.json 的 metrics[].sec 一一对应）
INDICES = [
    ("000300.SH", "沪深300", "idx_hs300.csv"),
    ("000905.SH", "中证500", "idx_zz500.csv"),
    ("000852.SH", "中证1000", "idx_zz1000.csv"),
    ("000688.SH", "科创50", "idx_kc50.csv"),
    ("399006.SZ", "创业板指", "idx_cyb.csv"),
]


def raw_dir() -> str:
    """行情/因子 csv 目录（= 原 .cache/weekly_forecast/raw）。"""
    p = os.path.join(WS, ".cache", "weekly_forecast", "raw")
    os.makedirs(p, exist_ok=True)
    return p


def state_dir(*rel: str) -> str:
    p = os.path.join(WS, ".cache", *rel)
    os.makedirs(p, exist_ok=True)
    return p


if __name__ == "__main__":
    print("WS        ", WS)
    print("cache_root", cache_root())
    print("raw_dir   ", raw_dir())
    print("data_dir  ", data_dir())
    print("out_dir   ", out_dir())
    try:
        dh = hub()
        print("data_hub  ", getattr(dh, "__file__", "?"))
    except Exception as e:                                       # noqa: BLE001
        print("data_hub  导入失败：%s" % e)
