# -*- coding: utf-8 -*-
"""回测专用面板：12 标的（剔除恒生科技）2019-01 起收盘价对齐面板
来源：新浪（全历史）优先，东财兜底
"""
import os, sys, time
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import data_fetch  # noqa
from config import STOM_POOL

POOL = [c for c in STOM_POOL if c != "sh513180"]
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "backtest_panel_2019.csv")

def main():
    closes = {}
    for code in POOL:
        ok = False
        for fetcher in (data_fetch.fetch_sina, data_fetch.fetch_em):
            try:
                df = fetcher(code)
                if df is not None and not df.empty and len(df) > 1000:
                    df = df[df["date"] >= "2019-01-01"]
                    closes[code] = df.set_index("date")["close"]
                    print(f"{code}: {fetcher.__name__} {len(df)} 根 {df['date'].min().date()}~{df['date'].max().date()}")
                    ok = True
                    break
            except Exception as e:
                print(f"  {code} {fetcher.__name__} fail: {type(e).__name__} {str(e)[:60]}")
            time.sleep(0.3)
        if not ok:
            print(f"[X] {code} 拉取失败")
            return 1
    panel = pd.DataFrame(closes).sort_index()
    panel = panel.dropna(how='all')
    panel.to_csv(OUT)
    print(f"面板 {panel.shape} 已保存: {OUT}  {panel.index[0].date()}~{panel.index[-1].date()}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
