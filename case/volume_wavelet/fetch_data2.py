import akshare as ak
import pandas as pd
import time

# 尝试 fund_etf_hist_em 再次
print("Trying fund_etf_hist_em with qfq...")
try:
    time.sleep(2)
    df = ak.fund_etf_hist_em(symbol="510300", period="daily", 
                              start_date="20190101", end_date="20260916",
                              adjust="qfq")
    print(f"Success! {len(df)} rows")
    print(df.head(3))
    df.to_csv("/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0/510300_data.csv", index=False, encoding='utf-8-sig')
    print("Saved successfully")
except Exception as e:
    print(f"Failed: {e}")
    
    # Try without qfq
    print("\nTrying without adjust...")
    try:
        time.sleep(2)
        df = ak.fund_etf_hist_em(symbol="510300", period="daily", 
                                  start_date="20190101", end_date="20260916")
        print(f"Success! {len(df)} rows")
        df.to_csv("/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0/510300_data.csv", index=False, encoding='utf-8-sig')
        print("Saved successfully")
    except Exception as e2:
        print(f"Also failed: {e2}")
        
        # Try stock_zh_a_hist
        print("\nTrying stock_zh_a_hist...")
        try:
            time.sleep(2)
            df = ak.stock_zh_a_hist(symbol="510300", period="daily", 
                                    start_date="20190101", end_date="20260916")
            print(f"Success! {len(df)} rows")
            df.to_csv("/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0/510300_data.csv", index=False, encoding='utf-8-sig')
        except Exception as e3:
            print(f"Also failed: {e3}")
