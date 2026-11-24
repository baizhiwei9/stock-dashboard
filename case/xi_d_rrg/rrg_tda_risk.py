#!/usr/bin/env python3
"""
扩散+RRG选行业 x 三种风控总权重 —— TDA拓扑控仓对比
================================================
选行业: 扩散最高6 -> 剔除RRG三四象限 (每月末)
总仓位:
  A. 满仓 1.0
  B. 趋势过滤 MA40/MA80 (行业等权)
  C. TDA拓扑控仓: β1结构洞+曲率 -> vulnerability -> position_ratio = max(0.15, 1-vuln/100)
组合收益 = 行业等权权重 x 总仓位
"""
import sys, os, warnings
sys.path.insert(0, '.')
sys.path.insert(0, 'pylib')
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
        q = {c: rrg[c].loc[t] for c in p.columns}
        diff_t = diffusion.loc[t] if t in diffusion.index else pd.Series(dtype=float)
        topd = diff_t.dropna().sort_values(ascending=False)
        top6d = topd.index[:6].tolist()
        keep = [c for c in top6d if q.get(c, {}).get('quadrant', 3) in (1, 2)]
        signals[t] = {c: 1.0 / len(keep) for c in keep} if keep else {}
    return signals, p

def tda_position_series(prices):
    """月度信号日 -> TDA position_ratio (用60日窗口的30行业价格)"""
    p = prices[prices.index >= pd.Timestamp(START)]
    me_idx = rrg_engine.month_end_indices(p)
    # 构建完整价格索引位置
    all_idx = {d: i for i, d in enumerate(prices.index)}
    out = {}
    for i in me_idx:
        t = p.index[i]
        pos_in_all = all_idx[t]
        if pos_in_all < WINDOW:
            out[t] = 0.5; continue
        pw = prices.iloc[pos_in_all - WINDOW + 1:pos_in_all + 1]
        try:
            res = run_tda_analysis_safe(pw)
            m = res['market']
            out[t] = m.get('position_ratio', 0.5)
        except Exception as e:
            out[t] = 0.5
    return out

def run(prices, signals, pos_map):
    p = prices[prices.index >= pd.Timestamp(START)]
    me_idx = rrg_engine.month_end_indices(p)
    ws = {t: {c: v * pos_map.get(t, 1.0) for c, v in w.items()} for t, w in signals.items()}
    r, nav = rrg_engine.portfolio_returns(prices, ws)
    return r, nav

def report(name, r, nav):
    s = rrg_engine.perf_stats(r, nav)
    print(f'{name:<22}{s["total_ret"]:>10.2%}{s["ann_ret"]:>10.2%}{s["maxdd"]:>11.2%}{s["sharpe"]:>8.3f}')
    return (r, nav)

if __name__ == '__main__':
    prices, diffusion, rrg = load()
    signals, p = base_signals(prices, diffusion, rrg)
    print(f'=== 扩散+RRG x 总仓位风控 | {START} ~ {END} ===')
    print(f'{"方案":<22}{"累计":>10}{"年化":>10}{"最大回撤":>11}{"夏普":>8}')

    # A 满仓
    posA = {t: 1.0 for t in signals}
    rA, nA = run(prices, signals, posA)

    # B 趋势过滤
    eq = prices.mean(axis=1)
    ma40 = eq.rolling(40).mean(); ma80 = eq.rolling(80).mean()
    posB = {}
    for t in signals:
        e, m4, m8 = eq.loc[t], ma40.loc[t], ma80.loc[t]
        if pd.notna(m8) and e < m8: posB[t] = 0.0
        elif pd.notna(m4) and e < m4: posB[t] = 0.5
        else: posB[t] = 1.0
    rB, nB = run(prices, signals, posB)

    # C TDA拓扑控仓
    print('  计算TDA仓位(60日窗口 x 30行业, 逐月度)...', flush=True)
    posC = tda_position_series(prices)
    rC, nC = run(prices, signals, posC)

    report('A 满仓(现状)', rA, nA)
    report('B 趋势过滤MA', rB, nB)
    report('C TDA拓扑控仓', rC, nC)

    print('\n--- 2026年至今分段 ---')
    for name, (r, nv) in [('A 满仓', (rA, nA)), ('B 趋势过滤', (rB, nB)), ('C TDA控仓', (rC, nC))]:
        seg = nv[(nv.index >= pd.Timestamp('20260101')) & (nv.index <= pd.Timestamp('20260822'))]
        if len(seg) > 10:
            tot = float(seg.iloc[-1] / seg.iloc[0] - 1)
            mdd = float((seg / seg.cummax() - 1).min())
            print(f'{name:<14}{tot:>9.2%}  回撤 {mdd:>8.2%}')
    # 保存仓位序列供分析
    print('\nTDA仓位月度分布:', pd.Series(posC).describe()[['mean','min','max']].round(3).to_dict())
    pd.Series(posC).to_csv('tda_position_series.csv')