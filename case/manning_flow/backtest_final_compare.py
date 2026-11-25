#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
最终对比: 等权vs加权 + 现状vs趋势锚定 + 窗口敏感性
覆盖: 7月大跌 / 8月震荡 / 9月反弹 / 10月大跌 全期
指标: 段内命中率(未来5日), 转折时点, 信号翻转次数, 溢出日占比
"""
import os, sys
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.join(os.getcwd(), '.pydeps'))
sys.path.insert(0, os.getcwd())
from weekly_report import INDUSTRY_ETF_MAP, INDEX_CONFIG

# ---------------- 数据 ----------------
df = pd.read_csv('industry_moneyflow_real.csv', dtype={'trade_date': str})
if os.path.exists('/tmp/mf_20261022.csv'):
    new = pd.read_csv('/tmp/mf_20261022.csv', dtype={'trade_date': str})
    new['trade_date'] = '20261022'
    df = pd.concat([df, new], ignore_index=True)
    df = df.drop_duplicates(subset=['trade_date', 'industry'], keep='last')
df = df[df['industry'].isin(INDUSTRY_ETF_MAP.keys())].copy()
df['date'] = pd.to_datetime(df['trade_date'], format='%Y%m%d')
df = df.sort_values(['date', 'industry']).reset_index(drop=True)
for col in ['net_buy_amount', 'net_sell_amount', 'net_amount', 'pct_change']:
    df[col] = pd.to_numeric(df[col], errors='coerce')
daily = df.groupby('date').agg(total_buy=('net_buy_amount', 'sum'), total_sell=('net_sell_amount', 'sum'))
df = df.merge(daily, on='date')
df['buy_ratio'] = df['net_buy_amount'] / df['total_buy']
df['sell_ratio'] = df['net_sell_amount'] / df['total_sell']
df['amount'] = df['net_buy_amount'] + df['net_sell_amount']
daily_amt = df.groupby('date')['amount'].sum().rename('total_amount')
df = df.merge(daily_amt, on='date')
df['amount_ratio'] = df['amount'] / df['total_amount']
df['anb'] = (df['buy_ratio'] - df['sell_ratio']) / df['amount_ratio'].replace(0, np.nan)
anb_panel = df.set_index(['date', 'industry'])['anb'].unstack('industry')
amt_panel = df.set_index(['date', 'industry'])['amount'].unstack('industry')

import requests
def fetch_full(code, n=220):
    url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,{n},qfq"
    r = requests.get(url, timeout=15); data = r.json()
    kl = data.get('data', {}).get(code, {})
    klist = kl.get('qfqday') or kl.get('day') or []
    d = pd.DataFrame(klist, columns=['trade_date', 'open', 'close', 'high', 'low'] + [f'e{i}' for i in range(len(klist[0]) - 5)])
    d['close'] = pd.to_numeric(d['close'], errors='coerce')
    d['trade_date'] = d['trade_date'].str.replace('-', '')
    return d.sort_values('trade_date').reset_index(drop=True)

# 从本地缓存读行情(避免腾讯限流)
PRICE_CACHE = os.path.join(os.getcwd(), 'price_cache')
tx_map = {"创业板": "sz399006", "科创50": "sh000688", "沪深300": "sh000300", "全A": "sh000985"}
price_full = {}
for name in tx_map:
    fp = os.path.join(PRICE_CACHE, f'{name}.csv')
    if os.path.exists(fp):
        price_full[name] = pd.read_csv(fp, dtype={'trade_date': str})
    else:
        price_full[name] = fetch_full(tx_map[name])

# 各段区间
SEGS = [
    ('7月大跌', '2026-06-25', '2026-08-03'),
    ('8月震荡', '2026-08-03', '2026-09-04'),
    ('9月反弹', '2026-09-07', '2026-09-30'),
    ('10月大跌', '2026-10-08', '2026-10-22'),
    ('全期', '2026-06-25', '2026-10-22'),
]

def agg_series(anb_ma, amt, inds, agg):
    if agg == 'eq':
        return anb_ma[inds].dropna(how='all').mean(axis=1)
    idx = anb_ma.index; vals = []
    for d in idx:
        row_anb = anb_ma.loc[d]; row_amt = amt.loc[d] if d in amt.index else pd.Series(dtype=float)
        ok = [(row_anb.get(i), row_amt.get(i, np.nan)) for i in inds
              if pd.notna(row_anb.get(i)) and pd.notna(row_amt.get(i, np.nan)) and row_amt.get(i, np.nan) > 0]
        vals.append(np.average([x for x, _ in ok], weights=[y for _, y in ok]) if ok else np.nan)
    return pd.Series(vals, index=idx)

def trend_signal(px_close, date, ma_win, mom_win):
    sub = px_close[px_close.index <= date]
    if len(sub) < max(ma_win, mom_win + 1):
        return 0
    close = sub.iloc[-1]; ma = sub.rolling(ma_win).mean().iloc[-1]
    if pd.isna(ma):
        return 0
    mom = (close / sub.iloc[-1 - mom_win] - 1) * 100
    above, mup = close > ma, mom > 0
    if above and mup:
        return 1
    if (not above) and (not mup):
        return -1
    return 0

def final_view(anb_val, trend, rule, eps):
    if rule == 'orig':
        if anb_val > 0 and trend > 0:
            return 1
        if anb_val < 0 and trend < 0:
            return -1
        return 0
    if rule == 'trend_anchor':
        if trend != 0:
            if abs(anb_val) < eps:
                return trend
            if (anb_val > 0 and trend > 0) or (anb_val < 0 and trend < 0):
                return trend
            return 0
        return 1 if anb_val > 0 else (-1 if anb_val < 0 else 0)
    return 0

def build_sig(agg, W, ma_w, mom_w, rule, eps):
    anb_ma = anb_panel.rolling(W, min_periods=min(3, W)).mean()
    out = {}
    for name in price_full:
        inds = INDEX_CONFIG[name]['industries']
        agg_s = agg_series(anb_ma, amt_panel, inds, agg)
        px = price_full[name]
        px_close = px.set_index(pd.to_datetime(px['trade_date'], format='%Y%m%d'))['close']
        px_close = px_close[~px_close.index.duplicated(keep='last')].sort_index()
        sig = {}
        for d in agg_s.index:
            ts = trend_signal(px_close, d, ma_w, mom_w)
            anbv = agg_s.loc[d]
            if pd.isna(anbv):
                continue
            sig[d] = final_view(anbv, ts, rule, eps)
        out[name] = pd.Series(sig)
    return out

CONFIGS = [
    ('现状: 等权W7/MA20/动量5/原规则', dict(agg='eq', W=7, ma_w=20, mom_w=5, rule='orig', eps=0.0)),
    ('加权W7/MA20/动量5/原规则', dict(agg='wt', W=7, ma_w=20, mom_w=5, rule='orig', eps=0.0)),
    ('等权W5/MA20/动量5/趋势锚定0.02', dict(agg='eq', W=5, ma_w=20, mom_w=5, rule='trend_anchor', eps=0.02)),
    ('加权W5/MA20/动量5/趋势锚定0.02', dict(agg='wt', W=5, ma_w=20, mom_w=5, rule='trend_anchor', eps=0.02)),
    ('等权W5/MA10/动量3/趋势锚定0.02', dict(agg='eq', W=5, ma_w=10, mom_w=3, rule='trend_anchor', eps=0.02)),
    ('加权W5/MA10/动量3/趋势锚定0.02', dict(agg='wt', W=5, ma_w=10, mom_w=3, rule='trend_anchor', eps=0.02)),
    ('等权W3/MA10/动量3/趋势锚定0.02', dict(agg='eq', W=3, ma_w=10, mom_w=3, rule='trend_anchor', eps=0.02)),
    ('加权W3/MA10/动量3/趋势锚定0.02', dict(agg='wt', W=3, ma_w=10, mom_w=3, rule='trend_anchor', eps=0.02)),
]

def first_run(sig, start, direction):
    sub = sig[sig.index >= pd.Timestamp(start)]
    for i in range(len(sub) - 1):
        if (sub.iloc[i:i + 2] == direction).all():
            return sub.index[i]
    return None

def seg_stats(sig, px_close):
    merged = pd.concat([sig, px_close], axis=1, join='inner')
    merged.columns = ['sig', 'close']
    merged['ret5'] = merged['close'].shift(-5) / merged['close'] - 1
    return merged

print('=' * 100)
for label, cfg in CONFIGS:
    sigs = build_sig(**cfg)
    print(f'\n### {label}')
    for name in ['创业板', '科创50', '沪深300']:
        sig = sigs[name]
        px_close = price_full[name].set_index(pd.to_datetime(price_full[name]['trade_date'], format='%Y%m%d'))['close']
        px_close = px_close[~px_close.index.duplicated(keep='last')].sort_index()
        merged = seg_stats(sig, px_close)
        # 转折时点
        jl = first_run(sig, '2026-07-01', -1)
        sl = first_run(sig, '2026-09-15', 1)
        ol = first_run(sig, '2026-10-08', -1)
        flips = int((sig.diff() != 0).sum())
        act = sig[sig != 0]
        print(f'  {name}: 7月转空={str(jl)[:10]} 9月转多={str(sl)[:10]} 10月转空={str(ol)[:10]} | 翻转次数={flips} | 非中性日={len(act)}')
        for seg_name, s0, e0 in SEGS:
            sub = merged[(merged.index >= pd.Timestamp(s0)) & (merged.index <= pd.Timestamp(e0))]
            m = sub['sig'] != 0
            if m.sum() >= 3:
                hr = float((np.sign(sub['sig'][m]) == np.sign(sub['ret5'][m])).mean())
                print(f'      {seg_name}: 命中率={hr:.3f} (信号日{m.sum()}/{len(sub)})')
            else:
                print(f'      {seg_name}: 信号不足({m.sum()}/{len(sub)})')