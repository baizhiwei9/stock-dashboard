# -*- coding: utf-8 -*-
"""动力学MACD a-滤波对比: a_span=1(原版) / 3 / 5
2023 IC 中证500期货回测, 与 bt_ic_2023.py 同构
"""
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "signals"))
import daily_views as DV
from signals import dxke_smooth
from bt_ic_2023 import backtest_futures, load_ic

INIT_CAP = 500_000.0
FEE_RATE = 0.000023
MAINT = 0.5
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "backtest_out")
os.makedirs(OUT, exist_ok=True)


def main():
    df = load_ic()
    d = df.copy(); d.insert(0, "date", d.index)
    close23 = df["close"]["2023"]

    rows = []
    for a_span in [1, 3, 5, 8, 13]:
        s = dxke_smooth.daily_views(d, a_span=a_span)
        s23 = s.reindex(close23.index).fillna(0).astype(int)
        m, nav, segs = backtest_futures(s23, close23)
        m["a_span"] = a_span
        rows.append(m)

    dfm = pd.DataFrame(rows)
    dfm = dfm[["a_span", "总收益%", "年化%", "最大回撤%", "日胜率%", "段胜率%",
               "交易段数", "持仓天数", "强平次数", "期末权益"]]
    print("===== 2023 IC 动力学MACD a-滤波对比 (50万本金) =====")
    print(dfm.round(2).to_string(index=False))
    dfm.to_csv(os.path.join(OUT, "dxke_a_smooth.csv"), index=False, encoding="utf-8-sig")

    # 验证 a_span=1 与原版对齐
    s_old = DV.dxke_daily(d)
    s_old23 = s_old.reindex(close23.index).fillna(0).astype(int)
    m_old, _, _ = backtest_futures(s_old23, close23)
    print(f"\n原版 dxke_daily 收益: {m_old['总收益%']:.2f}%")

if __name__ == "__main__":
    main()
