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

import tushare as ts

TS_TOKEN = '1f7d5d325804a92b869963036fab5e5cb71e7820e7d60903ee9952b1'
TS_HTTP_URL = "https://ttx.dailyfetch.top/"


def init_tushare():
    """初始化Tushare pro_api, 返回pro对象"""
    pro = ts.pro_api(TS_TOKEN)
    pro._DataApi__http_url = TS_HTTP_URL
    return pro
