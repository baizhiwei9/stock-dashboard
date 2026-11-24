# -*- coding: utf-8 -*-
"""参数校准: 用文章披露的全区间信号次数/次均持仓 反推各信号窗口参数"""
import numpy as np
import pandas as pd
import backtest_engine as be

# 文章披露: 全区间(2010-2026.8, 基准50/50) 子策略信号统计
DISCLOSED = {
    's1': dict(n=186, hold_days=22, ann_ret=0.0913, ex=0.0609, ir=0.93, mdd=0.1325, win=0.4866, payoff=2.25),
    's2': dict(n=158, hold_days=25, ann_ret=0.0733, ex=0.0434, ir=0.66, mdd=0.1711, win=0.5283, payoff=1.64),
    's7': dict(n=6,   hold_days=670, ann_ret=0.0696, ex=0.0399, ir=0.61, mdd=0.1186, win=0.8571, payoff=7.5),
    's8': dict(n=99,  hold_days=41, ann_ret=0.0790, ex=0.0490, ir=0.74, mdd=0.1545, win=0.66,   payoff=1.16),
    's9': dict(n=17,  hold_days=236, ann_ret=0.0769, ex=0.0469, ir=0.71, mdd=0.1273, win=0.6111, payoff=9.06),
}

def count_signals(score, isfull=True):
    """按交易规则统计信号次数/次均持仓(周->天x5)"""
    s = score.dropna()
    s = s[s != 0]
    if s.empty:
        return 0, 0
    # 连续相同方向算一次信号
    pos = np.sign(s)
    change = (pos != pos.shift(1)).sum()
    run_len = []
    cur = 1
    for i in range(1, len(pos)):
        if pos.iloc[i] == pos.iloc[i-1]:
            cur += 1
        else:
            run_len.append(cur); cur = 1
    run_len.append(cur)
    hold_weeks = np.mean(run_len)
    return int(change), hold_weeks * 5

def scan_s1(idx, krange=range(2, 30)):
    wk = be.weekly_base(idx, '20100101', '20260822')
    wk['xs_cum'] = wk['xs_ret'].rolling(1).sum()  # 每周超额
    res = []
    for k in krange:
        s = np.sign(wk['xs_cum'].rolling(k, min_periods=2).mean().diff())
        s = pd.Series(s, index=wk.index)
        n, hold = count_signals(s)
        res.append((k, n, hold))
    return res

def scan_s2(idx, krange=range(1, 26)):
    wk = be.weekly_base(idx, '20100101', '20260822')
    amt_share = wk['amt_g'] / (wk['amt_g'] + wk['amt_v'])
    res = []
    for k in krange:
        s = np.sign(amt_share - amt_share.shift(k))
        s = pd.Series(s, index=wk.index)
        n, hold = count_signals(s)
        res.append((k, n, hold))
    return res

def scan_s7_s9(krange=None):
    """PPI-CPI分位差与M1-M2分位差 (月度->周)"""
    mac, m, f = be.load_macro()
    cpi, ppi = mac['cpi'], mac['ppi']
    pc = (ppi - cpi).dropna()
    pc = pc[pc.index >= '2009-01-01']
    m1m2 = (m.set_index('月份')['货币(M1)-同比增长'] - m.set_index('月份')['货币和准货币(M2)-同比增长']).dropna()
    m1m2 = m1m2[m1m2.index >= '2009-01-01']
    res7, res9 = [], []
    for w in [24, 36, 60, 90, 120, 240]:
        pr = pc.rolling(w, min_periods=24).rank(pct=True)
        for th in [0.1, 0.2, 0.3, 0.4]:
            s = np.where((pc < 0) & (pr < th), 1, np.where((pc > 0) & (pr > 1 - th), -1, 0))
            s = pd.Series(s, index=pc.index)
            n, hold = count_signals(s)
            res7.append((w, th, n, hold))
    for w in [24, 36, 60, 90, 120, 240]:
        mr = m1m2.rolling(w, min_periods=24).rank(pct=True)
        for th in [0.3, 0.5, 0.7]:
            s = np.where(mr >= th, 1, -1)
            s = pd.Series(s, index=m1m2.index)
            n, hold = count_signals(s)
            res9.append((w, th, n, hold))
    return res7, res9

if __name__ == '__main__':
    idx = be.load_index()
    print('S1 价格动量 (期望 n=186, 持仓22天): k(周动量均值) -> n/hold')
    for r in scan_s1(idx)[::2]:
        print('  ', r)
    print('S2 成交额动量 (期望 n=158, 持仓25天): k(周差分) -> n/h')
    for r in scan_s2(idx):
        print('  ', r)
    print('S7 PPI-CPI分位 (期望 n=6, 持仓670天): (窗口,阈值) -> n/h')
    r7, r9 = scan_s7_s9()
    for r in sorted(r7, key=lambda x: abs(x[2]-6))[:10]:
        print('  ', r)
    print('S9 M1-M2分位 (期望 n=17, 持仓236天): (窗口,阈值) -> n/h')
    for r in sorted(r9, key=lambda x: abs(x[2]-17))[:10]:
        print('  ', r)