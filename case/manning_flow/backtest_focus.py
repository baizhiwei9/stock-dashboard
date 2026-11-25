#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V4周报多空信号聚焦回放:
三段(7月大跌/9月反弹/10月大跌)的转空/转多时点 + 段内命中率
规则: R0现状(同向才出) / R2趋势锚定(ANB微正不抵消趋势, 阈值eps) / R3(ANB阈值主导)
"""
import os, sys, json
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.join(os.getcwd(), '.pydeps'))
sys.path.insert(0, os.getcwd())
from weekly_report import INDUSTRY_ETF_MAP, INDEX_CONFIG

OUT = os.path.join(os.getcwd(), 'backtest_focus.json')

# ---------------- 1. 数据 ----------------
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
    r = requests.get(url, timeout=15)
    data = r.json()
    kl = data.get('data', {}).get(code, {})
    klist = kl.get('qfqday') or kl.get('day') or []
    d = pd.DataFrame(klist, columns=['trade_date', 'open', 'close', 'high', 'low'] + [f'e{i}' for i in range(len(klist[0]) - 5)])
    d['close'] = pd.to_numeric(d['close'], errors='coerce')
    d['trade_date'] = d['trade_date'].str.replace('-', '')
    d = d.sort_values('trade_date').reset_index(drop=True)
    return d

tx_map = {"全A": "sh000985", "沪深300": "sh000300", "中证500": "sh000905",
          "中证1000": "sh000852", "创业板": "sz399006", "科创50": "sh000688"}
price_full = {}
for name, code in tx_map.items():
    try:
        price_full[name] = fetch_full(code)
    except Exception as e:
        print(f'{name} 拉取失败: {e}')

# ---------------- 2. 工具函数 ----------------
def agg_series(anb_ma, amt, inds, agg):
    if agg == 'eq':
        sub = anb_ma[inds].dropna(how='all')
        return sub.mean(axis=1)
    idx = anb_ma.index
    vals = []
    for d in idx:
        row_anb = anb_ma.loc[d]; row_amt = amt.loc[d] if d in amt.index else pd.Series(dtype=float)
        seen = [(i, row_anb.get(i)) for i in inds if pd.notna(row_anb.get(i))]
        ok = [(a, row_amt.get(i, np.nan)) for (i, a) in seen if pd.notna(row_amt.get(i, np.nan)) and row_amt.get(i, np.nan) > 0]
        vals.append(np.average([x for x, _ in ok], weights=[y for _, y in ok]) if ok else np.nan)
    return pd.Series(vals, index=idx)

def trend_signal(px_close, date, ma_win, mom_win):
    sub = px_close[px_close.index <= date]
    if len(sub) < max(ma_win, mom_win + 1):
        return 0
    close = sub.iloc[-1]
    ma = sub.rolling(ma_win).mean().iloc[-1]
    if pd.isna(ma):
        return 0
    mom = (close / sub.iloc[-1 - mom_win] - 1) * 100
    above, mup = close > ma, mom > 0
    if above and mup:
        return 1
    if (not above) and (not mup):
        return -1
    return 0

def final_view(anb_val, trend, rule, eps=0.0):
    if rule == 'orig':  # 现状: 同向才出, 否则中性
        if anb_val > 0 and trend > 0:
            return 1
        if anb_val < 0 and trend < 0:
            return -1
        return 0
    if rule == 'trend_anchor':  # 趋势锚定: 趋势明确时, ANB微正/微负(在eps内)不抵消, 跟趋势; |ANB|>eps反向才中性
        if trend != 0:
            if abs(anb_val) < eps:
                return trend
            if (anb_val > 0 and trend > 0) or (anb_val < 0 and trend < 0):
                return trend
            return 0
        return 1 if anb_val > 0 else (-1 if anb_val < 0 else 0)
    if rule == 'anb_dom':  # ANB主导: |ANB|>eps 时按ANB方向, 否则跟随趋势
        if anb_val > eps:
            return 1
        if anb_val < -eps:
            return -1
        return trend
    return 0

# 关键节点(理想时刻): 7月顶部转空日 / 9月低点转多日 / 10月顶部转空日
NODE = {
    '创业板': {'jul_top': '2026-07-01', 'sep_bottom': '2026-09-15', 'oct_top': '2026-10-08'},
    '科创50': {'jul_top': '2026-07-01', 'sep_bottom': '2026-09-15', 'oct_top': '2026-10-08'},
    '沪深300': {'jul_top': '2026-07-01', 'sep_bottom': '2026-09-15', 'oct_top': '2026-10-08'},
}

def first_run(sig, start, target, direction):
    """从 start 开始, 首次出现方向=direction(连续>=k天)的日期(含start)"""
    sub = sig[sig.index >= pd.Timestamp(start)]
    for i in range(len(sub)):
        win = sub.iloc[i:i + 2]  # 连续2天同向算确认
        if len(win) == 2 and (win == direction).all():
            return sub.index[i]
    return None

# ---------------- 3. 网格 ----------------
AGGS = ['eq', 'wt']
WS = [1, 3, 5, 7]
MAS = [10, 20, 30]
MOMS = [3, 5]
RULES = ['orig', 'trend_anchor']
EPS = [0.01, 0.02]

rows = []
for agg in AGGS:
    for W in WS:
        anb_ma = anb_panel.rolling(W, min_periods=min(3, W)).mean()
        for ma_win in MAS:
            for mom_win in MOMS:
                for rule in RULES:
                    for eps in (EPS if rule == 'trend_anchor' else [0.0]):
                        rec = {'agg': agg, 'W': W, 'ma': ma_win, 'mom': mom_win, 'rule': rule, 'eps': eps}
                        # 先算创业板转空/转多时点
                        for name in ['创业板', '科创50']:
                            inds = INDEX_CONFIG[name]['industries']
                            agg_s = agg_series(anb_ma, amt_panel, inds, agg)
                            px = price_full.get(name)
                            if px is None:
                                continue
                            px_close = px.set_index(pd.to_datetime(px['trade_date'], format='%Y%m%d'))['close']
                            px_close = px_close[~px_close.index.duplicated(keep='last')].sort_index()
                            sig = {}
                            for d in agg_s.index:
                                ts = trend_signal(px_close, d, ma_win, mom_win)
                                anbv = agg_s.loc[d]
                                if pd.isna(anbv):
                                    continue
                                sig[d] = final_view(anbv, ts, rule, eps)
                            sig = pd.Series(sig)
                            rec[f'{name}_jul_short'] = str(first_run(sig, NODE[name]['jul_top'], None, -1))[:10]
                            rec[f'{name}_sep_long'] = str(first_run(sig, NODE[name]['sep_bottom'], None, 1))[:10]
                            rec[f'{name}_oct_short'] = str(first_run(sig, NODE[name]['oct_top'], None, -1))[:10]
                            # 段内命中率(未来5日)
                            merged = pd.concat([sig, px_close], axis=1, join='inner')
                            merged.columns = ['sig', 'close'] if merged.shape[1] == 2 else ['sig', 'close']
                            merged['ret5'] = merged['close'].shift(-5) / merged['close'] - 1
                            seg_hits = {}
                            for seg_name, (s0, e0) in {'7月大跌': ('2026-06-25', '2026-08-03'),
                                                       '9月反弹': ('2026-09-07', '2026-09-30'),
                                                       '10月大跌': ('2026-10-08', '2026-10-22')}.items():
                                sub = merged[(merged.index >= pd.Timestamp(s0)) & (merged.index <= pd.Timestamp(e0))]
                                m = sub['sig'] != 0
                                if m.sum() >= 3:
                                    hr = float((np.sign(sub['sig'][m]) == np.sign(sub['ret5'][m])).mean())
                                    seg_hits[seg_name] = round(hr, 3)
                                else:
                                    seg_hits[seg_name] = None
                            rec[f'{name}_seg'] = seg_hits
                        rows.append(rec)

# 评分: 优先"9月反弹能转多(sep_long 早)" + "10月大跌能转空(oct_short 早)" + 7月转空早
def t2d(s):
    try:
        return pd.Timestamp(s)
    except Exception:
        return None

def score(rec):
    sc = 0.0; cnt = 0
    for name in ['创业板', '科创50']:
        jl = t2d(rec.get(f'{name}_jul_short')); sl = t2d(rec.get(f'{name}_sep_long')); ol = t2d(rec.get(f'{name}_oct_short'))
        # 9月反弹转多: 在9/15~9/29之间越早越好(以9/16为理想: +1.0, 每晚1天-0.1)
        if sl is not None:
            delta = (sl - pd.Timestamp('2026-09-16')).days
            if 0 <= delta <= 10:
                sc += max(0, 1.0 - 0.1 * delta); cnt += 1
        # 10月转空: 10/8起越早越好(理想10/9: +1.0)
        if ol is not None:
            delta = (ol - pd.Timestamp('2026-10-09')).days
            if 0 <= delta <= 7:
                sc += max(0, 1.0 - 0.15 * delta); cnt += 1
        # 7月转空: 7/1起越早越好(理想7/2)
        if jl is not None:
            delta = (jl - pd.Timestamp('2026-07-02')).days
            if 0 <= delta <= 15:
                sc += max(0, 1.0 - 0.08 * delta); cnt += 1
    return sc / max(cnt, 1)

for r in rows:
    r['score'] = score(r)
rows.sort(key=lambda r: -r['score'])

print('=== Top20 (按转折时点评分) ===')
print(f"{'聚合':<4}{'W':<3}{'MA':<4}{'动量':<4}{'规则':<13}{'eps':<5}{'分':<7}{'创业7空':<12}{'创业9多':<12}{'创业10空':<12}{'科创7空':<12}{'科创9多':<12}{'科创10空':<12}")
for r in rows[:20]:
    print(f"{r['agg']:<4}{r['W']:<3}{r['ma']:<4}{r['mom']:<4}{r['rule']:<13}{r['eps']:<5}{r['score']:.3f}  "
          f"{str(r.get('创业板_jul_short')):<12}{str(r.get('创业板_sep_long')):<12}{str(r.get('创业板_oct_short')):<12}"
          f"{str(r.get('科创50_jul_short')):<12}{str(r.get('科创50_sep_long')):<12}{str(r.get('科创50_oct_short')):<12}")

print()
print('=== 现状配置参考(等权W7/MA20/动量5/原规则) ===')
for r in rows:
    if r['agg']=='eq' and r['W']==7 and r['ma']==20 and r['mom']==5 and r['rule']=='orig':
        print(r)

json_data = []
for r in rows:
    json_data.append({k: v for k, v in r.items()})
with open(OUT, 'w', encoding='utf-8') as f:
    json.dump(json_data, f, ensure_ascii=False, default=str, indent=1)
print(f'保存: {OUT}')