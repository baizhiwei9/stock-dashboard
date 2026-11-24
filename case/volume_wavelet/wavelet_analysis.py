import akshare as ak
import pandas as pd
import numpy as np
import pywt
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

print("Step 1: Fetching 510300 data...")
# 获取510300 ETF历史数据
try:
    df = ak.fund_etf_hist_em(symbol="510300", period="daily", 
                              start_date="20190101", end_date="20260916",
                              adjust="qfq")
    print(f"Data fetched: {len(df)} rows")
    print(df.head())
    print(df.tail())
    df.to_csv("/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0/510300_data.csv", index=False)
    print("Data saved to CSV")
except Exception as e:
    print(f"Error fetching data: {e}")
    # Try alternative source
    try:
        df = ak.stock_zh_index_daily(symbol="sh510300")
        df = df[df.index >= '2019-01-01']
        df = df[df.index <= '2026-09-16']
        print(f"Alternative data fetched: {len(df)} rows")
        df.to_csv("/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0/510300_data.csv")
    except Exception as e2:
        print(f"Alternative also failed: {e2}")
