import os
import sys
import tushare as ts
import pandas as pd
from datetime import datetime

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

stocks = {"金海通": "603195.SH", "臻镭科技": "688270.SH"}

for name, code in stocks.items():
    print(f"\n【{name} ({code})】")
    print("=" * 60)
    
    # 获取利润表所有字段
    df = pro.income(ts_code=code, period='20241231')
    if df is not None and len(df) > 0:
        row = df.iloc[0]
        print(f"公告日期: {row['ann_date']}")
        print(f"营业收入(revenue): {row['revenue']/1e8:.2f} 亿元")
        
        # 查找净利润相关字段
        for col in df.columns:
            if 'profit' in col.lower() and 'net' in col.lower():
                val = row[col]
                if pd.notna(val):
                    print(f"{col}: {val/1e8:.2f} 亿元")
        
        # 显示同比数据
        print("\n同比数据:")
        for col in df.columns:
            if 'prev' in col.lower() or 'yoy' in col.lower():
                val = row[col]
                if pd.notna(val):
                    print(f"  {col}: {val}")
                    
        # 计算增速
        if pd.notna(row.get('revenue')) and pd.notna(row.get('revenue_prev')) and row['revenue_prev'] > 0:
            rev_growth = (row['revenue'] - row['revenue_prev']) / row['revenue_prev'] * 100
            print(f"\n营收增速: {rev_growth:.2f}%")
        
        # 尝试获取净利润
        nprofit = row.get('n_profit') or row.get('net_profit') or row.get('parent_netprofit')
        nprofit_prev = row.get('n_profit_prev') or row.get('net_profit_prev') or row.get('parent_netprofit_prev')
        
        if nprofit is None:
            # 查看所有列
            for col in df.columns:
                if 'n_profit' in col.lower() or 'parent' in col.lower():
                    nprofit = row.get(col)
                    nprofit_prev = row.get(col.replace('parent_netprofit', 'parent_netprofit_prev').replace('n_income', 'n_income_prev'))
                    break
        
        if nprofit and pd.notna(nprofit):
            print(f"净利润: {nprofit/1e8:.2f} 亿元")
            if nprofit_prev and pd.notna(nprofit_prev) and nprofit_prev > 0:
                profit_growth = (nprofit - nprofit_prev) / nprofit_prev * 100
                print(f"净利润增速: {profit_growth:.2f}%")

