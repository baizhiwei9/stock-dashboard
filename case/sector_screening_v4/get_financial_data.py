import tushare as ts
import pandas as pd
from datetime import datetime

# 设置tushare token
token = "a7c1ab22e3162aac86bf77323755b2d99e9cadfad4ae631a50574265"
pro = ts.pro_api(token)

# 股票代码
stocks = {
    "金海通": "603195.SH",
    "臻镭科技": "688270.SH"
}

print("=" * 70)
print("查询最新财务数据（2024年年报）")
print("=" * 70)

for name, code in stocks.items():
    print(f"\n【{name} ({code})】")
    print("-" * 60)
    
    # 获取利润表数据
    try:
        # 使用年度报告获取完整数据
        df = pro.income(ts_code=code, period='20241231', fields='ts_code,ann_date,end_date,revenue,revenue_prev,total_profit,net_profit,net_profit_prev,basic_eps')
        if df is not None and len(df) > 0:
            row = df.iloc[0]
            print(f"公告日期: {row['ann_date']}")
            print(f"  营业收入: {row['revenue']/1e8:.2f} 亿元")
            print(f"  净利润: {row['net_profit']/1e8:.2f} 亿元")
            print(f"  基本每股收益: {row['basic_eps']:.2f} 元")
            
            if row['revenue_prev'] and row['revenue_prev'] > 0:
                revenue_growth = (row['revenue'] - row['revenue_prev']) / row['revenue_prev'] * 100
                print(f"  营收增速: {revenue_growth:.2f}%")
            
            if row['net_profit_prev'] and row['net_profit_prev'] > 0:
                profit_growth = (row['net_profit'] - row['net_profit_prev']) / row['net_profit_prev'] * 100
                print(f"  净利润增速: {profit_growth:.2f}%")
                
    except Exception as e:
        print(f"  利润表数据获取失败: {e}")
        
    # 获取财务指标
    try:
        df_indicator = pro.fina_indicator(ts_code=code, period='20241231', fields='ts_code,ann_date,end_date,roe,roic,gross_profit_margin,net_profit_margin,debt_to_assets')
        if df_indicator is not None and len(df_indicator) > 0:
            ind = df_indicator.iloc[0]
            print(f"\n  财务指标:")
            if pd.notna(ind.get('roe')):
                print(f"    ROE(净资产收益率): {ind['roe']:.2f}%")
            if pd.notna(ind.get('gross_profit_margin')):
                print(f"    毛利率: {ind['gross_profit_margin']:.2f}%")
            if pd.notna(ind.get('net_profit_margin')):
                print(f"    净利率: {ind['net_profit_margin']:.2f}%")
    except Exception as e:
        print(f"  财务指标获取失败: {e}")

print("\n" + "=" * 70)
print("查询完成")
