#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V4周报多空信号历史回放: 7月大跌 / 9月反弹 / 10月大跌
对比: 聚合方式(等权/成交额加权) x ANB窗口(W) x 趋势窗口(MA/动量) x 综合规则(原/趋势优先)
输出: 全网格方向命中率 + 关键方案逐日观点序列 + 转折时点
"""
import os, sys, json
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.join(os.getcwd(), '.pydeps'))
sys.path.insert(0, os.getcwd())

from weekly_report import INDUSTRY_ETF_MAP, INDEX_CONFIG, fetch_index_prices

OUT = os.path.join(os.getcwd(), 'backtest_view_grid.json')

# ---------------- 1. 数据准备 ----------------
df = pd.read_csv('industry_moneyflow_real.csv', dtype={'trade_date': str})
# 合并 10-22 数据(若存在)
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

# 每日 ANB 面板与每日成交额
anb_panel = df.set_index(['date', 'industry'])['anb'].unstack('industry')
amt_panel = df.set_index(['date', 'industry'])['amount'].unstack('industry')
dates = anb_panel.index

# 指数行情
print('拉取指数行情...')
prices = fetch_index_prices(dates[-1].strftime('%Y%m%d'))
# 补充: 用全区间(180根)行情, 直接重新拉一遍完整版
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

# ---------------- 2. 回访参数网格 ----------------
AGGS = ['eq', 'wt']          # 聚合方式
WS = [1, 3, 5, 7, 10]        # ANB窗口
MAS = [10, 20, 30]           # 均线窗口
MOMS = [3, 5, 10]            # 动量窗口
RULES = ['orig', 'trend1']   # 综合规则

# 三段行情区间(交易日列表)
SEGS = {
    '7月大跌': ('2026-06-25', '2026-08-03'),   # 顶部附近开始, 到8月初反弹起点
    '9月反弹': ('2026-09-07', '2026-09-30'),   # 反弹前后
    '10月大跌': ('2026-10-08', '2026-10-22'),  # 10月至今
}

def agg_anb_series(anb_ma, inds):
    """等权聚合"""
    sub = anb_ma[inds].dropna(how='all')
    return sub.mean(axis=1)

def agg_anb_series_wt(anb_ma, amt, inds):
    """成交额加权聚合"""
    idx = anb_ma.index
    vals = []
    for d in idx:
        row_anb = anb_ma.loc[d]
        row_amt = amt.loc[d] if d in amt.index else pd.Series(dtype=float)
        seen = [(i, row_anb.get(i)) for i in inds if pd.notna(row_anb.get(i))]
        w = [row_amt.get(i, np.nan) for (i, _) in seen]
        v = [x for (_, x) in seen]
        ok = [(a, b) for a, b in zip(v, w) if pd.notna(a) and pd.notna(b) and b > 0]
        if ok:
            vals.append(np.average([x for x, _ in ok], weights=[y for _, y in ok]))
        else:
            vals.append(np.nan)
    return pd.Series(vals, index=idx)

def trend_signal_from_px(px_close, date, ma_win, mom_win):
    """在 date(含) 处计算趋势信号"""
    sub = px_close[px_close.index <= date]
    if len(sub) < max(ma_win, mom_win + 1):
        return 0, None
    close = sub.iloc[-1]
    ma = sub.rolling(ma_win).mean().iloc[-1]
    if pd.isna(ma):
        return 0, None
    mom = (close / sub.iloc[-1 - mom_win] - 1) * 100
    above = close > ma
    mup = mom > 0
    if above and mup:
        s = 1
    elif (not above) and (not mup):
        s = -1
    else:
        s = 0
    return s, (close, ma, mom)

def final_view(anb_val, trend, rule):
    """综合观点: 1=多, 0=中性, -1=空"""
    if trend == 0:
        # 无明确趋势: 只看ANB(原规则也如此: trend=0恒中性, 这里给"ANB方向弱信号"对比用)
        if rule == 'trend1':
            return 1 if anb_val > 0 else (-1 if anb_val < 0 else 0)
        return 0
    # 有明确趋势
    if rule == 'trend1':
        return trend  # 趋势优先
    # 原规则: 同向才出, 否则中性
    if anb_val > 0 and trend > 0:
        return 1
    if anb_val < 0 and trend < 0:
        return -1
    return 0

def hit_rate(sig_series, ret5):
    """命中率: 信号非0日, 方向与未来5日收益一致比例"""
    s = sig_series.dropna()
    r = ret5.reindex(s.index)
    mask = s != 0
    s, r = s[mask], r[mask]
    if len(s) == 0:
        return np.nan, 0
    return float((np.sign(s) == np.sign(r)).mean()), int(len(s))

# 统一指数行情 -> 以 "创业板" 为主评, 附 科创50/沪深300
FOCUS = ['创业板', '科创50', '沪深300', '全A']

results = []
daily_records = {}

for agg in AGGS:
    for W in WS:
        anb_ma = anb_panel.rolling(W, min_periods=min(3, W)).mean()
        for ma_win in MAS:
            for mom_win in MOMS:
                for rule in RULES:
                    rec = {'agg': agg, 'W': W, 'ma': ma_win, 'mom': mom_win, 'rule': rule}
                    seg_scores = {}
                    for name in FOCUS:
                        inds = INDEX_CONFIG[name]['industries']
                        if agg == 'eq':
                            agg_s = agg_anb_series(anb_ma, inds)
                        else:
                            agg_s = agg_anb_series_wt(anb_ma, amt_panel, inds)
                        px = price_full.get(name)
                        if px is None:
                            continue
                        px_close = px.set_index(pd.to_datetime(px['trade_date'], format='%Y%m%d'))['close']
                        px_close = px_close[~px_close.index.duplicated(keep='last')].sort_index()
                        # 对齐: 只用有 ANB 的日期
                        sig = {}
                        for d in agg_s.index:
                            ts = trend_signal_from_px(px_close, d, ma_win, mom_win)[0]
                            anbv = agg_s.loc[d]
                            if pd.isna(anbv):
                                continue
                            sig[d] = final_view(anbv, ts, rule)
                        sig = pd.Series(sig)
                        # 未来5日收益
                        merged = pd.concat([sig, px_close], axis=1, join='inner')
                        merged.columns = ['sig', 'close']
                        merged['ret5'] = merged['close'].shift(-5) / merged['close'] - 1
                        # 划段统计
                        seg_hits = {}
                        for seg_name, (s0, e0) in SEGS.items():
                            s0 = pd.Timestamp(s0); e0 = pd.Timestamp(e0)
                            sub = merged[(merged.index >= s0) & (merged.index <= e0)]
                            hr, n = hit_rate(sub['sig'], sub['ret5'])
                            seg_hits[seg_name] = (hr if not np.isnan(hr) else None, n)
                        rec[f'{name}_seg'] = seg_hits
                        # 整体(6月以来)
                        full_sub = merged[merged.index >= '2026-06-01']
                        hr, n = hit_rate(full_sub['sig'], full_sub['ret5'])
                        rec[f'{name}_all'] = (hr, n)
                        rec[f'{name}_sig'] = sig
                    results.append(rec)

# ---------------- 3. 汇总: 按主指数评分 ----------------
def arr_score(rec):
    """评分: 创业板+科创50在3段里的非空命中率均值"""
    vals = []
    for name in ['创业板', '科创50']:
        seg = rec.get(f'{name}_seg', {})
        for k, (hr, n) in seg.items():
            if hr is not None and n >= 3:
                vals.append(hr)
    return np.mean(vals) if vals else 0

for rec in results:
    rec['score'] = arr_score(rec)

results.sort(key=lambda r: -r['score'])
print('=== 全网格 Top15 (按创业板+科创50三段命中率均值) ===')
print(f"{'聚合':<4}{'W':<3}{'MA':<4}{'动量':<4}{'规则':<7}{'评分':<8}{'创业_三seg':<36}{'科创_三seg':<36}")
for r in results[:15]:
    def fmt(name):
        seg = r.get(f'{name}_seg', {})
        return '|'.join(f"{k}:{v[0]:.2f}({v[1]})" if v[0] is not None else f"{k}:-" for k, v in seg.items())
    print(f"{r['agg']:<4}{r['W']:<3}{r['ma']:<4}{r['mom']:<4}{r['rule']:<7}{r['score']:.4f}  {fmt('创业板'):<36}{fmt('科创50'):<36}")

# ---------------- 4. 关键方案对比: 现方案 vs 最优候选 ----------------
print()
print('=== 关键方案逐日观点(创业板, 三段区间) ===')
candidates = [
    {'agg': 'eq', 'W': 7, 'ma': 20, 'mom': 5, 'rule': 'orig', 'label': '现状 等权W7/MA20/动量5/原规则'},
    {'agg': 'wt', 'W': 7, 'ma': 20, 'mom': 5, 'rule': 'orig', 'label': '加权W7/MA20/动量5/原规则'},
    {'agg': 'eq', 'W': 5, 'ma': 20, 'mom': 5, 'rule': 'trend1', 'label': '等权W5/MA20/动量5/趋势优先'},
    {'agg': 'wt', 'W': 5, 'ma': 20, 'mom': 5, 'rule': 'trend1', 'label': '加权W5/MA20/动量5/趋势优先'},
    {'agg': 'eq', 'W': 3, 'ma': 10, 'mom': 3, 'rule': 'trend1', 'label': '等权W3/MA10/动量3/趋势优先'},
    {'agg': 'wt', 'W': 3, 'ma': 10, 'mom': 3, 'rule': 'trend1', 'label': '加权W3/MA10/动量3/趋势优先'},
]

def find_rec(agg, W, ma_w, mom_w, rule):
    for r in results:
        if r['agg'] == agg and r['W'] == W and r['ma'] == ma_w and r['mom'] == mom_w and r['rule'] == rule:
            return r
    return None

# 段内观点序列(创业板): 输出 每3个交易日取一个代表
for c in candidates:
    r = find_rec(c['agg'], c['W'], c['ma'], c['mom'], c['rule'])
    if r is None:
        continue
    print()
    print(f"-- {c['label']} (评分={r['score']:.4f})")
    for name in ['创业板', '科创50']:
        sig = r.get(f'{name}_sig')
        if sig is None:
            continue
        seg_txt = []
        for seg_name, (s0, e0) in SEGS.items():
            s0 = pd.Timestamp(s0); e0 = pd.Timestamp(e0)
            sub = sig[(sig.index >= s0) & (sig.index <= e0)]
            txt = ''.join('多' if v == 1 else ('空' if v == -1 else '·') for v in sub.values)
            seg_txt.append(f"{seg_name}:{txt}")
        print(f"  {name}: " + ' | '.join(seg_txt))

# ---------------- 5. 保存 ----------------
summary = []
for r in results:
    summary.append({k: v for k, v in r.items() if not k.endswith('_sig')})
with open(OUT, 'w', encoding='utf-8') as f:
    json.dump(summary, f, ensure_ascii=False, default=str, indent=1)
print()
print(f'结果已保存: {OUT}')