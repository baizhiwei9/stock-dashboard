import akshare as ak
import pandas as pd
import time

# 尝试多种方式获取数据
print("Trying method 1: fund_etf_hist_em...")
try:
    df = ak.fund_etf_hist_em(symbol="510300", period="daily", 
                              start_date="20190101", end_date="20260916",
                              adjust="qfq")
    print(f"Success! {len(df)} rows")
    print(df.columns.tolist())
    df.to_csv("/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0/510300_data.csv", index=False)
except Exception as e:
    print(f"Method 1 failed: {e}")
    
    print("\nTrying method 2: stock_zh_index_daily...")
    try:
        df = ak.stock_zh_index_daily(symbol="sh510300")
        df.index = pd.to_datetime(df.index)
        df = df[df.index >= '2019-01-01']
        df = df[df.index <= '2026-09-16']
        print(f"Success! {len(df)} rows")
        df.to_csv("/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0/510300_data.csv")
    except Exception as e2:
        print(f"Method 2 failed: {e2}")
        
        print("\nTrying method 3: stock_zh_a_hist...")
        try:
            df = ak.stock_zh_a_hist(symbol="510300", period="daily", 
                                    start_date="20190101", end_date="20260916")
            print(f"Success! {len(df)} rows")
            df.to_csv("/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0/510300_data.csv", index=False)
        except Exception as e3:
            print(f"Method 3 failed: {e3}")
            
            print("\nTrying method 4: fund_etf_hist_em without adjust...")
            try:
                df = ak.fund_etf_hist_em(symbol="510300", period="daily", 
                                          start_date="20190101", end_date="20260916")
                print(f"Success! {len(df)} rows")
                df.to_csv("/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0/510300_data.csv", index=False)
            except Exception as e4:
                print(f"Method 4 failed: {e4}")
