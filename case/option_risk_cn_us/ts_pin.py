# -*- coding: utf-8 -*-
"""钉住出口IP的 tushare 接入: 复用单一 TCP 连接, 带重试与限速。"""
import sys, time, itertools
import requests
from requests.adapters import HTTPAdapter

SESSION = requests.Session()
_adapter = HTTPAdapter(pool_connections=2, pool_maxsize=4, max_retries=0)
SESSION.mount("https://", _adapter)
SESSION.mount("http://", _adapter)

# 把 tushare 内部使用的 requests.post 替换为复用连接的 Session.post
import tushare.pro.client as _tc
_tc.requests = SESSION

_last = [0.0]
GAP = 0.65


def _throttle():
    dt = time.time() - _last[0]
    if dt < GAP:
        time.sleep(GAP - dt)
    _last[0] = time.time()


def call(func, *args, retries=6, **kwargs):
    """带重试的 tushare 调用; 命中 IP 限频时等待后重试。"""
    last_err = None
    for i in range(retries + 1):
        try:
            _throttle()
            return func(*args, **kwargs)
        except Exception as e:
            last_err = e
            msg = str(e)
            wait = 8 * (i + 1)
            if "ip" in msg or "超限" in msg or "频" in msg:
                wait = 30 * (i + 1)
            print(f"    retry{i} after {wait}s: {msg[:60]}")
            time.sleep(wait)
    raise last_err


def init():
    from ts_config import init_tushare
    return init_tushare()