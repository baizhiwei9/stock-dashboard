"""
Tushare API 初始化配置
======================
所有需要调用Tushare数据的脚本都 import 本文件使用 init_tushare()

使用方式:
    from ts_config import init_tushare
    pro = init_tushare()
    df = pro.moneyflow_ind_ths(...)
    
    # pro_bar 需要额外传 api=pro
    import tushare as ts
    df = ts.pro_bar(api=pro, ts_code="000001.SZ", limit=3)

积分: 15000 (可调用 moneyflow_ind_ths=6000分, moneyflow=2000分)
代理: https://ttx.dailyfetch.top/
"""

import os
import sys

import tushare as ts

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
_LIB = os.path.join(_ROOT, "case", "_lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

import app_secrets  # noqa: E402

# import 期不因缺配置崩溃；真正取数时 init_tushare() 会因空 token 明确失败。
TS_TOKEN = app_secrets.get("tushare.token", "")
TS_HTTP_URL = "https://ttx.dailyfetch.top/"


def init_tushare():
    """初始化Tushare pro_api, 返回pro对象"""
    pro = ts.pro_api(TS_TOKEN)
    pro._DataApi__http_url = TS_HTTP_URL
    return pro
