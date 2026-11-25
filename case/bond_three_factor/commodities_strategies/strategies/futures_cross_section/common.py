# -*- coding: utf-8 -*-
"""横截面策略本地路径与日志。数据获取统一由 data.py 经 DataHub 完成。"""
from __future__ import annotations

import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "data" / "cross_section"
RAW = DATA / "raw"
OUT = DATA / "out"
for _d in (DATA, RAW, OUT):
    _d.mkdir(parents=True, exist_ok=True)


def log(*args, **kwargs):
    print(time.strftime("%H:%M:%S"), *args, **kwargs, flush=True)
