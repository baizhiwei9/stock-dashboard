"""检查今天是否为A股交易日 - 通过akshare获取交易日历"""
import sys
import os
import datetime

# 复用日报脚本的本地依赖目录(.deps)，否则 akshare 导入失败会退化为"仅排除周末"，
# 遇法定节假日会误判为交易日
_DEPS = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.deps')
if os.path.isdir(_DEPS):
    sys.path.insert(0, _DEPS)

def is_trading_day():
    """判断今天是否是A股交易日"""
    today = datetime.date.today()
    
    # 周末直接返回False
    if today.weekday() >= 5:
        return False
    
    # 检查节假日 - 用akshare的交易日历
    try:
        import akshare as ak
        # 获取今年和明年的交易日历
        year = today.year
        df = ak.tool_trade_date_hist_sina()
        trade_dates = set(df['trade_date'].astype(str).tolist())
        today_str = today.strftime('%Y-%m-%d')
        return today_str in trade_dates
    except Exception as e:
        # 降级：如果API失败，用周末判断（周一到周五执行）
        print(f"[WARNING] 获取交易日历失败: {e}，降级为仅排除周末")
        return today.weekday() < 5

if __name__ == '__main__':
    if is_trading_day():
        print("TRADING_DAY")
        sys.exit(0)
    else:
        print("NOT_TRADING_DAY")
        sys.exit(1)
