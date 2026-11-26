"""
Tushare 统一初始化（代理版）
所有脚本统一: from tushare_init import pro, ts
确保 token 和代理地址正确配置
"""
import tushare as ts

_TOKEN = "1f7d5d325804a92b869963036fab5e5cb71e7820e7d60903ee9952b1"
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
