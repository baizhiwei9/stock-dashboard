"""
Tushare 统一初始化（代理版）
所有脚本统一: from tushare_init import pro, ts
确保 token 和代理地址正确配置
"""
import os
import sys

import tushare as ts

# token 不入库：环境变量 TUSHARE_TOKEN / COZE_SEC_TUSHARE_TOKEN → case/_lib/secrets.json。
def _tushare_token():
    for _name in ("TUSHARE_TOKEN", "COZE_SEC_TUSHARE_TOKEN"):
        _v = (os.environ.get(_name) or "").strip()
        if _v:
            return _v
    try:
        _d = os.path.dirname(os.path.abspath(__file__))
        for _ in range(6):
            _lib = os.path.join(_d, "_lib")
            if os.path.isfile(os.path.join(_lib, "hub_root.py")):
                if _lib not in sys.path:
                    sys.path.insert(0, _lib)
                from hub_root import tushare_token
                return tushare_token()
            _d = os.path.dirname(_d)
    except Exception:
        pass
    return ""

_TOKEN = _tushare_token()
if not _TOKEN:
    raise RuntimeError("未配置 Tushare token：设 TUSHARE_TOKEN 或 case/_lib/secrets.json")
_PROXY_URL = "https://ttx.dailyfetch.top/"

pro = ts.pro_api(_TOKEN)
pro._DataApi__http_url = _PROXY_URL


def safe_call(func, **kwargs):
    """安全调用，失败返回None"""
    try:
        return func(**kwargs)
    except Exception as e:
        print(f"[tushare] {func.__name__} 失败: {e}")
        return None
