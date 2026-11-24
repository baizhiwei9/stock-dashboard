# -*- coding: utf-8 -*-
"""单信号全区间绩效核对: 与文章披露数字对照 (用于校准实现正确性)
   全区间: 20100108-20260731  (文章口径)
   信号每日生成, 周五采样; 周度调仓, 无信号延续前信号
"""
import numpy as np
import pandas as pd
import backtest_engine as be

DISCLOSED = {
    's1': dict(n=186, hold_days=22, ann_ret=0.0913, ex=0.0609, ir=0.93, mdd=0.1325, win=0.4866, payoff=2.25),
    's2': dict(n=158, hold_days=25, ann_ret=0.0733, ex=0.0434, ir=0.66, mdd=0.1711, win=0.5283, payoff=1.64),
    's7': dict(n=6,   hold_days=670, ann_ret=0.0696, ex=0.0399, ir=0.61, mdd=0.1186, win=0.8571, payoff=7.5),
    's8': dict(n=99,  hold_days=41, ann_ret=0.0790, ex=0.0490, ir=0.74, mdd=0.1545, win=0.66,   payoff=1.16),
    's9': dict(n=17,  hold_days=236, ann_ret=0.0769, ex=0.0469, ir=0.71, mdd=0.1273, win=0.6111, payoff=9.06),
}

def daily_signals(start='20100108', end='20260731'):
    """生成日度9信号 (s3-s6 为NaN占位)"""
    idx = be.load_index()
    d = idx[(idx['trade_date'] >= pd.Timestamp(start)) & (idx['trade_date'] <= pd.Timestamp(end))].copy()
    d['g_r'] = d['close_g'] / d['close_g'].shift(1) - 1
    d['v_r'] = d['close_v'] / d['close_v'].shift(1) - 1
    d['xs'] = d['g_r'] - d['v_r']
    d['ash'] = d['amount_g'] / (d['amount_g'] + d['amount_v'])
    d['week'] = d['trade_date'].dt.to_period('W-FRI')
    out = d[['trade_date', 'week']].copy()
    # S1: 日超额15日累计符号
    out['s1'] = np.sign(d['xs'].rolling(15, min_periods=10).sum())
    return out

def weekly_sig_s2(start='20100108', end='20260731'):
    idx = be.load_index()
    wk = be.weekly_base(idx, start, end)
    ash = wk['amt_g'] / (wk['amt_g'] + wk['amt_v'])
    sig = np.sign(ash.diff(8))
    return wk, pd.Series(sig.values, index=wk['last_date'])

def macro_sig(sig, start='20100108', end='20260731'):
    mac, m, f = be.load_macro()
    cpi, ppi = mac['cpi'], mac['ppi']
    pc = (ppi - cpi).dropna()
    pc = pc[(pc.index >= '2008-01-01') & (pc.index <= end)]
    pr = pc.rolling(36, min_periods=24).rank(pct=True)
    s = np.where((pc < 0) & (pr < 0.3), 1, np.where((pc > 0) & (pr > 0.7), -1, 0))
    return pd.Series(s, index=pc.index)

def macro_sig9(start='20100108', end='20260731'):
    mac, m, f = be.load_macro()
    cpi, ppi = mac['cpi'], mac['ppi']
    pc = (ppi - cpi).dropna(); pc = pc[(pc.index >= '2008-01-01') & (pc.index <= end)]
    m1m2 = (m.set_index('月份')['货币(M1)-同比增长'] - m.set_index('月份')['货币和准货币(M2)-同比增长']).dropna()
    m1m2 = m1m2[(m1m2.index >= '2008-01-01') & (m1m2.index <= end)]
    fi = f.set_index('月份')['同比增长'].astype(float).dropna()
    pr = pc.rolling(36, min_periods=24).rank(pct=True)
    s7 = np.where((pc < 0) & (pr < 0.3), 1, np.where((pc > 0) & (pr > 0.7), -1, 0))
    mr = m1m2.rolling(36, min_periods=24).rank(pct=True)
    s9 = np.where(mr >= 0.7, 1, -1)
    fch = fi.diff()
    s8 = np.where(fch < 0, 1, -1)
    return pd.Series(s7, index=pc.index), pd.Series(s8, index=fch.index), pd.Series(s9, index=m1m2.index)

def map_to_weekly(mseries, last_dates):
    """月度信号 -> 周最后交易日(用<=的最近月观测)"""
    ts = mseries.sort_index()
    ld = np.array(pd.to_datetime(last_dates))
    idx_pos = ts.index.values.searchsorted(ld, side='right') - 1
    vals = ts.values
    return np.where(idx_pos >= 0, vals[np.clip(idx_pos, 0, len(vals) - 1)], np.nan)

