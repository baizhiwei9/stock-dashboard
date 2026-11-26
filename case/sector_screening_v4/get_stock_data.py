import os
import sys
import tushare as ts
import pandas as pd
from datetime import datetime

# 设置tushare token
def _hub_lib_dir(_f=__file__):
    _d = os.path.dirname(os.path.abspath(_f))
    for _ in range(6):
        _lib = os.path.join(_d, "_lib")
        if os.path.isfile(os.path.join(_lib, "hub_root.py")):
            return _lib
        _d = os.path.dirname(_d)
    return ""

_HUB_LIB_DIR = _hub_lib_dir()
if _HUB_LIB_DIR and _HUB_LIB_DIR not in sys.path:
    sys.path.insert(0, _HUB_LIB_DIR)
from hub_root import tushare_token as _tushare_token, smtp_password as _smtp_password  # noqa: E402

token = _tushare_token()
pro = ts.pro_api(token)

# 股票代码
stocks = {
    "金海通": "603195.SH",
    "臻镭科技": "688270.SH"
}

print("=" * 60)
print("查询最新财务数据和行情数据")
print("=" * 60)

for name, code in stocks.items():
    print(f"\n【{name} ({code})】")
    print("-" * 50)
    
    # 1. 获取2024年年报财务数据
    try:
        # 获取最新财务数据
        df_fina = pro.fina_indicator(ts_code=code, start_date='20240101', end_date='20250430')
        if df_fina is not None and len(df_fina) > 0:
            # 取最新的财报
            latest = df_fina.iloc[0]
            print(f"最新财报期: {latest['ann_date']} (报告期: {latest['end_date']})")
            
            # 营收增速
            if 'roe' in latest.index:
                roe = latest.get('roe')
                if roe is not None:
                    print(f"ROE: {roe:.2f}%")
            
            # 获取营收和利润数据
            df_income = pro.income(ts_code=code, start_date='20230101', end_date='20250430', fields='ts_code,ann_date,f_ann_date,end_date,revenue,revenue_prev,net_profit,net_profit_prev')
            if df_income is not None and len(df_income) > 0:
                # 找到2024年年报
                for idx, row in df_income.iterrows():
                    if str(row['end_date']).startswith('2024'):
                        print(f"\n2024年年报数据:")
                        print(f"  营业收入: {row['revenue']/1e8:.2f} 亿元")
                        print(f"  净利润: {row['net_profit']/1e8:.2f} 亿元")
                        
                        if row['revenue_prev'] and row['revenue_prev'] > 0:
                            revenue_growth = (row['revenue'] - row['revenue_prev']) / row['revenue_prev'] * 100
                            print(f"  营收增速: {revenue_growth:.2f}%")
                        
                        if row['net_profit_prev'] and row['net_profit_prev'] > 0:
                            profit_growth = (row['net_profit'] - row['net_profit_prev']) / row['net_profit_prev'] * 100
                            print(f"  净利润增速: {profit_growth:.2f}%")
                        break
    except Exception as e:
        print(f"财务数据获取失败: {e}")
    
    # 2. 获取2026年至今的行情数据计算涨幅
    try:
        # 获取2026年第一个交易日到今天的数据
        df_daily = pro.daily(ts_code=code, start_date='20260101', end_date=datetime.now().strftime('%Y%m%d'))
        if df_daily is not None and len(df_daily) > 0:
            # 按日期排序，取最新和最早价格
            df_daily = df_daily.sort_values('trade_date')
            
            first_day = df_daily.iloc[0]
            last_day = df_daily.iloc[-1]
            
            first_close = first_day['close']
            last_close = last_day['close']
            
            change_pct = (last_close - first_close) / first_close * 100
            
            print(f"\n2026年行情:")
            print(f"  年初价格: {first_close:.2f} 元 ({first_day['trade_date']})")
            print(f"  当前价格: {last_close:.2f} 元 ({last_day['trade_date']})")
            print(f"  年内涨幅: {change_pct:+.2f}%")
        else:
            print("2026年暂无行情数据")
    except Exception as e:
        print(f"行情数据获取失败: {e}")

print("\n" + "=" * 60)
print("查询完成")
