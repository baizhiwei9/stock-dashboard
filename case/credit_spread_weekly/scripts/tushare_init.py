"""
Tushare 统一初始化（代理版）
所有脚本统一: from tushare_init import pro, ts
确保 token 和代理地址正确配置

token 不再写死：环境变量 → ``case/_lib/hub_root.py`` 的 secrets.json 口径
（``~/.config/stock-dashboard/secrets.json`` 的 ``tushare.token``）。
"""
import os
import sys

import tushare as ts

_PROXY_URL = "https://ttx.dailyfetch.top/"


def _tushare_token():
    """Tushare 代理 token：环境变量 → secrets.json，不再是源码字面量。"""
    for name in ("TUSHARE_TOKEN", "TUSHARE_PROXY_TOKEN", "COZE_SEC_TUSHARE_TOKEN"):
        val = (os.environ.get(name) or "").strip()
        if val:
            return val
    try:
        lib = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "_lib")
        if lib not in sys.path:
            sys.path.insert(0, lib)
        from hub_root import tushare_token
        return tushare_token()
    except Exception:
        return ""


_TOKEN = _tushare_token()
if not _TOKEN:
    raise RuntimeError(
        "Tushare token 未配置：请设置 TUSHARE_TOKEN，或写入 "
        "~/.config/stock-dashboard/secrets.json 的 tushare.token")

pro = ts.pro_api(_TOKEN)
pro._DataApi__http_url = _PROXY_URL


def safe_call(func, **kwargs):
    """安全调用，失败返回None"""
    try:
        return func(**kwargs)
    except Exception as e:
        print(f"[tushare] {func.__name__} 失败: {e}")
        return None
