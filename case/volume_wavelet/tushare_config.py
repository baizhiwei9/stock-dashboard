"""
Tushare 代理配置初始化模块
用法: from tushare_config import pro, ts
"""
import os, sys

# 确保 tushare 包可导入（沙箱环境中 tushare 位于工作区 pylibs/）
_ws = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_pylibs = os.path.join(_ws, 'pylibs')
if os.path.isdir(_pylibs) and _pylibs not in sys.path:
    sys.path.insert(0, _pylibs)

import tushare as ts

# 初始化带代理的 Tushare Pro API
pro = ts.pro_api('1f7d5d325804a92b869963036fab5e5cb71e7820e7d60903ee9952b1')
pro._DataApi__http_url = "https://ttx.dailyfetch.top/"

# 测试连接（首次导入时自动验证）
try:
    df = pro.index_basic(limit=1)
    print(f"Tushare代理连接成功，返回 {len(df)} 行数据")
except Exception as e:
    print(f"Tushare代理连接失败: {e}")

# ========== 资金单位统一换算（所有脚本复用的唯一入口） ==========
# tushare fund_daily 的 amount 单位为「千元」，换算为「元」需乘以 1000。
# 历史教训：曾多处内联 ×10000 导致成交额放大 10 倍，现已收敛为本入口，禁止再内联换算系数。
AMOUNT_TO_YUAN = 1000

def amount_to_yuan(amount):
    """将 tushare fund_daily 的 amount（千元）换算为元。

    兼容标量 / numpy 标量 / pandas Series，返回同类型换算结果。
    """
    return amount * AMOUNT_TO_YUAN
