#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""case/task_paths.py —— 路径口径唯一入口（wide_t1）

三段式：环境变量 → 探测 → 兜底。**不许写死宿主机路径**。

    WIDE_WS / WIDE_CACHE   模型缓存根（= 原宿主机 workspace 下的 .cache 那片）
    CASE_DATA_DIR          任务 data/（框架注入）
    CASE_OUT_DIR           任务 out/（框架注入）

为什么模型缓存要单独一个根
--------------------------
宽基 T+1 依赖三块**跨天保留的状态**，体积与内容都不是「一次运行产物」：

    <WS>/.cache/chan_zs_t1/panel_<指数>.csv   walk-forward 历史预测面板（必需，
                                              没有它 today_dual 算不出预测）
    <WS>/.cache/chan_zs_t1/dual_ledger.db     双轨台账（累年结算历史，必需）
    <WS>/.cache/weekly_forecast/raw/idx_*.csv 五宽基日线（可重建，但重建慢）
    <WS>/.cache/wide_ens/                     统一选模的 ms/enh/train 候选曲线

所以它们放 data/ 之外：data/ 会被 check 阶段当「本次产出」校验新鲜度，
把累年台账放进去会被误判成「旧文件」。README「跨天保留状态」一节有清单。
"""
from __future__ import annotations

import os

HERE = os.path.dirname(os.path.abspath(__file__))
CASE_DIR = os.path.dirname(HERE)                       # …/case
LIB = os.path.join(CASE_DIR, "_shared", "wide_ens_lib")


def _env(*names):
    for n in names:
        v = (os.environ.get(n) or "").strip()
        if v:
            return v
    return ""


def lib_dir():
    """共享库目录（wide_ens / chan_zs_t1 / wide_daily 三个模块根都在下面）。"""
    return LIB


def cache_root():
    """模型缓存根（wsenv.WS）。"""
    env = _env("WIDE_WS", "WIDE_CACHE")
    if env:
        return env
    return os.path.join(LIB, "cache")


def data_dir():
    return _env("CASE_DATA_DIR") or os.path.join(HERE, "data")


def out_dir():
    return _env("CASE_OUT_DIR") or os.path.join(HERE, "out")


def ensure():
    for p in (data_dir(), out_dir(), cache_root()):
        os.makedirs(p, exist_ok=True)


def py_paths():
    """共享库里所有「模块根」目录（子进程 PYTHONPATH 用）。"""
    L = lib_dir()
    return [L,
            os.path.join(L, "wide_ens"),
            os.path.join(L, "chan_zs_t1"),
            os.path.join(L, "wide_daily"),
            os.path.join(L, "bin"),
            os.path.join(L, "research_wf")]


def env_for_child():
    """子进程环境：把缓存根统一成 WIDE_WS，子模块 via wsenv 自动继承。"""
    return {"WIDE_WS": cache_root(),
            "CASE_DATA_DIR": data_dir(),
            "CASE_OUT_DIR": out_dir(),
            "OMP_NUM_THREADS": os.environ.get("OMP_NUM_THREADS", "2"),
            "MKL_NUM_THREADS": os.environ.get("MKL_NUM_THREADS", "2"),
            "TZ": os.environ.get("TZ", "Asia/Shanghai")}


if __name__ == "__main__":
    for k, v in (("lib_dir", lib_dir()), ("cache_root", cache_root()),
                 ("data_dir", data_dir()), ("out_dir", out_dir())):
        print("%-12s %-4s %s" % (k, "存在" if os.path.isdir(v) else "缺失", v))
