# -*- coding: utf-8 -*-
"""会话内薄封装：取数逻辑一律复用共享目录里的 ts_client，本文件不再重复实现

共享实现位置：<用户 workspace>/pylibs/ts_client.py
接入前提：PYTHONPATH 含 <用户 workspace>/pylibs（本文件会自动补上前缀）

用法与共享模块一致：
    from ts_api import init_pro, pro_bar, call
    pro = init_pro()                     # 已指向 https://ttx.dailyfetch.top/
    df  = pro.index_basic(limit=5)
    df2 = pro_bar(ts_code="000001.SZ", limit=3)
    df3 = call("daily_basic", ts_code="000001.SZ", start_date="20221201", end_date="20221231")

令牌不写进代码：环境变量 TS_TOKEN 或 secrets 文件（详见共享模块 docstring）。
"""
import os
import sys

_WORKSPACE = os.environ.get(
    "DUMATE_WORKSPACE",
    "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace")
_SHARED = os.path.join(_WORKSPACE, "pylibs")
if _SHARED not in sys.path:
    sys.path.insert(0, _SHARED)

import ts_client  # noqa: E402  共享实现

BASE_URL = ts_client.BASE_URL


def get_token():
    return ts_client.get_token()


def init_pro(token=None):
    """返回 pro 客户端；显式传入 token 时才本地构造，否则用共享单例。"""
    if token is None:
        return ts_client.pro()
    import tushare as ts
    p = ts.pro_api(token)
    p._DataApi__http_url = BASE_URL
    return p


def pro_bar(pro=None, **kwargs):
    """ts.pro_bar 封装：必须带 api=pro。"""
    if pro is None:
        return ts_client.pro_bar(**kwargs)
    import tushare as ts
    kwargs.pop("api", None)
    return ts.pro_bar(api=pro, **kwargs)


def call(api, **kwargs):
    """带缓存与退避重试的取数（见共享 ts_client.call）。"""
    return ts_client.call(api, **kwargs)


if __name__ == "__main__":
    p = init_pro()
    print("[endpoint]", p._DataApi__http_url)
    print(p.index_basic(limit=3).to_string(index=False))
    print(pro_bar(p, ts_code="000001.SZ", limit=2).to_string(index=False))