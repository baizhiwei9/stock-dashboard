#!/usr/bin/env python3
"""
扩散+RRG x 总仓位风控 v2 —— TDA拓扑控仓(重标定版) vs 趋势过滤 vs 满仓
====================================================================
修复点: TDA原始脆弱性映射仓位恒压半仓以下 -> 改用滚动历史分位重标定
  1. 缓存月度 TDA vulnerability (60日窗口 x 30行业)
  2. 滚动分位映射: q = vuln在近24个月中的分位
     pos = 0.9 - 0.6*q  (q=0 -> 0.9仓, q=0.5 -> 0.6仓, q=1 -> 0.3仓)
防前视: 只用 t 时刻之前的历史分位
"""
import sys, os, warnings, json
sys.path.insert(0, '.')
sys.path.insert(0, 'pylib')
for p in ['/tmp/.pip-global/lib/python3.12/site-packages']:
    if p not in sys.path: sys.path.insert(0, p)
warnings.filterwarnings('ignore')
import pandas as pd, numpy as np
import rrg_data, rrg_engine

REPO_TDA = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5febb5eaeeffe0mTIpBeu5w0Nq0'
sys.path.insert(0, REPO_TDA)
from tda_core import run_tda_analysis_safe

START, END = '20220101', '20260822'
WINDOW = 60

def load():
    diffusion = pd.read_csv('data_rrg/diffusion.csv', index_col=0, parse_dates=True)
    prices = rrg_data.load_industry_prices()
    rrg = rrg_engine.compute_rrg(prices)
    return prices, diffusion, rrg

def base_signals(prices, diffusion, rrg):
    p = prices[prices.index >= pd.Timestamp(START)]
    me_idx = rrg_engine.month_end_indices(p)
    signals = {}
    for i in me_idx:
        t = p.index[i]
        q_ = {c: rrg[c].loc[t] for c in p.columns}
        diff_t = diffusion.loc[t] if t in diffusion.index else pd.Series(dtype=float)
        topd = diff_t.dropna().sort_values(ascending=False)
        top6d = topd.index[:6].tolist()
        keep = [c for c in top6d if q_.get(c, {}).get('quadrant', 3) in (1, 2)]
        signals[t] = {c: 1.0 / len(keep) for c in keep} if keep else {}
    return signals, p

def cached_vuln(prices):
    """读取或计算月度脆弱性缓存"""
    cache = 'tda_vuln_series.csv'
    if os.path.exists(cache):
        df = pd.read_csv(cache, index_col=0, parse_dates=True)
        df['vuln'] = pd.to_numeric(df['vuln'], errors='coerce')
        df['pos_raw'] = pd.to_numeric(df['pos_raw'], errors='coerce')
        if not df['vuln'].isna().any():
            return df
    p = prices[prices.index >= pd.Timestamp(START)]
    me_idx = rrg_engine.month_end_indices(p)
    all_idx = {d: i for i, d in enumerate(prices.index)}
    rows = []
    print('  计算TDA脆弱性(60日窗口, 逐月度)...', flush=True)
    for i in me_idx:
        t = p.index[i]
        pos_in_all = all_idx[t]
        if pos_in_all < WINDOW:
            rows.append((t, np.nan, np.nan)); continue
        pw = prices.iloc[pos_in_all - WINDOW + 1:pos_in_all + 1]
        try:
            res = run_tda_analysis_safe(pw)
            m = res['market']
            rows.append((t, m.get('vulnerability', np.nan), m.get('position_ratio', np.nan)))
        except Exception:
            rows.append((t, np.nan, np.nan))
        print(f'    {t.date()} vuln={rows[-1][1]:.1f}' if pd.notna(rows[-1][1]) else f'    {t.date()} ERR', flush=True)
    df = pd.DataFrame(rows, columns=['date', 'vuln', 'pos_raw']).set_index('date')
    df.to_csv(cache)
    return df

def rolling_quantile_map(vuln_series, lookback=24):
    """滚动分位 -> 仓位. 对每个月份t, 用其之前lookback个月的vuln算分位"""
    pos = {}
    vals = vuln_series.dropna()
    dates = vals.index.tolist()
    for i, t in enumerate(dates):
        hist = vals.iloc[max(0, i - lookback):i]
        v = vals.loc[t]
        if len(hist) >= 6:
            q = (hist < v).mean()  # 脆弱性高于历史上多少比例
        else:
            q = 0.5
        pos[t] = max(0.3, min(0.9, 0.9 - 0.6 * q))
    return pos

def run(prices, signals, pos_map):
    ws = {t: {c: v * pos_map.get(t, 1.0) for c, v in w.items()} for t, w in signals.items()}
    r, nav = rrg_engine.portfolio_returns(prices, ws)
    return r, nav

if __name__ == '__main__':
    prices, diffusion, rrg = load()
    signals, p = base_signals(prices, diffusion, rrg)
    vuln_df = cached_vuln(prices)
    print(f'\n=== 扩散+RRG x 总仓位风控 v2 | {START} ~ {END} ===')
    print(f'{"方案":<24}{"累计":>10}{"年化":>10}{"最大回撤":>11}{"夏普":>8}')

    posA = {t: 1.0 for t in signals}
    rA, nA = run(prices, signals, posA)

    eq = prices.mean(axis=1)
    ma40 = eq.rolling(40).mean(); ma80 = eq.rolling(80).mean()
    posB = {}
    for t in signals:
        e, m4, m8 = eq.loc[t], ma40.loc[t], ma80.loc[t]
        if pd.notna(m8) and e < m8: posB[t] = 0.0
        elif pd.notna(m4) and e < m4: posB[t] = 0.5
        else: posB[t] = 1.0
    rB, nB = run(prices, signals, posB)

    posC_raw = vuln_df['pos_raw'].to_dict()  # 原始TDA映射
    rC, nC = run(prices, signals, posC_raw)

    posD = rolling_quantile_map(vuln_df['vuln'])
    rD, nD = run(prices, signals, posD)

    for name, (r, nv) in [('A 满仓(现状)', (rA, nA)), ('B 趋势过滤MA', (rB, nB)),
                           ('C TDA原始映射', (rC, nC)), ('D TDA分位重标定', (rD, nD))]:
        s = rrg_engine.perf_stats(r, nv)
        print(f'{name:<24}{s["total_ret"]:>10.2%}{s["ann_ret"]:>10.2%}{s["maxdd"]:>11.2%}{s["sharpe"]:>8.3f}')

    print('\n--- 2026年至今分段 ---')
    for name, (r, nv) in [('A 满仓', (rA, nA)), ('B 趋势过滤', (rB, nB)),
                          ('C TDA原始', (rC, nC)), ('D TDA分位重标定', (rD, nD))]:
        seg = nv[(nv.index >= pd.Timestamp('20260101')) & (nv.index <= pd.Timestamp('20260822'))]
        if len(seg) > 10:
            tot = float(seg.iloc[-1] / seg.iloc[0] - 1)
            mdd = float((seg / seg.cummax() - 1).min())
            print(f'{name:<16}{tot:>9.2%}  回撤 {mdd:>8.2%}')

    print('\nD仓位月度分布: mean={:.3f} min={:.3f} max={:.3f}'.format(
        np.mean(list(posD.values())), min(posD.values()), max(posD.values())))
    pd.Series(posD).to_csv('tda_quantile_position.csv')