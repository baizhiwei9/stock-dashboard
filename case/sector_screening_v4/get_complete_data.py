import tushare as ts
import pandas as pd
from datetime import datetime

token = "a7c1ab22e3162aac86bf77323755b2d99e9cadfad4ae631a50574265"
pro = ts.pro_api(token)

stocks = {"金海通": "603195.SH", "臻镭科技": "688270.SH"}

print("=" * 75)
print("金海通 & 臻镭科技 最新数据汇总（2024年年报 + 2026年行情）")
print("=" * 75)

for name, code in stocks.items():
    print(f"\n{'='*75}")
    print(f"【{name} ({code})】")
    print("=" * 75)
    
    # 获取利润表 - 2024和2023对比
    df_2024 = pro.income(ts_code=code, period='20241231')
    df_2023 = pro.income(ts_code=code, period='20231231')
    
    if df_2024 is not None and len(df_2024) > 0:
        r2024 = df_2024.iloc[0]
        revenue_2024 = r2024['revenue']
        nprofit_2024 = r2024.get('n_profit') or r2024.get('continued_net_profit') or r2024.get('parent_netprofit')
        
        print(f"\n📊 2024年年报:")
        print(f"   公告日期: {r2024['ann_date']}")
        print(f"   营业收入: {revenue_2024/1e8:.2f} 亿元")
        if nprofit_2024:
            print(f"   净利润: {nprofit_2024/1e8:.2f} 亿元")
        
        # 2023年数据
        if df_2023 is not None and len(df_2023) > 0:
            r2023 = df_2023.iloc[0]
            revenue_2023 = r2023['revenue']
            nprofit_2023 = r2023.get('n_profit') or r2023.get('continued_net_profit') or r2023.get('parent_netprofit')
            
            if revenue_2023 and revenue_2023 > 0:
                rev_growth = (revenue_2024 - revenue_2023) / revenue_2023 * 100
                print(f"   营收增速: {rev_growth:+.2f}%")
            
            if nprofit_2023 and nprofit_2023 > 0 and nprofit_2024:
                profit_growth = (nprofit_2024 - nprofit_2023) / nprofit_2023 * 100
                print(f"   净利润增速: {profit_growth:+.2f}%")
        else:
            print("   (2023年对比数据未获取到)")
    
    # 获取财务指标
    df_ind = pro.fina_indicator(ts_code=code, period='20241231')
    if df_ind is not None and len(df_ind) > 0:
        ind = df_ind.iloc[0]
        print(f"\n📈 关键财务指标:")
        if pd.notna(ind.get('roe')):
            print(f"   ROE(净资产收益率): {ind['roe']:.2f}%")
        if pd.notna(ind.get('gross_profit_margin')):
            print(f"   毛利率: {ind['gross_profit_margin']:.2f}%")
        if pd.notna(ind.get('net_profit_margin')):
            print(f"   净利率: {ind['net_profit_margin']:.2f}%")
    
    # 获取2026年行情
    df_daily = pro.daily(ts_code=code, start_date='20260101', end_date='20260630')
    if df_daily is not None and len(df_daily) > 0:
        df_daily = df_daily.sort_values('trade_date')
        first = df_daily.iloc[0]
        last = df_daily.iloc[-1]
        
        change = (last['close'] - first['close']) / first['close'] * 100
        
        print(f"\n📉 2026年行情:")
        print(f"   年初价格: {first['close']:.2f} 元 ({first['trade_date']})")
        print(f"   当前价格: {last['close']:.2f} 元 ({last['trade_date']})")
        print(f"   年内涨幅: {change:+.2f}%")

print(f"\n{'='*75}")
print("数据来源: Tushare | 查询时间: " + datetime.now().strftime('%Y-%m-%d %H:%M'))
print("=" * 75)