def run_single(sig_df, wk, name):
    """sig_df: df[trade_date, sig] 周采样信号; wk: 周度
    信号在t周最后交易日收盘确定并以收盘价交易 -> 持有t到t+1周的收益(shift -1)"""
    df = sig_df.merge(wk[['last_date', 'g_ret', 'v_ret']], left_on='trade_date', right_on='last_date', how='inner')
    df = df.sort_values('trade_date').reset_index(drop=True)
    df['g_ret'] = df['g_ret'].shift(-1)
    df['v_ret'] = df['v_ret'].shift(-1)
    pos = np.zeros(len(df)); cur = 0.0
    for i in range(len(df)):
        s = df['sig'].iloc[i]
        if not np.isnan(s) and s != 0:
            cur = 1.0 if s > 0 else -1.0
        pos[i] = cur
    df['pos'] = pos
    df['ret'] = np.where(df['pos'] > 0, df['g_ret'], np.where(df['pos'] < 0, df['v_ret'],
                       0.5 * df['g_ret'] + 0.5 * df['v_ret']))
    df['bench'] = 0.5 * df['g_ret'] + 0.5 * df['v_ret']
    df = df.dropna(subset=['ret']).reset_index(drop=True)
    nav = (1 + df['ret']).cumprod(); bnav = (1 + df['bench']).cumprod()
    n = len(df); yrs = n / 52.0
    ann = nav.iloc[-1] ** (1 / yrs) - 1; bann = bnav.iloc[-1] ** (1 / yrs) - 1
    r, b = df['ret'], df['bench']
    vol = r.std() * np.sqrt(52)
    ir = (r - b).mean() * 52 / ((r - b).std() * np.sqrt(52))
    mdd = (nav / nav.cummax() - 1).min()
    # 信号次数: 持仓方向切换(去除0后)
    p = df['pos'].replace(0, np.nan).dropna()
    n_sig = int(np.sum(np.sign(p).diff().fillna(0) != 0))
    # 时间跨度
    days = (df['trade_date'].max() - df['trade_date'].min()).days * 5 / 7
    hold = days / n_sig if n_sig else np.nan
    # 次胜率/赔率
    outs = []; i = 0
    while i < len(df):
        if df['pos'].iloc[i] == 0:
            i += 1; continue
        st = i; dd = df['pos'].iloc[i]
        while i < len(df) and df['pos'].iloc[i] == dd:
            i += 1
        seg = df.iloc[st:i]
        if seg.empty: continue
        outs.append(((1 + seg['ret']).prod() - 1) - ((1 + seg['bench']).prod() - 1))
    wins = np.array(outs)[np.array(outs) > 0]; losses = np.array(outs)[np.array(outs) <= 0]
    win = len(wins) / len(outs) if outs else np.nan
    payoff = wins.mean() / -losses.mean() if len(wins) and len(losses) else np.nan
    return dict(n=n_sig, hold=hold, ann=ann, ex=ann - bann, ir=ir, mdd=mdd, win=win, payoff=payoff)

if __name__ == '__main__':
    idx = be.load_index()
    wk = be.weekly_base(idx, '20100108', '20260731')
    # S1
    dd = daily_signals()
    sig1 = dd.groupby('week')['s1'].last().reset_index().rename(columns={'s1': 'sig'})
    ld_map = dd.groupby('week')['trade_date'].max().reset_index()
    sig1 = ld_map.merge(sig1, on='week')[['trade_date', 'sig']]
    r1 = run_single(sig1, wk, 's1')
    print('S1 实测:', {k: (round(v, 4) if isinstance(v, float) else v) for k, v in r1.items()})
    # S2
    wks2, s2 = weekly_sig_s2()
    r2 = run_single(pd.DataFrame({'trade_date': s2.index, 'sig': s2.values}).reset_index(drop=True), wk, 's2')
    print('S2 实测:', {k: (round(v, 4) if isinstance(v, float) else v) for k, v in r2.items()})
    # S7/S8/S9
    s7, s8, s9 = macro_sig9()
    for nm, s in [('s7', s7), ('s8', s8), ('s9', s9)]:
        v = map_to_weekly(s, wk['last_date'])
        r = run_single(pd.DataFrame({'trade_date': wk['last_date'], 'sig': v}), wk, nm)
        print(f'{nm.upper()} 实测:', {k: (round(vv, 4) if isinstance(vv, float) else vv) for k, vv in r.items()})